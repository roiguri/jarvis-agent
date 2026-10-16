"""Runs one eval case through the real heartbeat or a real owner turn, with the
real model.

``bootstrap()`` must run before any project import: it points JARVIS_ROOT at a
scratch directory and puts the API key in the environment. Each attempt then
resets the scratch root, seeds it from the fixtures and the case, swaps every
tool outside the local-safe allowlist for a recorder with a canned answer
(default-deny: nothing reaches Arbox, Google, media, GitHub or the web), pins
the clock, runs the tick or wake, and collects what happened from the scratch
episode store, the Outbox recorder and the telemetry store.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(EVALS_DIR, "fixtures", "memory")

# The staging instance's secrets: the only .env the runner reads, for the API
# key alone. Prod's is never read.
DEFAULT_ENV_FILE = "/app/jarvis_staging/secrets/.env"
_FORBIDDEN_ENV_FILES = {"/app/secrets/.env"}

# Tools that only touch the scratch root run for real; every other tool is
# replaced by a recorder.
ALLOW = {
    "read_memory", "write_memory", "list_memory", "manage_heartbeat_task",
    "manage_trigger", "heartbeat_respond", "activate_skill", "search_history",
}
DEFAULT_STUB = "No data available."


def bootstrap() -> str:
    """Scratch JARVIS_ROOT + API key; returns the scratch root. Call first."""
    import atexit

    root = tempfile.mkdtemp(prefix="jarvis-evals-")
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    os.environ["JARVIS_ROOT"] = root
    if not os.environ.get("GOOGLE_API_KEY"):
        env_file = os.environ.get("JARVIS_EVAL_ENV_FILE", DEFAULT_ENV_FILE)
        if os.path.realpath(env_file) in _FORBIDDEN_ENV_FILES:
            raise SystemExit(f"refusing to read {env_file}: evals use the staging key only")
        from dotenv import dotenv_values

        key = dotenv_values(env_file).get("GOOGLE_API_KEY")
        if not key:
            raise SystemExit(f"no GOOGLE_API_KEY in the environment or in {env_file}")
        os.environ["GOOGLE_API_KEY"] = key
    return root


@dataclass
class Result:
    calls: list[dict] = field(default_factory=list)   # {"tool", "args"}
    ack: dict | None = None
    reply: str = ""                                    # a chat case's reply text
    sends: list[dict] = field(default_factory=list)   # {"event", "text"}
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None
    error: str | None = None


class _Patches:
    def __init__(self):
        self._undo = []

    def set(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, value in reversed(self._undo):
            setattr(obj, name, value)
        self._undo.clear()


def _frozen_module(now: datetime):
    """Stand-in for ``import datetime`` (or ``as _dt``) with now() pinned."""
    import datetime as real

    class Frozen(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    return types.SimpleNamespace(datetime=Frozen, date=real.date, timezone=real.timezone,
                                 timedelta=real.timedelta)


class _Outbox:
    def __init__(self):
        self.sends: list[dict] = []

    async def notify_owner(self, text, *, event=None, metadata=None):
        from gateway.outbox import SendOutcome

        self.sends.append({"event": event, "text": text})
        return SendOutcome(ok=True)


def case_now(case: dict) -> datetime:
    """The case's Israel time on tomorrow's date, so anything the turn schedules
    for later that day is in the future for tools that read the real clock."""
    from timeutils import ISRAEL_TZ

    hh, mm = map(int, str(case["time"]).split(":"))
    day = datetime.now(ISRAEL_TZ).date() + timedelta(days=1)
    # A case that depends on the weekday names the one it must not fall on.
    if day.strftime("%A").lower() == str(case.get("weekday_not", "")).lower():
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ISRAEL_TZ)


def _reset():
    import agent
    import config
    from episodes import store as episode_store
    from observability import store as telemetry_store

    for name in os.listdir(config.MEMORY_DIR):
        if name.startswith("threads.sqlite"):
            continue
        path = os.path.join(config.MEMORY_DIR, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    for name in os.listdir(config.DATA_DIR):
        path = os.path.join(config.DATA_DIR, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    os.makedirs(os.path.join(config.DATA_DIR, "logs"), exist_ok=True)
    for path in (episode_store.STORE_PATH, telemetry_store.STORE_PATH):
        episode_store._initialized.discard(path)
        telemetry_store._initialized.discard(path)
    agent.memory.delete_thread("heartbeat")
    agent.memory.delete_thread("owner")
    shutil.copytree(FIXTURES, config.MEMORY_DIR, dirs_exist_ok=True)


def _seed(case: dict, now: datetime):
    import json

    import agent
    import config
    from episodes import store as episode_store
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from tools.core.history import CHAT_LOG, NOTIFICATION_LOG

    for rel, body in (case.get("files") or {}).items():
        path = os.path.join(config.MEMORY_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    def at(hhmm: str) -> datetime:
        hh, mm = map(int, hhmm.split(":"))
        return now.replace(hour=hh, minute=mm)

    with open(CHAT_LOG, "w", encoding="utf-8") as f:
        for row in case.get("chat") or []:
            f.write(json.dumps({"ts": at(row["time"]).astimezone(timezone.utc).isoformat(),
                                "thread_id": "owner", "role": row["role"],
                                "content": row["text"]}) + "\n")

    # Earlier background activity today, in every form a version of the code
    # might read it from: episode rows (the today view), heartbeat-thread
    # history (a tick that replays earlier ticks), and the notification log.
    history = []
    with episode_store.write() as con, open(NOTIFICATION_LOG, "w", encoding="utf-8") as notes:
        for i, e in enumerate(case.get("earlier") or []):
            ts = at(e["time"]).astimezone(timezone.utc).isoformat()
            source = e.get("kind", "tick")
            source = "wake" if source == "wake" else "heartbeat"
            turn_id = f"earlier{i}"
            ack = {"acted_tasks": e.get("acted") or [], "notify": bool(e.get("notify")),
                   "summary": e["summary"], "notification_text": e.get("message", "")}
            episode_store.insert(con, ts=ts, turn_id=turn_id, thread_id="heartbeat", scope="heartbeat",
                                 role="system", source=source,
                                 meta={"task": e.get("task")} if e.get("task") else None,
                                 content=e.get("instruction", "Run the scheduled heartbeat check now."))
            episode_store.insert(con, ts=ts, turn_id=turn_id, thread_id="heartbeat", scope="heartbeat",
                                 role="tool_call", source=source, tool_name="heartbeat_respond",
                                 content=json.dumps(ack))
            if e.get("notify"):
                notes.write(json.dumps({"ts": ts, "event": "heartbeat",
                                        "message": e.get("message", e["summary"])}) + "\n")
            stamp = agent._turn_stamp(at(e["time"]))
            history += [
                HumanMessage(content=f"{stamp} {e.get('instruction', 'Run the scheduled heartbeat check now.')}"),
                AIMessage(content="", tool_calls=[{"name": "heartbeat_respond", "args": ack,
                                                    "id": f"call_e{i}", "type": "tool_call"}]),
                ToolMessage(content="Acknowledged.", name="heartbeat_respond", tool_call_id=f"call_e{i}"),
                AIMessage(content=e["summary"]),
            ]
    if history:
        agent.agent_executor.update_state({"configurable": {"thread_id": "heartbeat"}},
                                          {"messages": history, "scope": "heartbeat"}, as_node="llm")


FILLER_TOPICS = [
    ("what's on my book list?", "book_list.md"),
    ("remind me what's in my memory index", "MEMORY.md"),
    ("which books did I finish?", "book_list.md"),
    ("show me my profile notes", "USER.md"),
]


def _seed_chat(case: dict, now: datetime) -> None:
    """An owner conversation from earlier in the day, written the way a real
    day leaves it: every row in the episode store and the chat log, and the
    thread holding whatever its window keeps. ``filler`` adds that many
    ordinary memory-lookup turns after the scripted ones, so the earliest
    scripted turns fall out of the window."""
    import json

    import agent
    import pending_mirrors
    from episodes import store as episode_store
    from gateway.base import OWNER_THREAD_ID
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from tools.core.history import CHAT_LOG

    def at(hhmm: str) -> datetime:
        hh, mm = map(int, hhmm.split(":"))
        return now.replace(hour=hh, minute=mm)

    turns = list(case.get("conversation") or [])
    n_filler = int(case.get("filler", 0))
    if n_filler:
        start = at(turns[-1]["time"]) if turns else now.replace(hour=8, minute=0)
        step = (now - start) / (n_filler + 1)
        for k in range(n_filler):
            question, filename = FILLER_TOPICS[k % len(FILLER_TOPICS)]
            when = (start + step * (k + 1)).strftime("%H:%M")
            turns.append({"time": when, "owner": question,
                          "tools": [{"name": "read_memory", "args": {"filename": filename}, "result": None}],
                          "jarvis": f"Here's what {filename} holds right now."})

    messages, pending_sent = [], []
    with episode_store.write() as con, open(CHAT_LOG, "w", encoding="utf-8") as chat:
        for i, turn in enumerate(turns):
            ts = at(turn["time"]).astimezone(timezone.utc).isoformat()
            if "sent" in turn:
                episode_store.insert(con, ts=ts, role="jarvis_sent", source="outbox",
                                     meta={"event": turn.get("event", "heartbeat")}, content=turn["sent"])
                pending_sent.append(f"{pending_mirrors.PREFIX.get(turn.get('event', 'heartbeat'))} {turn['sent']}")
                continue
            turn_id = f"chat{i}"
            common = dict(ts=ts, turn_id=turn_id, thread_id=OWNER_THREAD_ID, scope="user",
                          channel="telegram", source="chat")
            if pending_sent:
                messages.append(HumanMessage(content=pending_mirrors.HEADER + "\n" + "\n".join(pending_sent)))
                pending_sent = []
            messages.append(HumanMessage(content=f"{agent._turn_stamp(at(turn['time']))} {turn['owner']}"))
            episode_store.insert(con, role="owner", content=turn["owner"], **common)
            chat.write(json.dumps({"ts": ts, "thread_id": OWNER_THREAD_ID, "role": "user",
                                   "content": turn["owner"]}) + "\n")
            for j, call in enumerate(turn.get("tools") or []):
                result = call["result"]
                if result is None:  # a filler lookup reads the real fixture file
                    path = os.path.join(FIXTURES, call["args"]["filename"])
                    result = open(path, encoding="utf-8").read() if os.path.exists(path) else "(empty)"
                call_id = f"call_{i}_{j}"
                messages.append(AIMessage(content="", tool_calls=[{"name": call["name"], "args": call["args"],
                                                                   "id": call_id, "type": "tool_call"}]))
                messages.append(ToolMessage(content=result, name=call["name"], tool_call_id=call_id))
                episode_store.insert(con, role="tool_call", tool_name=call["name"],
                                     content=json.dumps(call["args"]), **common)
                episode_store.insert(con, role="tool_result", tool_name=call["name"], content=result, **common)
            messages.append(AIMessage(content=turn["jarvis"]))
            episode_store.insert(con, role="jarvis", content=turn["jarvis"], **common)
            chat.write(json.dumps({"ts": ts, "thread_id": OWNER_THREAD_ID, "role": "assistant",
                                   "content": turn["jarvis"]}) + "\n")
    if messages:
        # One write carrying user input: the reducer trims it to the window,
        # as a day of real turns would have.
        agent.agent_executor.update_state({"configurable": {"thread_id": OWNER_THREAD_ID}},
                                          {"messages": messages, "scope": "user",
                                           "last_user_turn_at": at(turns[-1]["time"]).astimezone(timezone.utc).isoformat()},
                                          as_node="llm")


def _stub_tools(case: dict, patches: _Patches):
    from dataclasses import replace

    from langchain_core.tools import StructuredTool
    from tools import registry

    stubs = case.get("stubs") or {}
    swapped = {}
    for name, entry in registry._REGISTRY.items():
        if name in ALLOW:
            swapped[name] = entry
            continue
        answer = stubs.get(name, DEFAULT_STUB)
        fake = StructuredTool(name=entry.tool.name, description=entry.tool.description,
                              args_schema=entry.tool.args_schema,
                              func=lambda _answer=answer, **_: _answer)
        swapped[name] = replace(entry, tool=fake)
    patches.set(registry, "_REGISTRY", swapped)


def run_case(case: dict) -> Result:
    import asyncio
    import json

    import agent
    import heartbeat
    import heartbeat_state
    from episodes import store as episode_store
    from gateway import factory
    from observability import store as telemetry_store

    now = case_now(case)
    _reset()
    _seed(case, now)
    if case.get("kind") in ("chat", "suggestions"):
        _seed_chat(case, now)
    seeded_max = (episode_store.rows("SELECT MAX(id) AS m FROM episodes") or [{"m": 0}])[0]["m"] or 0

    patches = _Patches()
    outbox = _Outbox()
    result = Result()
    try:
        _stub_tools(case, patches)
        frozen = _frozen_module(now.astimezone(timezone.utc))
        patches.set(agent, "_dt", frozen)
        patches.set(heartbeat, "datetime", frozen)
        patches.set(factory, "default_outbox", lambda: outbox)
        patches.set(heartbeat, "TURN_LOCK", asyncio.Lock())
        if case.get("kind") == "chat":
            from gateway.base import OWNER_THREAD_ID

            if case.get("compact"):
                try:
                    import compaction
                except ImportError:
                    compaction = None  # code from before compaction existed: plain trim only
                if compaction is not None:
                    patches.set(compaction, "datetime", frozen.datetime)
                    compaction.compact_thread(OWNER_THREAD_ID, force=True)
            outcome = agent.ask_jarvis(case["message"], OWNER_THREAD_ID, channel="telegram")
            result.reply = outcome.text
        elif case.get("kind") == "suggestions":
            import memory_suggestions

            patches.set(memory_suggestions, "datetime", frozen.datetime)
            asyncio.run(memory_suggestions.send(now.date()))
        elif case.get("kind", "tick") == "wake":
            from triggers import store as trigger_store
            from triggers.model import ORIGIN_JARVIS, At, Trigger, Turn

            w = case["wake"]
            trigger = Trigger("evalwake", At(now.astimezone(timezone.utc)),
                              Turn(w["instruction"], w.get("task")), ORIGIN_JARVIS, "heartbeat")
            trigger_store.add(trigger)
            asyncio.run(heartbeat.run_wake(trigger))
        else:
            due = list(case["due"])
            patches.set(heartbeat_state, "any_due", lambda _now: (True, due))
            asyncio.run(heartbeat.run_heartbeat())
    except Exception as e:  # a crash is a failed attempt, not a crashed run
        result.error = f"{type(e).__name__}: {e}"
    finally:
        patches.restore()

    rows = episode_store.rows("SELECT * FROM episodes WHERE id > ? AND role = 'tool_call' ORDER BY id",
                              (seeded_max,))
    for r in rows:
        try:
            args = json.loads(r["content"])
        except ValueError:
            args = {}
        result.calls.append({"tool": r["tool_name"], "args": args})
        if r["tool_name"] == "heartbeat_respond":
            result.ack = args
    result.sends = outbox.sends
    for t in telemetry_store.rows("SELECT * FROM turns"):
        result.input_tokens += t["input_tokens"] or 0
        result.cache_read_tokens += t["cache_read_tokens"] or 0
        result.output_tokens += t["output_tokens"] or 0
        result.model = result.model or t["model"]
    return result
