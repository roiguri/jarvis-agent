"""The idle skill reset: a user turn after SKILL_IDLE_RESET of silence starts
with no skills active; a shorter gap, and the heartbeat, keep them."""

from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage

import agent
from observability import load_turns, store
from tests.fakes import FakeLLM, tool_call


def state(thread_id):
    snap = agent.agent_executor.get_state({"configurable": {"thread_id": thread_id}})
    return snap.values or {}


def set_state(thread_id, **values):
    agent.agent_executor.update_state({"configurable": {"thread_id": thread_id}}, values, as_node="llm")


def activate_fitness(use_llm, thread_id, scope="user"):
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("activate_skill", {"namespaces": ["fitness"]}, 1)]),
        AIMessage(content="activated"),
    ]))
    agent.ask_jarvis("track my workout", thread_id, scope=scope)
    assert state(thread_id)["active_skills"] == {"fitness"}


def ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def test_long_silence_resets_skills(use_llm):
    activate_fitness(use_llm, "t_idle")
    set_state("t_idle", last_user_turn_at=ago(4))
    use_llm(FakeLLM([AIMessage(content="hi again")]))
    agent.ask_jarvis("hello", "t_idle")
    assert state("t_idle")["active_skills"] == set(), "skills reset after 4h"
    assert load_turns()[-1]["active_skills_start"] == [], "the turn row shows the reset"


def test_short_gap_keeps_skills(use_llm):
    activate_fitness(use_llm, "t_short")
    set_state("t_short", last_user_turn_at=ago(2))
    use_llm(FakeLLM([AIMessage(content="still here")]))
    agent.ask_jarvis("and another thing", "t_short")
    assert state("t_short")["active_skills"] == {"fitness"}, "2h is under the threshold"
    assert load_turns()[-1]["active_skills_start"] == ["fitness"]


def test_every_user_turn_records_its_time(use_llm):
    use_llm(FakeLLM([AIMessage(content="ok")]))
    before = datetime.now(timezone.utc)
    agent.ask_jarvis("hi", "t_stamp")
    recorded = datetime.fromisoformat(state("t_stamp")["last_user_turn_at"])
    assert before <= recorded <= datetime.now(timezone.utc)


def test_no_recorded_last_turn_counts_as_idle(use_llm):
    set_state("t_unrecorded", messages=[AIMessage(content="an old reply")], active_skills={"fitness", "travel"})
    assert "last_user_turn_at" not in state("t_unrecorded")
    use_llm(FakeLLM([AIMessage(content="ok")]))
    agent.ask_jarvis("hi", "t_unrecorded")
    assert state("t_unrecorded")["active_skills"] == set()


def test_heartbeat_is_not_reset_and_records_no_turn_time(use_llm):
    use_llm(FakeLLM([AIMessage(content="tick")]))
    out = agent.ask_jarvis("tick", "heartbeat", scope="heartbeat", initial_skills={"fitness"})
    assert out.active_skills == ("fitness",), "the warm start is kept, never reset"
    assert "last_user_turn_at" not in state("heartbeat"), "only user turns are recorded"


def test_needed_skill_comes_back_in_the_same_turn(use_llm):
    activate_fitness(use_llm, "t_back")
    set_state("t_back", last_user_turn_at=ago(5))
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("activate_skill", {"namespaces": ["fitness"]}, 2)]),
        AIMessage(content="", tool_calls=[tool_call("query_fitness_db", {"sql": "select 1"}, 3)]),
        AIMessage(content="here are your lifts"),
    ]))
    out = agent.ask_jarvis("what did I lift last week?", "t_back")
    assert out.text == "here are your lifts"
    assert state("t_back")["active_skills"] == {"fitness"}
    turn = load_turns()[-1]
    bound = {r["tool"]: r["first_call_index"] for r in store.rows(
        "SELECT tool, first_call_index FROM bound_tools WHERE turn_id = ?", (turn["turn_id"],))}
    assert bound["query_fitness_db"] == 2, "the first call ran without fitness; it rejoined after activation"
    assert (turn["active_skills_start"], turn["active_skills_end"]) == ([], ["fitness"])


def test_calling_an_inactive_skills_tool_activates_it_without_running(use_llm):
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("query_fitness_db", {"sql": "select 1"}, 4)]),
        AIMessage(content="", tool_calls=[tool_call("query_fitness_db", {"sql": "select 1"}, 5)]),
        AIMessage(content="here are your lifts"),
    ]))
    agent.ask_jarvis("what did I lift last week?", "t_direct")
    turn = load_turns()[-1]
    calls = store.rows("SELECT llm_call_index, status FROM tool_calls WHERE turn_id = ? ORDER BY id",
                       (turn["turn_id"],))
    assert [(c["llm_call_index"], c["status"]) for c in calls] == [(1, "not_active"), (2, "ok")], \
        "refused first, ran once the skill's rules were in the prompt"
    bound = {r["tool"]: r["first_call_index"] for r in store.rows(
        "SELECT tool, first_call_index FROM bound_tools WHERE turn_id = ?", (turn["turn_id"],))}
    assert bound["query_fitness_db"] == 2
    assert turn["active_skills_end"] == ["fitness"]
