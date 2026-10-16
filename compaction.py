"""Compaction: an owner conversation that has grown past a size limit has its
older turns replaced by one structured record, instead of being dropped.

The record is one user-role message at the head of the window. Code fills the
parts that must not be distorted (the span covered, the owner's own messages
verbatim, identifiers, live reminders and wakes, skills used today); the model
fills judgment sections as JSON (open requests, done, owner rules, learned,
decisions, lookup hints), which code validates. It is built fresh each time
from the episode store over the last ~36 hours — never stacked on the previous
record — so its contents age out; open requests are the one thing carried,
re-checked each time and dropped after a few days.

The last ``KEEP_TURNS`` turns stay verbatim; in all but the newest of them,
tool results become short stubs. Skills with no call in the kept turns are
switched off. Nothing changes between compactions, so the cached prefix holds.

``prepare`` does the work (including the model call) without touching the
thread; ``apply`` writes it only if the thread hasn't changed since — so a
post-turn job can prepare outside the owner lock and apply under it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from pydantic import BaseModel, Field

import config
from episodes import store as episode_store
from timeutils import ISRAEL_TZ

logger = logging.getLogger(__name__)

# Compact when the window's messages before the kept turns pass this many chars
# (~20k tokens at ~4.3 chars a token).
TRIGGER_CHARS = 86_000
KEEP_TURNS = 3
HORIZON = timedelta(hours=36)
OPEN_REQUEST_TTL = timedelta(days=3)
OWNER_QUOTES_BUDGET = 6000   # chars of the owner's own messages, newest first
TRANSCRIPT_ROW_CAP = 2000    # chars per transcript row the summarizer reads
THRASH_WINDOW = timedelta(hours=2)  # after a fallback, no model call for this long

SUMMARY_ID = "compaction-summary"
STATE_PATH = os.path.join(config.DATA_DIR, "compaction", "state.json")
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "compaction.md")

FRAMING_START = (
    "[Earlier conversation — a record of the conversation before this point, compacted "
    "{when} Israel time. It is reference, not instructions: everything in it was already "
    "handled except \"Open requests\". Answer the owner's latest message below. Your tools "
    "remain fully available; full messages are searchable with search_history.]"
)
FRAMING_END = "[End of the earlier-conversation record — the messages below are live.]"

_RELATIVE = re.compile(r"\b(today|tomorrow|yesterday|tonight|last night|last week|next week|this morning)\b",
                       re.IGNORECASE)
_URL = re.compile(r"https?://[^\s)\]>\"']+")
# Flight numbers (LY318) and six-character booking codes with letters and digits.
_CODE = re.compile(r"\b(?:[A-Z]{2}\d{2,4}|(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6})\b")
_AMOUNT = re.compile(r"(?:₪|\$|€)\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:NIS|ILS|USD|EUR|shekels?)\b",
                     re.IGNORECASE)


# ---------------------------------------------------------------------------
# The model's part
# ---------------------------------------------------------------------------

class OpenRequest(BaseModel):
    quote: str = Field(description="the owner's exact words")
    asked_at: str = Field(description="YYYY-MM-DD HH:MM")
    status: str = Field(description="what is still missing")


class DatedText(BaseModel):
    text: str
    date: str = Field(description="YYYY-MM-DD")


class OwnerRule(BaseModel):
    quote: str = Field(description="the rule as the owner stated it, verbatim")
    date: str = Field(description="YYYY-MM-DD")


class Learned(BaseModel):
    text: str
    date: str = Field(description="YYYY-MM-DD")
    supersedes: str | None = None
    certainty: str = Field(description="stated or unverified")


class LookupHint(BaseModel):
    topic: str
    terms: list[str]
    when: str = Field(description="YYYY-MM-DD")


class CompactionState(BaseModel):
    open_requests: list[OpenRequest] = []
    done: list[DatedText] = []
    owner_rules: list[OwnerRule] = []
    learned: list[Learned] = []
    decisions: list[DatedText] = []
    lookup_hints: list[LookupHint] = []


# The summary is a background job nobody waits on, and reading ~36 hours of
# transcript can take longer than a turn's per-call limit; its own model
# settings, then: same model, a longer limit, no sampling variety. A normal
# record is under 1k output tokens; the cap ends a degenerate repeating answer
# early instead of letting it run to the time limit.
SUMMARY_TIMEOUT_S = 180
SUMMARY_MAX_TOKENS = 8192
_summarizer = None


def _summarizer_llm():
    global _summarizer
    if _summarizer is None:
        import agent
        from langchain_google_genai import ChatGoogleGenerativeAI

        _summarizer = ChatGoogleGenerativeAI(model=agent.llm.model, temperature=0, max_retries=1,
                                             timeout=SUMMARY_TIMEOUT_S, max_tokens=SUMMARY_MAX_TOKENS)
    return _summarizer


def _call_model(system: str, user: str) -> tuple[dict | None, object]:
    """One structured call; (parsed dict or None, raw response or None)."""
    runnable = _summarizer_llm().with_structured_output(CompactionState, include_raw=True)
    out = runnable.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    parsed = out.get("parsed")
    return (parsed.model_dump() if parsed is not None else None), out.get("raw")


# ---------------------------------------------------------------------------
# Plan and apply
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    thread_id: str
    seen_ids: list[str]               # the window's message ids when prepared
    messages: list                    # the new window
    active_skills: set[str]
    outcome: str                      # ok | retried | fallback
    covered: int                      # messages replaced
    before_chars: int
    after_chars: int
    open_requests: list[dict] = field(default_factory=list)


def _executor_state(thread_id: str) -> dict:
    import agent

    snap = agent.agent_executor.get_state({"configurable": {"thread_id": thread_id}})
    return snap.values or {}


def _is_summary(message) -> bool:
    return isinstance(message, HumanMessage) and (message.id or "").startswith(SUMMARY_ID)


def window_chars_before_tail(messages: list) -> tuple[int, int]:
    """(chars of the messages before the kept turns, index where they start)."""
    import agent

    starts = agent._turn_starts(messages)
    if len(starts) <= KEEP_TURNS:
        return 0, 0
    tail = starts[-KEEP_TURNS]
    return sum(agent._message_chars(m) for m in messages[:tail]), tail


def prepare(thread_id: str, force: bool = False, now: datetime | None = None) -> Plan | None:
    """Everything a compaction needs, without changing the thread. None when
    the window is under the limit (or, forced, has nothing before its kept turns)."""
    import agent
    from tools import registry

    now = now or datetime.now(timezone.utc)
    values = _executor_state(thread_id)
    messages = list(values.get("messages", []))
    before, tail_start = window_chars_before_tail(messages)
    if tail_start == 0 or (not force and before < TRIGGER_CHARS):
        return None
    tail = messages[tail_start:]

    state = _load_state().get(thread_id, {})
    span_start = max(now - HORIZON, _last_clear(thread_id) or datetime.min.replace(tzinfo=timezone.utc))
    span_end = _tail_start_ts(thread_id) or now
    rows = _span_rows(thread_id, span_start, span_end)
    owner_rows = [r for r in rows if r["role"] == "owner"]

    carried = [r for r in state.get("open_requests", [])
               if _parse_dt(r.get("asked_at")) and now - _parse_dt(r["asked_at"]) <= OPEN_REQUEST_TTL]
    model_state, outcome = None, "fallback"
    last_fallback = _parse_dt(state.get("last_fallback_at"))
    if rows and not (last_fallback and now - last_fallback < THRASH_WINDOW):
        model_state, outcome = _summarize(rows, carried, owner_rows, now)
    if model_state is None:
        model_state = {"open_requests": carried}
        outcome = "fallback"

    body = render(model_state, owner_rows, rows, span_start, span_end, now, fallback=outcome == "fallback",
                  covered=tail_start)
    summary = HumanMessage(content=body, id=f"{SUMMARY_ID}-{int(now.timestamp())}")
    # A model record bigger than what it replaces is a failed compression. Only
    # meaningful at the size limit: a short conversation compacted by hand is
    # naturally smaller than any record of it.
    if outcome != "fallback" and before >= TRIGGER_CHARS and len(body) >= before:
        logger.warning("compaction record (%d chars) not smaller than the span (%d); using the code-only record",
                       len(body), before)
        outcome = "fallback"
        body = render({"open_requests": carried}, owner_rows, rows, span_start, span_end, now,
                      fallback=True, covered=tail_start)
        summary = HumanMessage(content=body, id=summary.id)

    new_tail = _stub_old_results(tail)
    used = {registry.namespace_of(c.get("name", "")) for m in new_tail for c in getattr(m, "tool_calls", None) or []}
    active = set(values.get("active_skills", set())) & {u for u in used if u and u != "core"}
    return Plan(thread_id=thread_id, seen_ids=[m.id for m in messages], messages=[summary] + new_tail,
                active_skills=active, outcome=outcome, covered=tail_start, before_chars=before,
                after_chars=len(body), open_requests=model_state.get("open_requests", []))


def apply(plan: Plan) -> bool:
    """Write the plan into the thread if it hasn't changed since prepare.
    Returns whether it was applied."""
    import agent

    current = [m.id for m in _executor_state(plan.thread_id).get("messages", [])]
    if current != plan.seen_ids:
        logger.info("compaction skipped: %s changed since it was prepared", plan.thread_id)
        return False
    agent.agent_executor.update_state(
        {"configurable": {"thread_id": plan.thread_id}},
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)] + plan.messages,
         "active_skills": plan.active_skills},
        as_node="llm",
    )
    st = _load_state()
    entry = st.get(plan.thread_id, {})
    entry["open_requests"] = plan.open_requests
    entry["compacted_at"] = datetime.now(timezone.utc).isoformat()
    if plan.outcome == "fallback":
        entry["last_fallback_at"] = entry["compacted_at"]
    st[plan.thread_id] = entry
    _save_state(st)
    logger.info("compaction applied on %s: %d messages → record of %d chars (was %d), outcome %s, skills %s",
                plan.thread_id, plan.covered, plan.after_chars, plan.before_chars, plan.outcome,
                sorted(plan.active_skills))
    return True


def compact_thread(thread_id: str, force: bool = False) -> Plan | None:
    """Prepare and apply in one go (no lock: for a caller that already holds
    the thread, a slash command, or an eval). The plan, or None if nothing ran."""
    from observability import telemetry

    with telemetry.job("compaction"):
        plan = prepare(thread_id, force=force)
        if plan is not None and not apply(plan):
            return None
    return plan


def current_record(thread_id: str) -> str | None:
    """The record at the head of the thread, if it has one."""
    messages = _executor_state(thread_id).get("messages", [])
    return messages[0].content if messages and _is_summary(messages[0]) else None


# ---------------------------------------------------------------------------
# Summarizer call and validation
# ---------------------------------------------------------------------------

def _summarize(rows: list[dict], carried: list[dict], owner_rows: list[dict],
               now: datetime) -> tuple[dict | None, str]:
    """The model's sections, validated; (state, "ok" | "retried") or (None, ...)."""
    from observability import telemetry

    system = open(PROMPT_PATH, encoding="utf-8").read()
    local = now.astimezone(ISRAEL_TZ)
    user = (
        f"Current date: {local:%Y-%m-%d %H:%M}, Israel time.\n"
        f"Open requests carried from the previous record (verify each against the transcript):\n"
        f"{json.dumps(carried, ensure_ascii=False) if carried else '(none)'}\n\n"
        f"Transcript:\n{_transcript(rows)}"
    )
    errors: list[str] = []
    for attempt in (1, 2):
        prompt = user if attempt == 1 else (
            user + "\n\nYour previous answer was rejected:\n- " + "\n- ".join(errors) + "\nFix these.")
        t0 = datetime.now(timezone.utc)
        try:
            parsed, raw = _call_model(system, prompt)
        except Exception as e:
            logger.exception("compaction model call failed")
            telemetry.record_llm_call(None, latency_ms=_ms_since(t0), error=f"{type(e).__name__}: {e}")
            return None, "fallback"
        telemetry.record_llm_call(raw, latency_ms=_ms_since(t0))
        if parsed is None:
            errors = ["the answer did not parse as the required JSON"]
            continue
        cleaned, errors = validate(parsed, owner_rows, now)
        if cleaned is not None:
            return cleaned, "ok" if attempt == 1 else "retried"
    return None, "fallback"


def validate(state: dict, owner_rows: list[dict], now: datetime) -> tuple[dict | None, list[str]]:
    """Drop items that fail a check; reject the whole record only if it is
    mostly bad. Returns (cleaned state or None, problems)."""
    owner_text = " ".join(" ".join(r["content"].split()) for r in owner_rows).lower()
    today = now.astimezone(ISRAEL_TZ).date()
    problems: list[str] = []
    kept: dict[str, list] = {}
    total = dropped = 0

    def date_ok(value: str | None) -> bool:
        d = _parse_date(value)
        return d is not None and d <= today

    for key, items in state.items():
        kept[key] = []
        for item in items or []:
            total += 1
            reason = None
            if "quote" in item and " ".join(item["quote"].split()).lower() not in owner_text:
                reason = f"{key}: quote not found in the owner's messages: {item['quote'][:80]!r}"
            elif key in ("done", "learned", "decisions") and _RELATIVE.search(item.get("text", "")):
                reason = f"{key}: relative date word in {item['text'][:80]!r}"
            elif not date_ok(item.get("date") or item.get("asked_at") or item.get("when")):
                reason = f"{key}: missing, unparseable or future date in {json.dumps(item)[:80]}"
            if reason:
                dropped += 1
                problems.append(reason)
                continue
            kept[key].append({k: episode_store.scrub(v) if isinstance(v, str) else v for k, v in item.items()})
    if total and dropped * 2 > total:
        return None, problems
    if problems:
        logger.info("compaction dropped %d of %d items: %s", dropped, total, problems)
    return kept, problems


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _hm(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M")


def render(state: dict, owner_rows: list[dict], rows: list[dict], span_start: datetime,
           span_end: datetime, now: datetime, *, fallback: bool, covered: int) -> str:
    import timeutils

    when = now.astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M")
    away = timeutils.owner_tz_name()
    if away:
        when += f" ({now.astimezone(timeutils.owner_tz()):%H:%M} {away}, where the owner is)"
    out = [FRAMING_START.format(when=when),
           f"Covers {span_start.astimezone(ISRAEL_TZ):%Y-%m-%d %H:%M} – "
           f"{span_end.astimezone(ISRAEL_TZ):%Y-%m-%d %H:%M} Israel time."]
    if fallback:
        out.append(f"({covered} earlier messages were replaced without an AI summary; the owner's own "
                   "messages are quoted below; search_history has everything.)")

    def section(title: str, lines: list[str]) -> None:
        if lines:
            out.append(f"{title}:\n" + "\n".join(f"- {line}" for line in lines))

    section("Open requests", [f"\"{r['quote']}\" (asked {r['asked_at']}) — {r['status']}"
                              for r in state.get("open_requests", [])])
    section("Done", [f"{d['date']}: {d['text']}" for d in state.get("done", [])])
    section("The owner's rules", [f"\"{r['quote']}\" ({r['date']})" for r in state.get("owner_rules", [])])
    section("Learned", [f"{x['date']}: {x['text']}"
                        + (f" (was: {x['supersedes']})" if x.get("supersedes") else "")
                        + (" [unverified]" if x.get("certainty") == "unverified" else "")
                        for x in state.get("learned", [])])
    section("Decisions", [f"{d['date']}: {d['text']}" for d in state.get("decisions", [])])
    section("Search hints", [f"{h['topic']}: {', '.join(h['terms'])} ({h['when']})"
                             for h in state.get("lookup_hints", [])])

    quotes, used = [], 0
    for r in reversed(owner_rows):
        text = " ".join(r["content"].split())
        line = f"[{_hm(r['ts'])}] {text[:500]}"
        if used + len(line) > OWNER_QUOTES_BUDGET:
            break
        quotes.append(line)
        used += len(line)
    section("The owner's messages, newest first", quotes)

    found: list[str] = []
    for r in rows:
        for pattern in (_URL, _AMOUNT, _CODE):
            for m in pattern.findall(r["content"]):
                if m not in found:
                    found.append(m)
    if found:
        out.append("Identifiers: " + ", ".join(found[:20]))
    section("Scheduled reminders and wakes (live, from the trigger store)", _live_triggers())
    section("Skills used today", _skills_today(now))
    out.append(FRAMING_END)
    return "\n\n".join(out)


def _live_triggers() -> list[str]:
    try:
        from triggers import store as trigger_store
        from triggers.model import Send

        lines = []
        for t in trigger_store.all_triggers():
            what = t.action.text if isinstance(t.action, Send) else f"wake: {t.action.instruction}"
            lines.append(f"{t.when.instant.astimezone(ISRAEL_TZ):%Y-%m-%d %H:%M} {what[:120]}")
        return lines[:15]
    except Exception:
        logger.exception("compaction: couldn't read the trigger store")
        return []


def _skills_today(now: datetime) -> list[str]:
    from tools import registry

    day_start = now.astimezone(ISRAEL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    last: dict[str, str] = {}
    for r in episode_store.rows("SELECT ts, tool_name FROM episodes WHERE role = 'tool_call' AND scope = 'user' "
                                "AND ts >= ? ORDER BY ts", (day_start.astimezone(timezone.utc).isoformat(),)):
        ns = registry.namespace_of(r["tool_name"] or "")
        if ns and ns != "core":
            last[ns] = r["ts"]
    return [f"{ns} (last used {_hm(ts)[-5:]})" for ns, ts in last.items()]


# ---------------------------------------------------------------------------
# The span and its rows
# ---------------------------------------------------------------------------

def _span_rows(thread_id: str, start: datetime, end: datetime) -> list[dict]:
    """The conversation rows the record covers: the thread's turns and every
    message Jarvis sent the owner, between ``start`` and ``end``."""
    return episode_store.rows(
        "SELECT * FROM episodes WHERE ts >= ? AND ts < ? AND "
        "((thread_id = ? AND scope = 'user' AND source <> 'slash_command') OR role = 'jarvis_sent') "
        "ORDER BY ts, id",
        (start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat(), thread_id))


def _transcript(rows: list[dict]) -> str:
    tag = {"owner": "owner", "jarvis": "jarvis", "jarvis_sent": "jarvis-sent", "system": "system",
           "tool_call": "tool", "tool_result": "tool"}
    lines = []
    for r in rows:
        content = " ".join(r["content"].split())[:TRANSCRIPT_ROW_CAP]
        name = f" {r['tool_name']}" if r["role"] in ("tool_call", "tool_result") else ""
        kind = " call" if r["role"] == "tool_call" else " result" if r["role"] == "tool_result" else ""
        lines.append(f"[{_hm(r['ts'])} {tag.get(r['role'], r['role'])}{name}{kind}] {content}")
    return "\n".join(lines)


def _tail_start_ts(thread_id: str) -> datetime | None:
    """When the first kept turn began: the KEEP_TURNS-th newest input row."""
    rows = episode_store.rows(
        "SELECT ts FROM episodes WHERE thread_id = ? AND scope = 'user' AND role IN ('owner', 'system') "
        "AND source <> 'slash_command' ORDER BY ts DESC, id DESC LIMIT ?", (thread_id, KEEP_TURNS))
    return _parse_dt(rows[-1]["ts"]) if len(rows) == KEEP_TURNS else None


def _last_clear(thread_id: str) -> datetime | None:
    rows = episode_store.rows(
        "SELECT ts FROM episodes WHERE thread_id = ? AND source = 'slash_command' AND role = 'owner' "
        "AND content LIKE '/clear%' ORDER BY ts DESC LIMIT 1", (thread_id,))
    return _parse_dt(rows[0]["ts"]) if rows else None


def _stub_old_results(tail: list) -> list:
    """Tool results in all but the newest kept turn become short stubs."""
    import agent

    starts = agent._turn_starts(tail)
    last_turn = starts[-1] if starts else 0
    out = []
    for i, m in enumerate(tail):
        if isinstance(m, ToolMessage) and i < last_turn:
            size = len(m.content) if isinstance(m.content, str) else len(str(m.content))
            m = ToolMessage(content=f"[result cleared at compaction — {size:,} chars; search_history finds it]",
                            name=m.name, tool_call_id=m.tool_call_id, id=m.id, status=m.status)
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=ISRAEL_TZ)


def _parse_date(value) -> object | None:
    dt = _parse_dt(value)
    return dt.date() if dt else None


def _ms_since(t0: datetime) -> int:
    return int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)
