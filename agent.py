import base64
import datetime as _dt
import json
import logging
import os
import sqlite3
import time
import traceback as _tb
from uuid import uuid4
from zoneinfo import ZoneInfo
from typing import Annotated, Required, NotRequired
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import AgentState
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import tools_condition

# The tool registry is the single source of the agent's tool surface.
from tools import registry
import heartbeat_state

# Per-turn telemetry — ContextVars + recorders for turns.jsonl / tool_calls.jsonl.
from observability import telemetry

# ---------------------------------------------------------------------------
# State schema — sliding message window + media blob stripping
# ---------------------------------------------------------------------------
# The default AgentState accumulates messages forever. We override the reducer
# so that after every state update the list is trimmed to the most recent
# MAX_MESSAGES entries. This cap is enforced before the checkpoint is written
# to SQLite, so storage stays bounded regardless of conversation length.
MAX_MESSAGES = 50


def _strip_media_blobs(msg):
    """Replace base64 media content blocks with lightweight text references.

    Only HumanMessages with list content are affected. Rules:
    - image_url blocks with a data: URL → replaced with '[image attached]' text
    - media blocks (audio/video) with a data field → dropped; the text hint
      '[Audio message attached: ...]' / '[Video attached: ...]' that ask_jarvis
      appends as a separate block is preserved as-is.
    All other message types and plain-string content are returned unchanged.
    """
    from langchain_core.messages import HumanMessage
    if not isinstance(msg, HumanMessage) or not isinstance(msg.content, list):
        return msg
    new_content = []
    changed = False
    for block in msg.content:
        if not isinstance(block, dict):
            new_content.append(block)
            continue
        block_type = block.get("type")
        if block_type == "image_url":
            url = (block.get("image_url") or {}).get("url", "")
            if url.startswith("data:"):
                new_content.append({"type": "text", "text": "[image attached]"})
                changed = True
                continue
        elif block_type == "media" and "data" in block:
            # Text hint is a separate block — just drop the blob
            changed = True
            continue
        new_content.append(block)
    if not changed:
        return msg
    return HumanMessage(content=new_content, id=msg.id)


def _add_and_trim(existing: list, new: list) -> list:
    from langchain_core.messages import HumanMessage
    # Strip blobs from existing messages — they have already been seen by the
    # LLM. New messages keep their blobs so the LLM can process them this turn.
    stripped_existing = [_strip_media_blobs(msg) for msg in existing]
    combined = add_messages(stripped_existing, new)[-MAX_MESSAGES:]
    # A raw index slice can land mid-tool-call-sequence, producing orphaned
    # function-call or tool-response messages at the start that the LLM rejects.
    # Advance to the first HumanMessage to guarantee a valid conversation boundary.
    for i, msg in enumerate(combined):
        if isinstance(msg, HumanMessage):
            return combined[i:]
    return combined

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
    scope: NotRequired[str]
    active_skills: NotRequired[Annotated[set[str], _merge_skills]]
    heartbeat_due_tasks: NotRequired[list[str] | None]


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

# Load secrets from the external, secure directory
load_dotenv("/app/secrets/.env")

# Verify Google API key is loaded
if not os.getenv("GOOGLE_API_KEY"):
    raise ValueError("GOOGLE_API_KEY not found. Please check /app/secrets/.env")

# LangGraph owns this path (see CLAUDE.md placement principle); gitignored.
DB_PATH = "/app/jarvis_memory/threads.sqlite"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Initialize the LLM
llm = ChatGoogleGenerativeAI(
    model="gemini-3-flash-preview",
    temperature=0.2, # Lower temperature for more deterministic tool usage
    max_retries=2
)

# ---------------------------------------------------------------------------
# File-driven identity — assembled per turn (hot reload; no startup cache)
# ---------------------------------------------------------------------------
# SOUL.md (user-curated identity) is read from the memory dir. AGENTS.md
# (developer-owned operating rules) is committed code under prompts/ and is
# never in the Jarvis-writable memory surface.
_MEMORY_DIR = "/app/jarvis_memory"
_DAILY_DIR = os.path.join(_MEMORY_DIR, "daily")
_SOUL_PATH = os.path.join(_MEMORY_DIR, "SOUL.md")
_USER_PATH = os.path.join(_MEMORY_DIR, "USER.md")
_HEARTBEAT_MD_PATH = os.path.join(_MEMORY_DIR, "HEARTBEAT.md")
_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
_AGENTS_PATH = os.path.join(_PROMPTS_DIR, "AGENTS.md")
_HEARTBEAT_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "heartbeat.md")

_ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

_USER_FRAMING = (
    "You are in a live conversation with Roi — be direct and proactive, "
    "and reply in your own voice."
)
_HEARTBEAT_FRAMING = (
    "You are a scheduled background tick, not a live chat — be terse, act "
    "only on tasks that are due, and if nothing needs attention reply with "
    "exactly [NO_ACTION] and send nothing."
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


def _load_yesterday_daily_log() -> str:
    """Yesterday's daily log (heartbeat scope). The live chat slice already
    covers today, so yesterday is the cheapest 'context not otherwise in this
    prompt' window. Older days are reachable via read_memory on demand."""
    yesterday = (_dt.datetime.now(_dt.timezone.utc).astimezone(_ISRAEL_TZ)
                 - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    fname = f"daily_{yesterday}.md"
    body = load_or_blank(os.path.join(_DAILY_DIR, fname))
    return f"--- Yesterday's log ({fname}) ---\n{body}" if body else ""


def _load_today_daily_log() -> str:
    """Today's daily log (user scope) — the heartbeat→chat awareness bridge."""
    fname = f"daily_{_today_israel()}.md"
    body = load_or_blank(os.path.join(_DAILY_DIR, fname))
    return f"--- Today's log ({fname}) ---\n{body}" if body else ""


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
    Filters to thread_ids starting with 'telegram_' and timestamps >= start of
    Israel-today. Truncates each message to keep the prompt bounded.
    """
    import json
    chat_log = "/app/jarvis_data/logs/chat_history.jsonl"
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
                    if not tid.startswith("telegram_") or tid == "telegram_test":
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


def _load_recent_heartbeat_notifications(limit: int = 20) -> str:
    """Today's heartbeat-sent notifications (user scope) — gives the live
    assistant visibility into what the background tick already pushed today
    without waiting for the daily log to be rewritten."""
    import json
    notif_log = "/app/jarvis_data/logs/notifications.jsonl"
    if not os.path.exists(notif_log):
        return ""
    since = _today_israel_start_utc()
    records = []
    try:
        with open(notif_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("event") != "heartbeat":
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
        message = r.get("message", "").replace("\n", " ").strip()
        if len(message) > 240:
            message = message[:240] + "..."
        lines.append(f"[{ts_local}] {message}")
    return "--- Heartbeat activity today (Israel time) ---\n" + "\n".join(lines)


def build_system_prompt(
    scope: str,
    active_skills: set[str],
    due_tasks: list[str] | None = None,
) -> str:
    """Assemble the system prompt for one model call, read fresh each call.

    Always: a [Current time]/[Active scope] envelope + SOUL.md (identity,
    memory dir) + AGENTS.md (operating rules, committed under prompts/) +
    USER.md (Roi's profile/preferences, memory dir) + a scope framing line
    + the registry skill block. Scope-specific:
    - user: today's daily log + today's heartbeat-sent notifications (live
      feed; complements the daily log which is rewritten only by the tick).
    - heartbeat: the heartbeat-only rules (prompts/heartbeat.md) + HEARTBEAT.md
      + today's user chat (so the tick can skip tasks Roi has already
      addressed) + yesterday's daily log (older days are reachable via
      read_memory on demand). When ``due_tasks`` is a list, only those task
      blocks of HEARTBEAT.md are injected (non-due blocks collapse to a
      one-line note); None injects the full file.
    All files are read per turn (edits take effect next turn, no restart).
    """
    now = _dt.datetime.now(_dt.timezone.utc).astimezone(_ISRAEL_TZ)
    envelope = (
        f"[Current time: {now.strftime('%A, %Y-%m-%d %H:%M Israel time')}]\n"
        f"[Active scope: {scope}]"
    )
    parts = [
        envelope,
        load_or_blank(_SOUL_PATH),
        load_or_blank(_AGENTS_PATH),
        load_or_blank(_USER_PATH),
    ]

    if scope == "heartbeat":
        parts.append(_HEARTBEAT_FRAMING)
        parts.append(load_or_blank(_HEARTBEAT_PROMPT_PATH))
        hb = load_or_blank(_HEARTBEAT_MD_PATH)
        if hb and due_tasks is not None:
            hb = heartbeat_state.filter_heartbeat_md(hb, due_tasks)
        if hb:
            parts.append(f"--- HEARTBEAT.md ---\n{hb}")
        chat = _load_recent_user_chat()
        if chat:
            parts.append(chat)
        yday = _load_yesterday_daily_log()
        if yday:
            parts.append(yday)
    else:
        parts.append(_USER_FRAMING)
        today = _load_today_daily_log()
        if today:
            parts.append(today)
        hb_notifs = _load_recent_heartbeat_notifications()
        if hb_notifs:
            parts.append(hb_notifs)

    parts.append(registry.compact_skill_list(scope, active_skills))
    return "\n\n".join(p for p in parts if p)


# Initialize the SQLite Checkpointer
# This gives the agent "short-term memory" by tracking conversation history per thread_id
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
memory = PruningSqliteSaver(conn)

# ---------------------------------------------------------------------------
# Agent loop — hand-rolled StateGraph (llm node + tool node + conditional edge)
# ---------------------------------------------------------------------------
# Replaces create_agent so the llm node re-binds its tool set per invocation
# — that is what makes same-turn skill activation work. The loop,
# checkpointer, state schema and reducers are otherwise identical to what
# create_agent provided. Each call binds the scoped tool set
# (core + active skills) and a per-call system prompt.


def _llm_node(state: JarvisState) -> dict:
    """Bind the scoped tool set, prepend the system prompt, call the model.

    Tools = core + the tools of every currently-active skill. Rebound every
    invocation so a skill activated earlier in this same turn is usable on
    the very next model call.

    Telemetry: on success calls record_llm_call (token usage rolls up to the
    turn accumulator). On exception sets acc['error'] and re-raises — the
    single turn-end record is emitted by ask_jarvis's finally, never here.
    """
    scope = state.get("scope", "user")
    active = set(state.get("active_skills", set()))
    due_tasks = state.get("heartbeat_due_tasks")
    bound_llm = llm.bind_tools(registry.get_tools(scope, active))
    try:
        response = bound_llm.invoke(
            [SystemMessage(content=build_system_prompt(scope, active, due_tasks))]
            + state["messages"]
        )
    except Exception as e:
        acc = telemetry.TURN_ACC.get()
        if acc is not None and acc.get("error") is None:
            acc["error"] = f"{type(e).__name__}: {e}"
        raise
    telemetry.record_llm_call(response)
    return {"messages": [response]}


def _tool_node(state: JarvisState) -> dict:
    """Execute the last message's tool calls.

    Every tool returns a plain string except the activate/deactivate
    meta-tools, which return a sentinel dict ``{"_activate": [...],
    "content": str}``. For those, the human-readable ``content`` becomes the
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
    to_deactivate: set[str] = set()

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
            if ns and ns != "core":
                msg = (
                    f"'{name}' belongs to the '{ns}' skill, which is not active. "
                    f"Call activate_skill(namespaces=['{ns}']) first, then call "
                    f"'{name}' again — both in this same turn."
                )
            else:
                msg = f"Tool '{name}' does not exist."
            messages.append(ToolMessage(
                content=msg, name=name, tool_call_id=call_id, status="error",
            ))
            telemetry.record_tool_call(
                tool_name=name, namespace=ns, destructive=destructive,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                status="not_active", args_size=args_size,
                error_str=msg, traceback_str=None,
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
                traceback_str=tb_str,
            )
            continue

        if isinstance(result, dict) and ("_activate" in result or "_deactivate" in result):
            to_activate |= set(result.get("_activate", []) or [])
            to_deactivate |= set(result.get("_deactivate", []) or [])
            messages.append(ToolMessage(
                content=str(result.get("content", "")),
                name=name, tool_call_id=call_id,
            ))
        else:
            messages.append(ToolMessage(
                content=result if isinstance(result, str) else str(result),
                name=name, tool_call_id=call_id,
            ))
        telemetry.record_tool_call(
            tool_name=name, namespace=ns, destructive=destructive,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            status="ok", args_size=args_size,
            error_str=None, traceback_str=None,
        )

    delta: dict = {"messages": messages}
    if to_activate or to_deactivate:
        delta["active_skills"] = (active | to_activate) - to_deactivate
    return delta


def build_graph():
    """Compile the agent graph once at startup."""
    builder = StateGraph(JarvisState)
    builder.add_node("llm", _llm_node)
    builder.add_node("tools", _tool_node)
    builder.add_edge(START, "llm")
    # tools_condition routes to "tools" when the last AI message has tool calls,
    # otherwise to END — the same branch create_agent's prebuilt loop applied.
    builder.add_conditional_edges("llm", tools_condition)
    builder.add_edge("tools", "llm")
    return builder.compile(checkpointer=memory)


agent_executor = build_graph()

def ask_jarvis(
    user_input: str,
    thread_id: str,
    media_attachments: list[dict] | None = None,
    scope: str = "user",
    turn_id: str | None = None,
    heartbeat_due_tasks: list[str] | None = None,
) -> str:
    """
    Encapsulates the agent execution and parses complex LangChain message blocks into a clean string.

    Args:
        user_input: the text message from the user
        thread_id: conversation thread identifier
        media_attachments: optional generic media attachments, e.g.
            [{"kind": "image", "path": "media/image_abc.jpg"}]
        scope: turn scope — "user" (default) or "heartbeat". Carried into
            JarvisState; informational only at this stage (does not gate tools).
        turn_id: optional pre-minted turn identifier (uuid4 hex). If omitted,
            one is generated here. Propagated via TURN_ID ContextVar so the
            nodes and tool calls can stamp it on telemetry records.
        heartbeat_due_tasks: heartbeat scope only — restrict the HEARTBEAT.md
            blocks injected into the system prompt to these task names.
            None injects the full file. Overwritten in state every turn.
    """
    config = {"configurable": {"thread_id": thread_id}}

    # --- Telemetry boundary ----------------------------------------------
    # Snapshot active_skills before the graph runs. Empty for a fresh thread.
    try:
        snap = agent_executor.get_state(config)
        active_start = sorted((snap.values or {}).get("active_skills", set()))
    except Exception:
        active_start = []
    turn_id = turn_id or uuid4().hex
    _tid_token = telemetry.TURN_ID.set(turn_id)
    telemetry.record_turn_start(
        thread_id=thread_id,
        scope=scope,
        active_skills_start=active_start,
        model=getattr(llm, "model", None),
    )

    final_response = ""
    try:
        # Build the message content with text and media.
        if media_attachments:
            content = [{"type": "text", "text": user_input}]

            for attachment in media_attachments:
                try:
                    media_type = str(attachment.get("kind", "")).strip().lower()
                    media_path = str(attachment.get("path", "")).strip()
                    mime_type = str(attachment.get("mime_type", "")).strip()
                    if not media_type or not media_path:
                        continue

                    abs_path = media_path

                    with open(abs_path, "rb") as f:
                        media_data = f.read()

                    if media_type == "image":
                        # Encode image as base64 data URL
                        import mimetypes
                        if not mime_type:
                            mime_type = mimetypes.guess_type(abs_path)[0] or "image/jpeg"
                        b64_data = base64.b64encode(media_data).decode("utf-8")
                        content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64_data}"
                            }
                        })
                    elif media_type == "audio":
                        if not mime_type:
                            mime_type = "audio/ogg"
                        b64_data = base64.b64encode(media_data).decode("utf-8")
                        content.append({
                            "type": "media",
                            "mime_type": mime_type,
                            "data": b64_data,
                        })
                        # Keep a lightweight textual hint for models that ignore raw media blocks.
                        content.append({
                            "type": "text",
                            "text": f"\n[Audio message attached: {media_path}]"
                        })
                    elif media_type == "video":
                        if not mime_type:
                            mime_type = "video/mp4"
                        b64_data = base64.b64encode(media_data).decode("utf-8")
                        content.append({
                            "type": "media",
                            "mime_type": mime_type,
                            "data": b64_data,
                        })
                        # Keep a lightweight textual hint for models that ignore raw media blocks.
                        content.append({
                            "type": "text",
                            "text": f"\n[Video attached: {media_path}]"
                        })
                except Exception as e:
                    logger.warning(f"Failed to load media {media_path}: {e}")
                    content.append({
                        "type": "text",
                        "text": f"\n[Failed to load {media_type}: {media_path}]"
                    })

            # Use HumanMessage to pass structured content to the LLM
            message = HumanMessage(content=content)
            events = agent_executor.stream(
                {"messages": [message], "scope": scope,
                 "heartbeat_due_tasks": heartbeat_due_tasks},
                config,
                stream_mode="values"
            )
        else:
            # Standard text-only message
            events = agent_executor.stream(
                {"messages": [("user", user_input)], "scope": scope,
                 "heartbeat_due_tasks": heartbeat_due_tasks},
                config,
                stream_mode="values"
            )

        for event in events:
            last_message = event["messages"][-1]
            if last_message.type == "ai" and last_message.content:
                content = last_message.content
                # Parse the response, handling both plain strings and complex list blocks
                if isinstance(content, list):
                    final_response = "".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                else:
                    final_response = str(content)
        return final_response
    except Exception as e:
        acc = telemetry.TURN_ACC.get()
        if acc is not None and acc.get("error") is None:
            acc["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        # End-state active_skills + no_action signal — read from the post-run
        # checkpoint snapshot. Falls back to active_start so the record stays
        # consistent even if get_state fails (rare).
        try:
            end_snap = agent_executor.get_state(config)
            active_end = sorted((end_snap.values or {}).get("active_skills", set()))
        except Exception:
            active_end = active_start
        no_action = scope == "heartbeat" and final_response.strip().startswith("[NO_ACTION]")
        telemetry.record_turn_end(active_skills_end=active_end, no_action=no_action)
        telemetry.TURN_ID.reset(_tid_token)


def get_heartbeat_ack(thread_id: str) -> dict | None:
    """The ``heartbeat_respond`` payload from a thread's LAST turn, or None.

    Walks the checkpointed messages after the final HumanMessage (the turn
    boundary) and returns the args of the last heartbeat_respond tool call in
    that slice — a stale ack from an earlier tick is never picked up. Any
    failure degrades to None, never raises.
    """
    try:
        snap = agent_executor.get_state({"configurable": {"thread_id": thread_id}})
        messages = (snap.values or {}).get("messages", [])
    except Exception:
        logger.exception("get_heartbeat_ack: failed to read state for %s", thread_id)
        return None

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
    response = llm.invoke(user_input)
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return content if isinstance(content, str) else str(content)


# Local Testing CLI
if __name__ == "__main__":
    print("Jarvis Agent Core initialized. Type 'quit' to exit.")
    
    # Simulate a user thread for local testing
    thread_id = "local_dev_test_01"
    
    while True:
        user_input = input("\nYou: ")
        if user_input.lower() in ['quit', 'exit', 'q']:
            break
            
        print("Jarvis: ", end="", flush=True)
        # Use our clean wrapper function to get the response
        response = ask_jarvis(user_input, thread_id)
        print(response)