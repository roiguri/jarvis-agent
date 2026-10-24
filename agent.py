import dataclasses
import datetime as _dt
import json
import logging
import os
import sqlite3
import time
import traceback as _tb
from uuid import uuid4
from timeutils import ISRAEL_TZ as _ISRAEL_TZ, owner_tz, owner_tz_name
from typing import Annotated, Required, NotRequired
from dotenv import load_dotenv
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage, convert_to_messages,
)
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import AgentState
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import tools_condition

# Instance paths (JARVIS_ROOT and everything derived from it). First project
# import: it validates the root and creates the state subtrees before any module
# below resolves a path or reads the environment.
import config

# The tool registry is the single source of the agent's tool surface.
from tools import registry
import heartbeat_state
import pending_mirrors
import turn_budget
import turn_context
from gateway.base import OWNER_THREAD_ID

# Per-turn telemetry — ContextVars + recorders for the telemetry store.
from observability import telemetry
from episodes import record as episodes_record, today as episodes_today
from files import model_input

# ---------------------------------------------------------------------------
# State schema — sliding message window + media blob stripping
# ---------------------------------------------------------------------------
# The default AgentState accumulates messages forever. Our reducer
# (_add_and_trim) trims to whole turns fitting in MAX_MESSAGES when a turn
# starts; storage stays bounded by the cap plus up to two turns' traffic.
MAX_MESSAGES = 50

# A user turn that follows this much silence starts with no skills active. The
# prompt cache is gone after about an hour anyway, so the reset costs no cache
# hits, and the model re-activates what the new request needs.
SKILL_IDLE_RESET = _dt.timedelta(hours=3)

def _strip_media_blobs(msg):
    """Drop base64 media blocks from a message the model has already seen.

    Only HumanMessages with list content are affected: inline image_url data
    blocks and media blocks with data are removed. The file's marker,
    `[file:… · kind · …]`, is a separate text block and stays, so history keeps
    the id. All other messages and plain-string content are returned unchanged.
    """
    from langchain_core.messages import HumanMessage
    if not isinstance(msg, HumanMessage) or not isinstance(msg.content, list):
        return msg
    new_content = []
    changed = False
    for block in msg.content:
        if isinstance(block, dict):
            block_type = block.get("type")
            if block_type == "image_url" and (block.get("image_url") or {}).get("url", "").startswith("data:"):
                changed = True
                continue
            if block_type == "media" and "data" in block:
                changed = True
                continue
        new_content.append(block)
    if not changed:
        return msg
    return HumanMessage(content=new_content, id=msg.id)


def _turn_starts(messages: list) -> list[int]:
    """Indices where a turn starts: a HumanMessage not preceded by one (a mirror
    block and the message it precedes are one turn's input)."""
    return [
        i for i, msg in enumerate(messages)
        if isinstance(msg, HumanMessage)
        and (i == 0 or not isinstance(messages[i - 1], HumanMessage))
    ]


def _add_and_trim(existing: list, new: list) -> list:
    # Strip blobs from existing messages — they have already been seen by the
    # LLM. New messages keep their blobs so the LLM can process them this turn.
    stripped_existing = [_strip_media_blobs(msg) for msg in existing]
    combined = add_messages(stripped_existing, new)
    # Only a write carrying user input starts a turn. Within a turn the window
    # only grows: trimming on every write let a turn with ~40 tool calls slice
    # off its own HumanMessage mid-flight, leaving a history the model rejects.
    items = new if isinstance(new, list) else [new]
    if not any(isinstance(m, HumanMessage) for m in convert_to_messages(items)):
        return combined
    # Cut only where a turn starts — a raw index slice can land mid-tool-call
    # sequence, leaving orphaned calls or responses the LLM rejects. Keep the
    # earliest start that fits in the cap, but never drop the previous turn:
    # one long turn would otherwise leave only the new input, and "continue"
    # would see nothing.
    starts = _turn_starts(combined)
    cut = next((i for i in starts if len(combined) - i <= MAX_MESSAGES), starts[-1])
    if len(starts) >= 2:
        cut = min(cut, starts[-2])
    # A compaction record at the head is pinned: the trim never evicts it.
    head = combined[0]
    if cut > 0 and isinstance(head, HumanMessage) and (head.id or "").startswith("compaction-summary"):
        return [head] + combined[cut:]
    return combined[cut:]

def _merge_skills(existing, new):
    """Reducer for active_skills.

    - existing absent/None → empty set
    - no update this turn (new is None) → persist existing
    - update provided → it is the authoritative new set (the tool node will
      compute existing ± delta and return the full set in a later step)

    Coerced to a set so a list/tuple update is accepted.
    """
    base = set(existing) if existing else set()
    if new is None:
        return base
    return set(new)


class JarvisState(AgentState):
    messages: Required[Annotated[list, _add_and_trim]]
    # Added defensively: pre-existing checkpoints predate these fields, so
    # they are NotRequired and every reader uses state.get(..., default).
    # scope: last-write-wins (set once per thread at the call site).
    # active_skills: persisted across turns via _merge_skills; filters the
    # bound tool surface via registry.get_tools(scope, active_skills).
    # heartbeat_due_tasks: which HEARTBEAT.md task blocks to inject this turn
    # (heartbeat scope only). None = all. Overwritten every turn.
    # last_user_turn_at: UTC ISO start of the latest user-scope turn; drives
    # the idle skill reset.
    scope: NotRequired[str]
    active_skills: NotRequired[Annotated[set[str], _merge_skills]]
    heartbeat_due_tasks: NotRequired[list[str] | None]
    last_user_turn_at: NotRequired[str]


# ---------------------------------------------------------------------------
# Checkpointer — single-checkpoint-per-thread pruning
# ---------------------------------------------------------------------------
# SqliteSaver accumulates one row per graph step with no built-in limit.
# We subclass it and extend put() so that immediately after writing a new
# checkpoint, all older rows for that thread are deleted. The writes table
# is cleaned up in the same pass. This keeps exactly 1 checkpoint per thread
# at all times, enforced at the storage layer rather than in application code.

class PruningSqliteSaver(SqliteSaver):
    def put(self, config, checkpoint, metadata, new_versions):
        result = super().put(config, checkpoint, metadata, new_versions)
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        new_id = checkpoint["id"]
        with self.cursor() as cur:
            cur.execute(
                "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id != ?",
                (thread_id, checkpoint_ns, new_id),
            )
            cur.execute(
                "DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id != ?",
                (thread_id, checkpoint_ns, new_id),
            )
        return result

logger = logging.getLogger(__name__)

# Load secrets from this instance's secrets dir (ROOT/secrets/.env).
load_dotenv(config.ENV_FILE)

# Verify Google API key is loaded
if not os.getenv("GOOGLE_API_KEY"):
    raise ValueError("GOOGLE_API_KEY not found. Please check /app/secrets/.env")

# LangGraph owns this path (see CLAUDE.md placement principle); gitignored.
# config creates MEMORY_DIR at import, so no makedirs is needed here.
DB_PATH = os.path.join(config.MEMORY_DIR, "threads.sqlite")

# Initialize the LLM
# One generation is otherwise unbounded: max_retries alone caps attempts, not
# the time any single attempt may take. A malformed-function-call run once spent
# 6m48s and 65k output tokens inside ONE call, which no step budget can catch —
# the graph never advances while it happens. 60s is ~2x the slowest per-call
# average ever logged (31s) and above the p99 duration of a WHOLE turn (37s).
LLM_CALL_TIMEOUT_S = 60
# Floor for a call's timeout once it is capped to the turn's remaining budget:
# a near-zero timeout would only turn "out of time" into an opaque failure.
MIN_CALL_TIMEOUT_S = 15

# A finish reason other than these means the model stopped for its own reason
# (malformed tool call, token ceiling, safety filter) rather than because it was
# done. Such a turn can still end the graph normally with empty content, which
# is how one silently dropped a request while telemetry recorded it as clean.
_NORMAL_FINISH_REASONS = {"STOP", "FINISH_REASON_UNSPECIFIED", ""}

llm = ChatGoogleGenerativeAI(
    model=config.MODEL,
    temperature=0.2, # Lower temperature for more deterministic tool usage
    max_retries=2,
    timeout=LLM_CALL_TIMEOUT_S,
)

# ---------------------------------------------------------------------------
# File-driven identity — assembled per turn (hot reload; no startup cache)
# ---------------------------------------------------------------------------
# SOUL.md (user-curated identity) is read from the memory dir. AGENTS.md
# (developer-owned operating rules) is committed code under prompts/ and is
# never in the Jarvis-writable memory surface.
_MEMORY_DIR = config.MEMORY_DIR
_DAILY_DIR = os.path.join(_MEMORY_DIR, "daily")
_SOUL_PATH = os.path.join(_MEMORY_DIR, "SOUL.md")
_USER_PATH = os.path.join(_MEMORY_DIR, "USER.md")
_MEMORY_INDEX_PATH = os.path.join(_MEMORY_DIR, "MEMORY.md")
_HEARTBEAT_MD_PATH = os.path.join(_MEMORY_DIR, "HEARTBEAT.md")
_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
_AGENTS_PATH = os.path.join(_PROMPTS_DIR, "AGENTS.md")
_HEARTBEAT_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "heartbeat.md")

# Size targets for the injected memory files, in chars. Keeping them under is
# the nightly consolidation's job; writes are never refused for size. The
# prompt includes at most CORE_FILE_CEILING x the target, so a file that grows
# unchecked (consolidation failing for days) cannot swell every call.
CORE_FILE_TARGETS = {"USER.md": 4000, "MEMORY.md": 8000}
CORE_FILE_CEILING = 3


def _core_file(name: str, path: str) -> str:
    """A core memory file for the prompt, cut at its ceiling (with a marker
    and a journal warning) if it has grown far past its target."""
    body = load_or_blank(path)
    limit = CORE_FILE_TARGETS[name] * CORE_FILE_CEILING
    if len(body) > limit:
        logger.warning("%s is %d chars, over its prompt ceiling of %d; cut in the prompt",
                       name, len(body), limit)
        body = body[:limit] + "\n[truncated — consolidation pending]"
    return body


# A due task's notes file goes into the tick's prompt whole up to this size;
# past it, cut with a marker (notes are agent-written and can grow).
NOTES_MAX_CHARS = 8000


def _due_task_notes(due_tasks: list[str] | None) -> list[str]:
    """One prompt section per due task's notes file (heartbeat scope). ``None``
    (the due-gate failed open) shows every task's notes except gated and
    paused ones, matching the task blocks shown."""
    try:
        tasks = heartbeat_state.parse_tasks()
    except Exception:
        logger.exception("couldn't parse HEARTBEAT.md for notes")
        return []
    if due_tasks is None:
        names = {t.name for t in tasks if not t.gate and not t.paused}
    else:
        names = set(due_tasks)
    sections = []
    for task in tasks:
        if task.name not in names:
            continue
        rel = heartbeat_state.notes_path(task)
        body = load_or_blank(os.path.join(_MEMORY_DIR, rel))
        if len(body) > NOTES_MAX_CHARS:
            logger.warning("notes file %s is %d chars; cut in the prompt", rel, len(body))
            body = body[:NOTES_MAX_CHARS] + "\n[truncated]"
        sections.append(f"--- Notes: {task.name} ({rel}) ---\n{body or '(no notes file yet)'}")
    return sections


def _memory_index() -> str:
    body = _core_file("MEMORY.md", _MEMORY_INDEX_PATH)
    return f"--- MEMORY.md (index of memory files) ---\n{body}" if body else ""


_USER_FRAMING = (
    "You are in a live conversation with Roi — be direct and proactive, "
    "and reply in your own voice."
)
_HEARTBEAT_FRAMING = (
    "You are a scheduled background tick, not a live chat — be terse and act "
    "only on tasks that are due."
)
# TODO(#176): when the review applies changes, drop "change nothing" here.
_DREAM_FRAMING = (
    "You are running the morning memory review in the background, not a live "
    "chat. Look things up if you need to, but change nothing; your final answer "
    "is the note Roi receives."
)


def load_or_blank(path: str) -> str:
    """Read a prompt source file, stripped. Returns '' on missing/IO error so
    a transient FS problem degrades the prompt instead of crashing a turn."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _today_israel() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone(_ISRAEL_TZ).strftime("%Y-%m-%d")


def _turn_stamp(now: _dt.datetime) -> str:
    """The turn's time, prefixed to its input once at turn start. Formatted
    from ``now`` and never from the clock, so every call in the turn sees the
    same moment. Away mode (/tz) adds the owner's own clock."""
    stamp = now.astimezone(_ISRAEL_TZ).strftime("%A, %Y-%m-%d %H:%M Israel time")
    away = owner_tz_name()
    if away:
        local = now.astimezone(owner_tz()).strftime("%A, %Y-%m-%d %H:%M")
        stamp += f" | owner local: {local} {away}"
    return f"[{stamp}]"


def _load_yesterday_daily_log() -> str:
    """Yesterday's daily log (heartbeat scope), written the morning after.
    Older days are reachable via read_memory on demand."""
    yesterday = (_dt.datetime.now(_dt.timezone.utc).astimezone(_ISRAEL_TZ)
                 - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    fname = f"daily_{yesterday}.md"
    body = load_or_blank(os.path.join(_DAILY_DIR, fname))
    return f"--- Yesterday's log ({fname}) ---\n{body}" if body else ""


def _today_israel_start_utc() -> _dt.datetime:
    """Start of today (Israel time), expressed in UTC. Used to filter
    activity logs to 'today' regardless of the writer's clock."""
    now = _dt.datetime.now(_dt.timezone.utc).astimezone(_ISRAEL_TZ)
    start_il = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_il.astimezone(_dt.timezone.utc)


def _load_recent_user_chat(limit: int = 60) -> str:
    """Today's user-thread chat (heartbeat scope) — gives the tick visibility
    into what Roi has already discussed today so it can skip already-handled
    tasks instead of duplicating briefings.

    Reads chat_history.jsonl directly to avoid pulling in the tool import path.
    That log is shared by both threads, so this drops the heartbeat's own thread
    (else the tick would read its own briefings back) and test rows, keeping
    today's user-channel messages. Truncates each message to keep the prompt
    bounded.
    """
    import json
    from heartbeat import HEARTBEAT_THREAD_ID
    chat_log = os.path.join(config.DATA_DIR, "logs", "chat_history.jsonl")
    if not os.path.exists(chat_log):
        return ""
    since = _today_israel_start_utc()
    records = []
    try:
        with open(chat_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    tid = rec.get("thread_id", "")
                    if not tid or tid == HEARTBEAT_THREAD_ID or tid.endswith("_test"):
                        continue
                    ts = _dt.datetime.fromisoformat(rec["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_dt.timezone.utc)
                    if ts >= since:
                        records.append(rec)
                except (KeyError, ValueError, json.JSONDecodeError):
                    continue
    except OSError:
        return ""
    if not records:
        return ""
    records = records[-limit:]
    lines = []
    for r in records:
        ts_local = _dt.datetime.fromisoformat(r["ts"]).astimezone(_ISRAEL_TZ).strftime("%H:%M")
        role = r.get("role", "?")
        content = r.get("content", "").replace("\n", " ").strip()
        if len(content) > 240:
            content = content[:240] + "..."
        lines.append(f"[{ts_local}] {role}: {content}")
    return "--- Today's chat with Roi (Israel time) ---\n" + "\n".join(lines)


def build_system_prompt(
    scope: str,
    active_skills: set[str],
    due_tasks: list[str] | None = None,
) -> str:
    """Assemble the system prompt for one model call, read fresh each call.

    Always: a [Current date]/[Active scope] envelope + SOUL.md (identity,
    memory dir) + AGENTS.md (operating rules, committed under prompts/) +
    USER.md (Roi's profile/preferences, memory dir) + a scope framing line
    + the registry skill block. Scope-specific:
    - user: the today view. (Heartbeat sends reach the user thread as
      mirrored history via the pending-mirror drain, not as a prompt slice.)
    - heartbeat: the heartbeat-only rules (prompts/heartbeat.md) + HEARTBEAT.md
      + today's user chat (so the tick can skip tasks Roi has already
      addressed) + the today view + yesterday's daily log (older days are
      reachable via read_memory). When ``due_tasks`` is a list, only those task
      blocks of HEARTBEAT.md are injected (non-due blocks collapse to a
      one-line note); None injects every task except gated ones.
    - dream: the morning memory review's framing line only; its turn input
      carries the instruction and the material.
    All files are read per turn (edits take effect next turn, no restart).
    """
    # Date only: the clock lives in the turn's input stamp (_turn_stamp), so
    # this prompt — rebuilt on every call — stays byte-stable through the day.
    lines = [
        f"[Current date: {_today_israel()}]",
        f"[Active scope: {scope}]",
    ]
    # Away mode (/tz): the stamp carries the owner's clock; this line carries
    # what to do with it. Home has no file and no line.
    away = owner_tz_name()
    if away:
        lines.insert(1, f"[Owner timezone: {away} — times the owner speaks are local to them]")
    # Origin channel — a runtime value (no channel-name literal in this module),
    # inform-only. None on origin-less turns (heartbeat), where the line is skipped.
    channel = turn_context.current_channel()
    if scope == "user" and channel:
        lines.append(f"[Channel: {channel}]")
    envelope = "\n".join(lines)
    parts = [
        envelope,
        load_or_blank(_SOUL_PATH),
        load_or_blank(_AGENTS_PATH),
        _core_file("USER.md", _USER_PATH),
        _memory_index(),
    ]

    if scope == "heartbeat":
        parts.append(_HEARTBEAT_FRAMING)
        parts.append(load_or_blank(_HEARTBEAT_PROMPT_PATH))
        hb = load_or_blank(_HEARTBEAT_MD_PATH)
        if hb:
            hb = heartbeat_state.filter_heartbeat_md(hb, due_tasks)
        if hb:
            parts.append(f"--- HEARTBEAT.md ---\n{hb}")
        parts.extend(_due_task_notes(due_tasks))
        parts.append(episodes_today.background_today(_dt.datetime.now(_dt.timezone.utc)))
        chat = _load_recent_user_chat()
        if chat:
            parts.append(chat)
        parts.append(_load_yesterday_daily_log())
    elif scope == "dream":
        parts.append(_DREAM_FRAMING)
    else:
        parts.append(_USER_FRAMING)
        parts.append(episodes_today.background_today(_dt.datetime.now(_dt.timezone.utc)))


    parts.append(registry.compact_skill_list(scope, active_skills))
    return "\n\n".join(p for p in parts if p)


# Initialize the SQLite Checkpointer
# This gives the agent "short-term memory" by tracking conversation history per thread_id
conn = sqlite3.connect(DB_PATH, check_same_thread=False)


def keep_db_small(conn: sqlite3.Connection = conn) -> None:
    """Each turn replaces its thread's snapshot; without these SQLite keeps the
    freed pages and the grown WAL. Turning auto_vacuum on takes one VACUUM.
    Called by main.py at service start, once logging is set up."""
    conn.execute("PRAGMA journal_size_limit = 4194304")
    try:
        if conn.execute("PRAGMA auto_vacuum").fetchone()[0] != 1:
            conn.execute("PRAGMA auto_vacuum = FULL")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            logger.info("threads.sqlite compacted; auto_vacuum on")
    except sqlite3.OperationalError as e:   # another process holds the file
        logger.warning("threads.sqlite not compacted: %s", e)


memory = PruningSqliteSaver(conn)

# ---------------------------------------------------------------------------
# Agent loop — hand-rolled StateGraph (llm node + tool node + conditional edge)
# ---------------------------------------------------------------------------
# Replaces create_agent so the llm node re-binds its tool set per invocation
# — that is what makes same-turn skill activation work. The loop,
# checkpointer, state schema and reducers are otherwise identical to what
# create_agent provided. Each call binds the scoped tool set
# (core + active skills) and a per-call system prompt.


def _finish_reason(message) -> str:
    """The provider's stop reason for an AI message, '' when it doesn't say.

    Gemini reports it in response_metadata; treat a missing key as normal
    rather than as a failure, so a provider that omits it never turns every
    turn into a false alarm.
    """
    if message is None:
        return ""
    meta = getattr(message, "response_metadata", None) or {}
    return str(meta.get("finish_reason") or "")


def _record_turn_error(detail: str) -> None:
    """Stamp the turn accumulator so the record stops reading as clean.

    Only the first error sticks: the earliest cause is the one that explains
    the turn, and a later symptom must not overwrite it.
    """
    acc = telemetry.TURN_ACC.get()
    if acc is not None and acc.get("error") is None:
        acc["error"] = detail


def _ai_text(message) -> str:
    """The text blocks of an AI message joined; '' for none or no message."""
    if message is None:
        return ""
    content = message.content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


def _message_chars(message) -> int:
    """A message's size in chars: its text plus any tool calls it carries.
    Media blocks are left out — their payload is a blob, not prompt text."""
    content = message.content
    if isinstance(content, str):
        chars = len(content)
    else:
        chars = sum(
            len(block.get("text") or "") if isinstance(block, dict) else len(str(block))
            for block in content or []
        )
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        chars += len(json.dumps(tool_calls, ensure_ascii=False, default=str))
    return chars


_SCHEMA_CHARS: dict[str, int] = {}


def _bound_tools(tools: list) -> list[dict]:
    """One telemetry entry per bound tool, with its declaration's size. The
    declarations are fixed for the life of the process, so sizes are cached."""
    out = []
    for t in tools:
        if t.name not in _SCHEMA_CHARS:
            try:
                _SCHEMA_CHARS[t.name] = len(json.dumps(convert_to_openai_tool(t), ensure_ascii=False))
            except Exception:
                _SCHEMA_CHARS[t.name] = 0
        out.append({"tool": t.name, "namespace": registry.namespace_of(t.name) or "",
                    "schema_chars": _SCHEMA_CHARS[t.name]})
    return out


def _llm_node(state: JarvisState) -> dict:
    """Bind the scoped tool set, prepend the system prompt, call the model.

    Tools = core + the tools of every currently-active skill. Rebound every
    invocation so a skill activated earlier in this same turn is usable on
    the very next model call.

    Telemetry: every call is recorded with its tokens, latency and input
    composition (system prompt, tool declarations, earlier turns, this turn),
    a failed one with its error before the exception propagates to ask_jarvis.
    """
    scope = state.get("scope", "user")
    active = set(state.get("active_skills", set()))
    due_tasks = state.get("heartbeat_due_tasks")
    messages = list(state["messages"])
    starts = _turn_starts(messages)
    split = starts[-1] if starts else 0

    # The turn budget is checked here because every model call passes through
    # this node. Exhaustion makes this the last call — no tools bound, so the
    # reply carries no tool calls and the graph ends through its normal route.
    # Both notices ride on this request only, never the checkpoint: "stop now"
    # text left in history would be imitated by later turns.
    tracker = turn_budget.TRACKER.get()
    tracker.check()
    tools = []
    if tracker.exhausted_by:
        runnable = llm
        messages.append(HumanMessage(content=tracker.policy.exhaustion_ask))
    else:
        tools = registry.get_tools(scope, active)
        runnable = llm.bind_tools(tools)
        if tracker.wrapped_up:
            messages.append(HumanMessage(content=tracker.policy.wrap_up_notice))
    prompt = build_system_prompt(scope, active, due_tasks)
    try:
        composition = {
            "prompt": prompt,
            "scope": scope,
            "bound": _bound_tools(tools),
            "history_chars": sum(_message_chars(m) for m in messages[:split]),
            "turn_chars": sum(_message_chars(m) for m in messages[split:]),
            "history_messages": split,
            "turn_messages": len(messages) - split,
        }
    except Exception as e:
        # A measuring bug must not stop the turn; the row and /usage show it.
        logger.exception("Telemetry: measuring the call's input failed")
        composition = {"telemetry_error": f"{type(e).__name__}: {e}"}
    started_at = _dt.datetime.now(_dt.timezone.utc)
    t0 = time.perf_counter()
    try:
        response = runnable.invoke(
            [SystemMessage(content=prompt)] + messages,
            timeout=max(MIN_CALL_TIMEOUT_S, min(tracker.policy.budget.call_timeout_s or LLM_CALL_TIMEOUT_S,
                                                 tracker.remaining_s())),
        )
    except Exception as e:
        telemetry.record_llm_call(
            None, started_at=started_at, latency_ms=int((time.perf_counter() - t0) * 1000),
            error=f"{type(e).__name__}: {e}", **composition,
        )
        raise
    tracker.record_call(response)
    telemetry.record_llm_call(
        response, started_at=started_at, latency_ms=int((time.perf_counter() - t0) * 1000),
        finish_reason=_finish_reason(response), **composition,
    )
    return {"messages": [response]}


def _tool_node(state: JarvisState) -> dict:
    """Execute the last message's tool calls.

    Every tool returns a plain string except the activate_skill meta-tool,
    which returns a sentinel dict ``{"_activate": [...], "content": str}``. For those, the human-readable ``content`` becomes the
    ToolMessage and the namespaces drive an ``active_skills`` state update;
    all other tools behave exactly as the prebuilt ToolNode did (one
    ToolMessage per call, exceptions captured as an error ToolMessage so the
    model can recover rather than crashing the graph).

    Activation takes effect the same turn: _llm_node re-binds
    registry.get_tools(scope, active_skills) on its next invocation.
    """
    scope = state.get("scope", "user")
    active = set(state.get("active_skills", set()))
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None) or []

    messages: list[ToolMessage] = []
    to_activate: set[str] = set()

    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}
        call_id = tc.get("id")
        ns = registry.namespace_of(name) or ""
        destructive = registry.is_destructive(name)
        try:
            args_size = len(json.dumps(args, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            args_size = -1
        t0 = time.perf_counter()
        tool = registry.find(name, scope, active)
        if tool is None:
            # A call to an inactive skill's tool does not run: the skill's rules
            # must be in the prompt first. The refusal activates the skill, so
            # the model's next call sees the rules and can repeat the call.
            if ns and ns != "core" and registry.find(name, scope, active | {ns}) is not None:
                to_activate.add(ns)
                msg = (
                    f"'{name}' belongs to the '{ns}' skill, which was not active, so it did not run. "
                    f"The skill is active now and its rules are in your instructions; "
                    f"read them, then call '{name}' again."
                )
            elif ns and ns != "core":
                msg = f"Tool '{name}' is not available in this turn."
            else:
                msg = f"Tool '{name}' does not exist."
            messages.append(ToolMessage(
                content=msg, name=name, tool_call_id=call_id, status="error",
            ))
            telemetry.record_tool_call(
                tool_name=name, namespace=ns, destructive=destructive,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                status="not_active", args_size=args_size,
                error_str=msg, traceback_str=None, result_size=len(msg),
            )
            continue
        try:
            result = tool.invoke(args)
        except Exception as e:  # parity with ToolNode handle_tool_errors=True
            tb_str = _tb.format_exc()
            messages.append(ToolMessage(
                content=f"Error: {e}",
                name=name, tool_call_id=call_id, status="error",
            ))
            telemetry.record_tool_call(
                tool_name=name, namespace=ns, destructive=destructive,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                status="error", args_size=args_size,
                error_str=f"{type(e).__name__}: {e}",
                traceback_str=tb_str, result_size=len(messages[-1].content),
            )
            continue

        if isinstance(result, dict) and "_activate" in result:
            to_activate |= set(result.get("_activate", []) or [])
            messages.append(ToolMessage(
                content=str(result.get("content", "")),
                name=name, tool_call_id=call_id,
            ))
        else:
            messages.append(ToolMessage(
                content=result if isinstance(result, str) else str(result),
                name=name, tool_call_id=call_id,
            ))
            turn_budget.TRACKER.get().committed[name] += 1
        telemetry.record_tool_call(
            tool_name=name, namespace=ns, destructive=destructive,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            status="ok", args_size=args_size,
            error_str=None, traceback_str=None,
            result_size=len(messages[-1].content),
        )

    delta: dict = {"messages": messages}
    if to_activate:
        delta["active_skills"] = active | to_activate
    return delta


def build_graph(checkpointer=None):
    """Compile the agent graph once at startup. With a checkpointer, a thread
    keeps its conversation across turns; without one, every run starts fresh."""
    builder = StateGraph(JarvisState)
    builder.add_node("llm", _llm_node)
    builder.add_node("tools", _tool_node)
    builder.add_edge(START, "llm")
    # tools_condition routes to "tools" when the last AI message has tool calls,
    # otherwise to END — the same branch create_agent's prebuilt loop applied.
    builder.add_conditional_edges("llm", tools_condition)
    builder.add_edge("tools", "llm")
    return builder.compile(checkpointer=checkpointer)


agent_executor = build_graph(memory)
# Heartbeat ticks and wakes: no saved conversation, each run starts fresh. What
# a tick needs from before comes from its prompt (notes files, the today view,
# today's chat) and the episode store, not from replayed earlier ticks.
stateless_executor = build_graph()

def ask_jarvis(
    user_input: str,
    thread_id: str,
    files: list | None = None,
    scope: str = "user",
    turn_id: str | None = None,
    heartbeat_due_tasks: list[str] | None = None,
    channel: str | None = None,
    trigger=None,
    input_role: str | None = None,
    initial_skills: set[str] | None = None,
) -> turn_budget.TurnOutcome:
    """
    Run one agent turn and classify how it ended.

    Never raises for a failed turn: an exception becomes a ``failed`` outcome
    whose text names the cause and the tool calls that already committed, and
    the same note is written into the thread so the next turn knows too.
    Callers decide only where ``outcome.text`` goes.

    Args:
        user_input: the text message from the user
        thread_id: conversation thread identifier
        files: optional file-store records (files.store.FileRecord) this message
            brought; each is shown to the model with its marker.
        scope: turn scope — "user" (default), "heartbeat" or "dream". Carried into
            JarvisState; informational only at this stage (does not gate tools).
        turn_id: optional pre-minted turn identifier (uuid4 hex). If omitted,
            one is generated here. Propagated via TURN_ID ContextVar so the
            nodes and tool calls can stamp it on telemetry records.
        heartbeat_due_tasks: heartbeat scope only — restrict the HEARTBEAT.md
            blocks injected into the system prompt to these task names.
            None injects every task except gated ones. Overwritten in state every turn.
        channel: origin channel name (router-stamped), or None for
            origin-less turns. Published via CURRENT_CHANNEL.
        trigger: the trigger whose firing started this turn (a scheduled
            wake), or None. Published via CURRENT_TRIGGER.
        input_role: how the episode store records this turn's input: "owner"
            (default in user scope) or "system" (default in heartbeat scope;
            also a confirmation result fed back as a turn).
        initial_skills: heartbeat scope — the skills the turn starts with (the
            due tasks' warm start). Heartbeat turns keep no conversation: they
            run on ``stateless_executor`` and start from their input alone.
    """
    stateless = scope in ("heartbeat", "dream")
    executor = stateless_executor if stateless else agent_executor
    now = _dt.datetime.now(_dt.timezone.utc)
    raw_input = user_input
    user_input = f"{_turn_stamp(now)} {user_input}"
    tracker = turn_budget.TurnTracker(turn_budget.POLICIES.get(scope, turn_budget.POLICIES["user"]))
    config = {
        "configurable": {"thread_id": thread_id},
        # A backstop just above what the budget allows (two super-steps per LLM
        # call): the budget ends turns, so reaching this means a bug in it, and
        # it surfaces as a failed turn.
        "recursion_limit": 2 * tracker.policy.budget.max_llm_calls + 4,
    }

    # --- Telemetry boundary ----------------------------------------------
    # Snapshot active_skills before the graph runs. Empty for a fresh thread.
    state_update: dict = {}
    try:
        if stateless:
            active_start = sorted(initial_skills or [])
            if active_start:
                state_update["active_skills"] = set(active_start)
            snap = None
        else:
            snap = agent_executor.get_state(config)
            active_start = sorted((snap.values or {}).get("active_skills", set()))
        if scope == "user" and snap is not None:
            state_update["last_user_turn_at"] = now.isoformat()
            if active_start and _idle_long_enough((snap.values or {}).get("last_user_turn_at"), now):
                logger.info("Skills reset after idle on %s: %s", thread_id, active_start)
                state_update["active_skills"] = set()
                active_start = []
    except Exception:
        active_start = []
    turn_id = turn_id or uuid4().hex
    _tid_token = telemetry.TURN_ID.set(turn_id)
    _scope_token = turn_context.CURRENT_SCOPE.set(scope)
    _thread_token = turn_context.CURRENT_THREAD_ID.set(thread_id)
    _channel_token = turn_context.CURRENT_CHANNEL.set(channel)
    _trigger_token = turn_context.CURRENT_TRIGGER.set(trigger)
    _tracker_token = turn_budget.TRACKER.set(tracker)
    telemetry.record_turn_start(
        thread_id=thread_id,
        scope=scope,
        active_skills_start=active_start,
        model=getattr(llm, "model", None),
        channel=channel,
        trigger_id=getattr(trigger, "id", None),
        due_tasks=heartbeat_due_tasks if scope == "heartbeat" else None,
    )
    episode_source = ("chat" if scope == "user" else "dream" if scope == "dream"
                      else "wake" if trigger is not None else "heartbeat")
    episodes_record.turn_input(
        turn_id=turn_id, thread_id=thread_id, scope=scope, channel=channel,
        role=input_role or ("owner" if scope == "user" else "system"),
        source=episode_source, content=raw_input, ts=now.isoformat(),
        attachments=[f.marker() for f in files or []],
        meta={"trigger": trigger.id, "task": getattr(trigger.action, "task", None)} if trigger is not None else None,
    )

    mirror_block = mirror_cursor = None
    if scope == "user" and thread_id == OWNER_THREAD_ID:
        mirror_block, mirror_cursor = pending_mirrors.drain_pending()

    final_response = ""
    last_values: dict | None = None
    outcome: turn_budget.TurnOutcome | None = None
    # The first streamed event means the input (mirror block included) is in
    # the checkpoint — from then on the drained mirrors are part of the thread
    # whether or not the turn finishes, so the cursor must advance.
    input_checkpointed = False
    try:
        if files:
            content = [{"type": "text", "text": user_input}, *model_input.content_blocks(files)]
            input_messages = [HumanMessage(content=content)]
        else:
            input_messages = [("user", user_input)]
        if mirror_block:
            input_messages.insert(0, ("user", mirror_block))
        events = executor.stream(
            {"messages": input_messages, "scope": scope,
             "heartbeat_due_tasks": heartbeat_due_tasks, **state_update},
            config,
            stream_mode="values"
        )

        last_ai = None
        for event in events:
            input_checkpointed = True
            last_values = event
            last_message = event["messages"][-1]
            if last_message.type == "ai":
                last_ai = last_message
                if last_message.content:
                    final_response = _ai_text(last_message)
        if tracker.exhausted_by:
            outcome = _exhausted_outcome(tracker, turn_id, _ai_text(last_ai))
        else:
            reason = _finish_reason(last_ai)
            if not final_response.strip() and reason not in _NORMAL_FINISH_REASONS:
                # The graph ended normally but the model stopped for its own
                # reason and produced no text. Left alone this collapses to ""
                # and is reported as a clean turn, which is how a request was
                # once dropped in silence.
                logger.warning(
                    "Turn %s ended with finish_reason=%s and no text.", turn_id, reason
                )
                _record_turn_error(f"finish_reason: {reason}")
                outcome = _failed_outcome(
                    config, tracker,
                    f"the model stopped mid-response without an answer (it reported `{reason}`)",
                    suffix=" Rephrasing or splitting the request usually gets past it.",
                    stateless=stateless,
                )
            elif tracker.wrapped_up:
                outcome = turn_budget.TurnOutcome(
                    turn_budget.WRAPPED_UP,
                    f"{final_response}\n\n{turn_budget.WRAPPED_UP_LINE}".strip(),
                    committed_calls=tracker.committed_calls(),
                )
            else:
                outcome = turn_budget.TurnOutcome(
                    turn_budget.COMPLETED, final_response,
                    committed_calls=tracker.committed_calls(),
                )
    except Exception as e:
        logger.exception("Turn %s failed", turn_id)
        _record_turn_error(f"{type(e).__name__}: {e}")
        if tracker.exhausted_by:
            # The tool-free last call itself failed; the turn still ended on
            # its budget, so it keeps that outcome and its "Stopped early" line.
            outcome = _exhausted_outcome(tracker, turn_id, "")
        else:
            outcome = _failed_outcome(
                config, tracker, turn_budget.describe_error(e),
                unsaved_input=None if input_checkpointed else user_input,
                stateless=stateless,
            )
    finally:
        if mirror_cursor and input_checkpointed:
            pending_mirrors.advance_cursor(mirror_cursor)
        acc = telemetry.TURN_ACC.get()
        if acc is not None:
            acc["outcome"] = outcome.kind if outcome else turn_budget.FAILED
            acc["budget"] = tracker.record()
        # End-state active_skills + no_action signal — read from the post-run
        # checkpoint snapshot, or for a stateless turn from its last streamed
        # state. Falls back to active_start so the record stays consistent even
        # if get_state fails (rare).
        try:
            if stateless:
                end_values = last_values or {}
            else:
                end_values = agent_executor.get_state(config).values or {}
            active_end = sorted(end_values.get("active_skills", set()))
            end_messages = end_values.get("messages", [])
        except Exception:
            active_end = active_start
            end_messages = []
        # no_action mirrors delivery: a tick delivers only when its ack says
        # notify, so a missing ack counts as a no-op.
        no_action = False
        ack = None
        if scope == "heartbeat":
            ack = _ack_from_messages(end_messages)
            no_action = ack is None or not bool(ack.get("notify"))
            if outcome is None or not outcome.finished:
                episodes_record.turn_failure(
                    turn_id=turn_id, thread_id=thread_id, scope=scope, source=episode_source,
                    cause=(outcome.cause if outcome else None) or "unknown", ts=now.isoformat(),
                )
        if outcome is not None:
            outcome = dataclasses.replace(outcome, ack=ack, active_skills=tuple(active_end))
        turn_slice = _this_turns_messages(end_messages, user_input)
        if turn_slice:
            episodes_record.turn_steps(turn_id=turn_id, thread_id=thread_id, scope=scope,
                                       channel=channel, source=episode_source, messages=turn_slice)
        telemetry.record_turn_end(active_skills_end=active_end, no_action=no_action)
        telemetry.TURN_ID.reset(_tid_token)
        turn_context.CURRENT_SCOPE.reset(_scope_token)
        turn_context.CURRENT_THREAD_ID.reset(_thread_token)
        turn_context.CURRENT_CHANNEL.reset(_channel_token)
        turn_context.CURRENT_TRIGGER.reset(_trigger_token)
        turn_budget.TRACKER.reset(_tracker_token)
    return outcome


def _this_turns_messages(messages: list, stamped_input: str) -> list:
    """The thread's messages from this turn's input on, or [] when this turn's
    input never reached the thread (the slice would be the previous turn's)."""
    starts = _turn_starts(messages)
    if not starts:
        return []
    turn = messages[starts[-1]:]
    for m in turn:
        if not isinstance(m, HumanMessage):
            break
        content = m.content
        text = content if isinstance(content, str) else "".join(
            b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text")
        if text.startswith(stamped_input):
            return turn
    return []


def _idle_long_enough(last_turn_at: str | None, now: _dt.datetime) -> bool:
    """Whether SKILL_IDLE_RESET has passed since the last user turn. A thread
    with no recorded last turn counts as idle."""
    if not last_turn_at:
        return True
    return now - _dt.datetime.fromisoformat(last_turn_at) >= SKILL_IDLE_RESET


def _exhausted_outcome(tracker, turn_id: str, answer: str) -> turn_budget.TurnOutcome:
    """A ceiling, not an error to hand back raw: the turn did real work on the
    way up, and its last call answered without tools (or failed trying)."""
    cause = turn_budget.EXHAUSTED_CAUSE[tracker.exhausted_by]
    logger.error("Turn %s exhausted its %s budget.", turn_id, tracker.exhausted_by)
    _record_turn_error(f"budget exhausted: {tracker.exhausted_by}")
    calls = tracker.committed_calls()
    text = answer.strip() or (
        f"I stopped before finishing: {cause}. {turn_budget.committed_sentence(calls)}"
    )
    return turn_budget.TurnOutcome(
        turn_budget.BUDGET_EXHAUSTED,
        f"{text}\n\n{turn_budget.stopped_early_line(cause)}",
        cause=cause,
        committed_calls=calls,
    )


def _failed_outcome(
    config: dict, tracker, cause: str, suffix: str = "", unsaved_input: str | None = None,
    stateless: bool = False,
) -> turn_budget.TurnOutcome:
    """A ``failed`` outcome, with its note written into the thread.

    Appended as the llm node's output: a failed turn can only end on a
    ToolMessage or its HumanMessage (the tool node never raises — its telemetry
    writes are guarded), so an AIMessage after either keeps the history valid,
    and with no tool calls the graph routes to END — the next turn starts clean.

    ``unsaved_input`` is the turn's text when it failed before its input reached
    the checkpoint. It is written ahead of the note so the turn boundary moves:
    otherwise the thread's last HumanMessage is the previous turn's.

    A ``stateless`` turn has no thread to write into; its failure is recorded
    in the episode store instead, where the today view shows it.
    """
    calls = tracker.committed_calls()
    text = turn_budget.failure_text(cause, calls) + suffix
    if stateless:
        return turn_budget.TurnOutcome(turn_budget.FAILED, text, cause=cause, committed_calls=calls)
    written = [HumanMessage(content=unsaved_input)] if unsaved_input is not None else []
    written.append(AIMessage(content=turn_budget.failure_note(cause, calls)))
    try:
        agent_executor.update_state(config, {"messages": written}, as_node="llm")
    except Exception:
        logger.exception("Could not write the failure note into the thread")
    return turn_budget.TurnOutcome(turn_budget.FAILED, text, cause=cause, committed_calls=calls)


def _ack_from_messages(messages) -> dict | None:
    """The ``heartbeat_respond`` payload from the last turn in ``messages``.

    Walks the messages after the final HumanMessage (the turn boundary) and
    returns the args of the last heartbeat_respond tool call in that slice —
    a stale ack from an earlier tick is never picked up. None if absent.
    """
    last_human = None
    for i, m in enumerate(messages):
        if isinstance(m, HumanMessage):
            last_human = i
    if last_human is None:
        return None

    ack = None
    for m in messages[last_human + 1:]:
        for tc in getattr(m, "tool_calls", None) or []:
            if tc.get("name") == "heartbeat_respond":
                ack = tc.get("args") or {}
    return ack


def ask_jarvis_once(user_input: str) -> str:
    """Single-turn LLM call — no tools, no memory, no agent loop.
    Use for simple generation tasks like formatting a notification message.

    Model content may be a plain string or a list of content blocks
    (Gemini). Flatten to a string so callers always get the documented type.
    """
    return _ai_text(llm.invoke(user_input))