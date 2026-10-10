"""The telemetry store: what a turn writes, how a model call's input is split,
jobs outside a turn, the JSONL import, retention, and that a write failure
never reaches the turn."""

import json
import os
import types
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

import agent
from observability import store, summarize_usage, telemetry, usage
from tests.conftest import LOG_DIR
from tests.fakes import FakeLLM, tool_call


def ai(content="", calls=(), tokens=(100, 5, 40)):
    inp, out, cached = tokens
    return AIMessage(
        content=content, tool_calls=list(calls),
        usage_metadata={"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out,
                        "input_token_details": {"cache_read": cached}},
        response_metadata={"finish_reason": "STOP", "model_name": "fake-model"},
    )


def turn_rows():
    return store.rows("SELECT * FROM turns ORDER BY ts")


def calls_of(turn_id):
    return store.rows("SELECT * FROM llm_calls WHERE turn_id = ? ORDER BY call_index", (turn_id,))


# --- What a turn writes ----------------------------------------------------------

def test_turn_writes_linked_rows(use_llm):
    use_llm(FakeLLM([
        ai(calls=[tool_call("list_memory", {}, 1)], tokens=(1000, 10, 400)),
        ai("done", tokens=(1200, 20, 900)),
    ]))
    agent.ask_jarvis("what's in memory?", "t_links", channel="telegram")

    [turn] = turn_rows()
    assert (turn["scope"], turn["channel"], turn["outcome"]) == ("user", "telegram", "completed")
    assert turn["ended_at"] is not None, "closed at the end"
    assert (turn["llm_calls"], turn["tool_calls"]) == (2, 1)
    assert (turn["input_tokens"], turn["cache_read_tokens"], turn["output_tokens"]) == (2200, 1300, 30)

    calls = calls_of(turn["turn_id"])
    assert [c["call_index"] for c in calls] == [1, 2]
    assert [c["input_tokens"] for c in calls] == [1000, 1200], "tokens per call"
    assert all(c["latency_ms"] is not None and c["finish_reason"] == "STOP" for c in calls)

    [tool] = store.rows("SELECT * FROM tool_calls")
    assert (tool["tool"], tool["llm_call_index"], tool["status"]) == ("list_memory", 1, "ok"), \
        "a tool call links to the model call that issued it"
    assert tool["result_size"] is not None

    hashes = {c["prompt_hash"] for c in calls}
    prompts = store.rows("SELECT * FROM prompts")
    assert hashes <= {p["prompt_hash"] for p in prompts}, "every call's prompt is stored"
    assert len(prompts) <= len(calls), "an unchanged prompt is stored once"
    assert all(p["chars"] == len(p["text"]) for p in prompts)


def test_bound_tools_record_when_each_tool_joined(use_llm):
    use_llm(FakeLLM([
        ai(calls=[tool_call("activate_skill", {"namespaces": ["fitness"]}, 1)]),
        ai("ok"),
    ]))
    agent.ask_jarvis("activate fitness", "t_bound")
    [turn] = turn_rows()
    bound = {r["tool"]: r for r in store.rows(
        "SELECT * FROM bound_tools WHERE turn_id = ?", (turn["turn_id"],))}
    assert bound["list_memory"]["first_call_index"] == 1, "core tools bound from the first call"
    assert bound["query_fitness_db"]["first_call_index"] == 2, "a skill's tools join when activated"
    assert bound["query_fitness_db"]["namespace"] == "fitness"
    assert all(r["schema_chars"] > 0 for r in bound.values())
    calls = calls_of(turn["turn_id"])
    assert calls[1]["bound_tool_count"] > calls[0]["bound_tool_count"]
    assert calls[1]["schema_chars"] > calls[0]["schema_chars"]


def test_composition_splits_at_the_turn_start(use_llm):
    use_llm(FakeLLM([ai("first answer")]))
    agent.ask_jarvis("first question", "t_split")
    use_llm(FakeLLM([
        ai(calls=[tool_call("list_memory", {}, 2)]),
        ai("second answer"),
    ]))
    agent.ask_jarvis("second question", "t_split")

    first, second = turn_rows()
    [only] = calls_of(first["turn_id"])
    assert (only["history_messages"], only["turn_messages"], only["history_chars"]) == (0, 1, 0)

    a, b = calls_of(second["turn_id"])
    assert a["history_messages"] == 2, "the earlier turn's question and answer"
    assert a["history_chars"] > 0
    assert a["turn_messages"] == 1, "this turn's input"
    assert (b["history_messages"], b["turn_messages"]) == (2, 3), \
        "this turn grows by the call and its result; history stays put"
    assert b["turn_chars"] > a["turn_chars"]
    assert a["prompt_chars"] > 0 and a["schema_chars"] > 0


def test_heartbeat_records_due_tasks_and_trigger(use_llm):
    use_llm(FakeLLM([ai("tick")]))
    wake = types.SimpleNamespace(id="wake0001", action=types.SimpleNamespace(task=None))
    agent.ask_jarvis("tick", "heartbeat", scope="heartbeat",
                     heartbeat_due_tasks=["inbox-check"], trigger=wake)
    [turn] = turn_rows()
    assert json.loads(turn["due_tasks"]) == ["inbox-check"]
    assert turn["trigger_id"] == "wake0001"


def test_failed_model_call_is_recorded(use_llm):
    use_llm(FakeLLM([RuntimeError("503 UNAVAILABLE")]))
    agent.ask_jarvis("hello", "t_fail")
    [turn] = turn_rows()
    assert turn["outcome"] == "failed"
    [call] = calls_of(turn["turn_id"])
    assert "503 UNAVAILABLE" in call["error"]
    assert call["latency_ms"] is not None and call["prompt_chars"] > 0


def test_open_row_until_the_turn_ends():
    token = telemetry.TURN_ID.set("open-turn")
    try:
        telemetry.record_turn_start(thread_id="t_open", scope="user")
        [row] = turn_rows()
        assert (row["turn_id"], row["ended_at"]) == ("open-turn", None), \
            "a turn that never ends stays visible"
        assert usage.load_turns() == [], "rollups leave open rows out"
        assert len(usage.load_turns(include_open=True)) == 1
        telemetry.record_turn_end()
        [row] = turn_rows()
        assert row["ended_at"] is not None
    finally:
        telemetry.TURN_ID.reset(token)


def test_write_failure_never_reaches_the_turn(use_llm, monkeypatch):
    def locked():
        raise OSError("database is locked")

    monkeypatch.setattr(store, "write", locked)
    use_llm(FakeLLM([ai(calls=[tool_call("list_memory", {}, 3)]), ai("fine")]))
    out = agent.ask_jarvis("hi", "t_locked")
    assert out.text == "fine"


# --- Jobs -----------------------------------------------------------------------

def test_job_records_calls_outside_a_turn():
    with telemetry.job("compaction"):
        telemetry.record_llm_call(ai("summary", tokens=(5000, 300, 0)), latency_ms=900)
    [row] = turn_rows()
    assert (row["scope"], row["job"], row["outcome"], row["llm_calls"]) == ("job", "compaction", "completed", 1)
    assert row["input_tokens"] == 5000
    [call] = calls_of(row["turn_id"])
    assert call["latency_ms"] == 900
    assert telemetry.TURN_ACC.get() is None and telemetry.TURN_ID.get() is None

    with pytest.raises(ValueError):
        with telemetry.job("nightly"):
            raise ValueError("bad op")
    failed = turn_rows()[-1]
    assert (failed["job"], failed["outcome"]) == ("nightly", "failed")
    assert "bad op" in failed["error"]


def test_call_outside_turn_or_job_is_not_recorded():
    telemetry.record_llm_call(ai("stray"))
    assert store.rows("SELECT * FROM llm_calls") == []


# --- Retention ------------------------------------------------------------------

def test_trim_drops_rows_past_retention():
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    with store.write() as con:
        for ts, tid in ((old, "old"), (new, "new")):
            con.execute("INSERT INTO turns (ts, turn_id, ended_at) VALUES (?, ?, ?)", (ts, tid, ts))
            con.execute("INSERT INTO llm_calls (ts, turn_id) VALUES (?, ?)", (ts, tid))
            con.execute("INSERT INTO prompts (prompt_hash, text, first_seen, last_seen) "
                        "VALUES (?, '', ?, ?)", (tid, ts, ts))
    deleted = store.trim()
    assert deleted["turns"] == deleted["llm_calls"] == deleted["prompts"] == 1
    assert [r["turn_id"] for r in turn_rows()] == ["new"]


# --- JSONL import ---------------------------------------------------------------
# TODO(#150): remove these tests with the import.

def _write_jsonl(name, records):
    with open(os.path.join(LOG_DIR, name), "w", encoding="utf-8") as f:
        for r in records:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def _legacy_turns():
    now = datetime.now(timezone.utc)
    rows = []
    for i, (scope, model, outcome, error) in enumerate([
        ("user", "gemini-3-flash-preview", "completed", None),
        ("heartbeat", "gemini-3-flash-preview", "completed", None),
        ("user", "gemini-3-flash-preview", "budget_exhausted", "budget exhausted: steps"),
        ("user", "gemini-9-unpriced", "failed", "RuntimeError: x"),
        ("heartbeat", "gemini-3-flash-preview", None, None),
    ]):
        ts = (now - timedelta(days=i, hours=1)).isoformat()
        rows.append({
            "ts": ts, "turn_id": f"legacy{i}", "thread_id": "owner", "scope": scope,
            "started_at": ts, "ended_at": ts, "duration_ms": 1000 + i,
            "llm_calls": 2 + i, "tool_calls": i, "input_tokens": 10000 * (i + 1),
            "cache_read_tokens": 1000 * i, "output_tokens": 100 + i, "reasoning_tokens": i,
            "total_tokens": 10000 * (i + 1) + 100 + i, "model": model,
            "active_skills_start": [], "active_skills_end": ["fitness"],
            "no_action": scope == "heartbeat", "error": error, "outcome": outcome,
            "budget": {"limits": {"max_llm_calls": 30},
                       "exhausted_by": "steps" if outcome == "budget_exhausted" else None,
                       "wrapped_up": False},
        })
    return rows


def test_import_is_idempotent_and_skips_old_and_bad_lines():
    turns = _legacy_turns()
    too_old = dict(turns[0], turn_id="ancient",
                   ts=(datetime.now(timezone.utc) - timedelta(days=400)).isoformat())
    _write_jsonl("turns.jsonl", turns + [too_old, "{not json"])
    _write_jsonl("tool_calls.jsonl", [
        {"ts": turns[0]["ts"], "turn_id": "legacy0", "tool": "list_memory", "namespace": "core",
         "destructive": False, "duration_ms": 5, "status": "ok", "args_size": 2,
         "error": None, "traceback": None},
        # Very old rows carry no turn_id; a re-run must not duplicate them either.
        {"ts": turns[1]["ts"], "tool": "read_memory", "namespace": "core", "destructive": False,
         "duration_ms": 3, "status": "ok", "args_size": 9, "error": None, "traceback": None},
    ])
    paths = (telemetry.TURNS_LOG, telemetry.TOOL_CALLS_LOG)
    first = store.import_jsonl(*paths)
    assert first == {"turns": 5, "tool_calls": 2, "skipped_lines": 1}
    assert store.imported()
    again = store.import_jsonl(*paths)
    assert (again["turns"], again["tool_calls"]) == (0, 0), "a re-run adds nothing"
    assert len(turn_rows()) == 5


def test_readers_match_the_jsonl_path_on_imported_data(monkeypatch):
    turns = _legacy_turns()
    _write_jsonl("turns.jsonl", turns)
    store.import_jsonl(telemetry.TURNS_LOG, telemetry.TOOL_CALLS_LOG)

    ranges = {"all": (None, None), "2 days": usage.israel_last_n_days(2),
              "one day": usage.israel_day_range(
                  datetime.fromisoformat(turns[1]["ts"]).astimezone(usage._IL_TZ).date().isoformat())}
    groups = ("day", "scope", "day+scope", "week")
    from_store = {(r, g): summarize_usage(*ranges[r], group_by=g) for r in ranges for g in groups}

    # The old path: the same rollup over the raw JSONL records, filtered by ts.
    def jsonl_load_turns(since=None, until=None):
        def inside(rec):
            ts = datetime.fromisoformat(rec["ts"])
            return (since is None or ts >= since) and (until is None or ts < until)
        return [rec for rec in turns if inside(rec)]

    monkeypatch.setattr(usage, "load_turns", jsonl_load_turns)
    from_jsonl = {(r, g): summarize_usage(*ranges[r], group_by=g) for r in ranges for g in groups}
    assert from_store == from_jsonl
    assert from_store[("one day", "scope")], "the one-day window is not empty"


# --- Measurement failures and message shapes ------------------------------------

def test_measurement_failure_is_recorded_not_raised(use_llm, monkeypatch):
    def broken(message):
        raise TypeError("unexpected content shape")

    monkeypatch.setattr(agent, "_message_chars", broken)
    use_llm(FakeLLM([ai("still answered")]))
    out = agent.ask_jarvis("hi", "t_measure")
    assert out.text == "still answered", "the turn is unaffected"
    [turn] = turn_rows()
    [call] = calls_of(turn["turn_id"])
    assert "unexpected content shape" in call["telemetry_error"]
    assert call["prompt_chars"] is None, "no composition rather than a wrong one"
    assert call["input_tokens"] == 100, "tokens still recorded"
    n = usage.telemetry_errors()
    assert n == 1
    assert "⚠ 1 telemetry error" in usage.format_usage_table(summarize_usage(), telemetry_errors=n)
    assert "⚠ 1 telemetry error" in usage.format_usage_table([], telemetry_errors=n), \
        "shown even when the period has no finished turns"


def test_message_chars_tolerates_empty_text_blocks():
    msg = types.SimpleNamespace(content=[{"type": "text", "text": None}, {"type": "media", "data": "x" * 50}],
                                tool_calls=None)
    assert agent._message_chars(msg) == 0, "empty text counts 0; media blobs are not prompt text"


def test_mirror_block_counts_as_this_turns_input(use_llm):
    from gateway.base import OWNER_THREAD_ID

    sent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _write_jsonl("notifications.jsonl", [{"ts": sent, "event": "heartbeat", "message": "Morning briefing"}])
    use_llm(FakeLLM([ai("answer")]))
    agent.ask_jarvis("about that briefing", OWNER_THREAD_ID)
    [turn] = turn_rows()
    [call] = calls_of(turn["turn_id"])
    assert (call["history_messages"], call["turn_messages"]) == (0, 2), \
        "the mirror block and the message it precedes are one turn's input"
