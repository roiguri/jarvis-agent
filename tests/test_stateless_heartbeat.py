"""The stateless heartbeat: ticks and wakes keep no conversation, start from
their due tasks' warm-start skills, see their notes files and the today view
in the prompt, and hand their ack back in the turn outcome."""

import asyncio
import os
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage

import agent
import config
import heartbeat
import heartbeat_state
from episodes import store as episode_store
from observability import load_turns, store as telemetry_store
from tests.fakes import FakeLLM, tool_call

HEARTBEAT_MD = """# Tasks

- **inbox-check** | every 1h | notes: `heartbeat/inbox.md`
  Check the inbox.

- **steps** | every 4h
  Check steps.

- **gated-task** | every 1h | gate: some_gate
  Code runs this.
"""


@pytest.fixture
def heartbeat_md():
    written = []

    def write(rel, body):
        path = os.path.join(config.MEMORY_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        written.append(path)

    write("HEARTBEAT.md", HEARTBEAT_MD)
    yield write
    for path in written:
        os.remove(path)


@pytest.fixture
def run_tick(monkeypatch, fake_outbox):
    def run(due):
        monkeypatch.setattr(heartbeat, "TURN_LOCK", asyncio.Lock())
        monkeypatch.setattr(heartbeat_state, "any_due", lambda now: (True, list(due)))
        asyncio.run(heartbeat.run_heartbeat())
    return run


def ack(n, acted=(), notify=False, **extra):
    return AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": list(acted), "notify": notify, "summary": "done", **extra}, n)])


def test_a_tick_keeps_no_conversation(use_llm):
    use_llm(FakeLLM([ack(1), AIMessage(content="tick done")]))
    out = agent.ask_jarvis("tick", "heartbeat", scope="heartbeat")
    snap = agent.agent_executor.get_state({"configurable": {"thread_id": "heartbeat"}})
    assert not (snap.values or {}).get("messages"), "nothing saved for the heartbeat thread"
    assert out.ack == {"acted_tasks": [], "notify": False, "summary": "done"}, "ack in the outcome"


def test_each_tick_starts_from_its_input_alone(use_llm):
    llm = FakeLLM([ack(1), AIMessage(content="first"), ack(2), AIMessage(content="second")])
    use_llm(llm)
    agent.ask_jarvis("tick one", "heartbeat", scope="heartbeat")
    agent.ask_jarvis("tick two", "heartbeat", scope="heartbeat")
    second_call = llm.sent[2]
    assert not any("tick one" in str(m.content) for m in second_call), "no replay of earlier ticks"


def test_warm_start_skills_are_bound_from_the_first_call(use_llm):
    use_llm(FakeLLM([ack(1), AIMessage(content="ok")]))
    out = agent.ask_jarvis("tick", "heartbeat", scope="heartbeat", initial_skills={"fitness"})
    turn = load_turns()[-1]
    first = {r["tool"] for r in telemetry_store.rows(
        "SELECT tool FROM bound_tools WHERE turn_id = ? AND first_call_index = 1", (turn["turn_id"],))}
    assert "query_fitness_db" in first
    assert out.active_skills == ("fitness",)


def test_stamped_tasks_remember_their_skills_for_next_time(use_llm, heartbeat_md, run_tick):
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("activate_skill", {"namespaces": ["fitness"]}, 1)]),
        ack(2, acted=["inbox-check"]),
        AIMessage(content="done"),
    ]))
    run_tick(["inbox-check"])
    assert heartbeat_state.skills_for(["inbox-check"]) == {"fitness"}
    assert heartbeat_state.skills_for(["steps"]) == set(), "only the stamped task"

    use_llm(FakeLLM([ack(3, acted=["inbox-check"]), AIMessage(content="done")]))
    run_tick(["inbox-check"])
    assert load_turns()[-1]["active_skills_start"] == ["fitness"], "the next tick starts warm"


def test_due_tasks_notes_are_in_the_prompt(heartbeat_md):
    heartbeat_md("heartbeat/inbox.md", "Last checked: 09:00, 3 unread.")
    prompt = agent.build_system_prompt("heartbeat", set(), ["inbox-check", "steps"])
    assert "--- Notes: inbox-check (heartbeat/inbox.md) ---\nLast checked: 09:00, 3 unread." in prompt
    assert "--- Notes: steps (heartbeat/steps.md) ---\n(no notes file yet)" in prompt, "default path"
    only_steps = agent.build_system_prompt("heartbeat", set(), ["steps"])
    assert "Notes: inbox-check" not in only_steps, "a task that isn't due shows no notes"
    fail_open = agent.build_system_prompt("heartbeat", set(), None)
    assert "Notes: inbox-check" in fail_open and "Notes: gated-task" not in fail_open


def test_oversized_notes_are_cut(heartbeat_md):
    heartbeat_md("heartbeat/inbox.md", "n" * (agent.NOTES_MAX_CHARS + 10))
    prompt = agent.build_system_prompt("heartbeat", set(), ["inbox-check"])
    assert "n" * agent.NOTES_MAX_CHARS + "\n[truncated]" in prompt


def test_a_failed_tick_shows_in_the_next_ticks_today_view(use_llm, heartbeat_md, run_tick):
    use_llm(FakeLLM([RuntimeError("503 UNAVAILABLE")]))
    run_tick(["inbox-check"])
    failures = episode_store.rows("SELECT * FROM episodes WHERE meta LIKE '%failed%'")
    assert len(failures) == 1 and failures[0]["content"].startswith("Did not finish:")
    prompt = agent.build_system_prompt("heartbeat", set(), ["inbox-check"])
    assert "tick: Did not finish:" in prompt
