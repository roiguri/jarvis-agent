"""The episode store: what each kind of turn writes, content hygiene, the
search_history tool, the today view, the history import, and that a store
failure never reaches a turn."""

import asyncio
import json
import os
import types
from datetime import date, datetime, timedelta, timezone

from langchain_core.messages import AIMessage

import agent
from episodes import record, store, today
from gateway.base import OWNER_THREAD_ID
from observability import load_turns
from tests.conftest import LOG_DIR
from tests.fakes import FakeLLM, tool_call
from tools import registry
from tools.core.history import search_history


def rows(where="1 = 1", params=()):
    return store.rows(f"SELECT * FROM episodes WHERE {where} ORDER BY id", params)


def tool_turn():
    return FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 1)]),
        AIMessage(content="Your memory has a reading list."),
    ])


# --- What a turn writes ----------------------------------------------------------

def test_user_turn_records_input_steps_and_reply(use_llm):
    use_llm(tool_turn())
    agent.ask_jarvis("what's in my memory?", "t_ep", channel="telegram")
    got = rows()
    assert [r["role"] for r in got] == ["owner", "tool_call", "tool_result", "jarvis"]
    owner, call, result, reply = got
    assert owner["content"] == "what's in my memory?", "the raw message, without the turn stamp"
    assert (call["tool_name"], json.loads(call["content"])) == ("list_memory", {})
    assert result["tool_name"] == "list_memory" and result["content"]
    assert reply["content"] == "Your memory has a reading list."
    turn_id = load_turns()[-1]["turn_id"]
    assert {r["turn_id"] for r in got} == {turn_id}, "joins telemetry by turn_id"
    assert {(r["scope"], r["source"], r["channel"]) for r in got} == {("user", "chat", "telegram")}


def test_files_reach_the_model_once_and_are_recorded_as_markers(use_llm):
    from files import store as file_store
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"0" * 100
    rec = file_store.add(jpeg, source="test", filename="photo.jpg")
    llm = FakeLLM([AIMessage(content="nice photo")])
    use_llm(llm)
    agent.ask_jarvis("look", "t_media", files=[rec])
    first = llm.sent[0][-1].content
    assert any(b.get("type") == "image_url" for b in first), "the model sees the image"
    assert any(rec.marker() in (b.get("text") or "") for b in first), "and its marker"
    owner = rows("role = 'owner'")[0]
    assert owner["content"] == f"look\n{rec.marker()}"
    assert "base64" not in owner["content"]


def test_a_file_with_no_caption_is_recorded_as_its_marker(use_llm):
    from files import store as file_store
    rec = file_store.add(b"%PDF-1.4 x", source="test")
    use_llm(FakeLLM([AIMessage(content="a pdf")]))
    agent.ask_jarvis("", "t_media_nocap", files=[rec])
    assert rows("role = 'owner'")[0]["content"] == rec.marker()


def test_failed_turn_keeps_the_owner_message(use_llm):
    use_llm(FakeLLM([RuntimeError("503 UNAVAILABLE")]))
    agent.ask_jarvis("are you there?", "t_fail")
    roles = [r["role"] for r in rows()]
    assert roles[0] == "owner" and rows()[0]["content"] == "are you there?"
    assert "jarvis" in roles, "the failure note written into the thread is recorded too"


def test_forwarded_notification_block_is_not_recorded_as_owner(use_llm):
    sent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with open(os.path.join(LOG_DIR, "notifications.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": sent, "event": "heartbeat", "message": "Morning briefing: HRV good"}) + "\n")
    use_llm(FakeLLM([AIMessage(content="glad to hear")]))
    agent.ask_jarvis("thanks for the briefing", OWNER_THREAD_ID)
    owners = rows("role = 'owner'")
    assert [r["content"] for r in owners] == ["thanks for the briefing"]
    assert not rows("content LIKE '%Morning briefing%'"), "the send is recorded once, by the outbox path"


def test_heartbeat_tick_and_wake_inputs(use_llm):
    use_llm(FakeLLM([AIMessage(content="tick done")]))
    agent.ask_jarvis("tick: inbox-check is due", "heartbeat", scope="heartbeat")
    wake = types.SimpleNamespace(id="wake0001", action=types.SimpleNamespace(task="step-challenge"))
    use_llm(FakeLLM([AIMessage(content="wake done")]))
    agent.ask_jarvis("send the evening nudge", "heartbeat", scope="heartbeat", trigger=wake)
    tick, wake_row = rows("role = 'system'")
    assert (tick["scope"], tick["source"], tick["meta"]) == ("heartbeat", "heartbeat", None)
    assert wake_row["source"] == "wake"
    assert json.loads(wake_row["meta"]) == {"trigger": "wake0001", "task": "step-challenge"}


def test_confirmation_result_is_a_system_input(use_llm):
    use_llm(FakeLLM([AIMessage(content="Deleted it.")]))
    agent.ask_jarvis("[Confirmed: delete notes.md]", "t_confirm", input_role="system")
    assert rows()[0]["role"] == "system"


def test_slash_command_and_notification_paths():
    import main
    from gateway.base import InboundMessage

    reply = asyncio.run(main.process_inbound_message(InboundMessage(
        user_id=1, chat_id=1, thread_id=OWNER_THREAD_ID, user_text="/help", channel="telegram")))
    owner, jarvis = rows("source = 'slash_command'")
    assert (owner["role"], owner["content"]) == ("owner", "/help")
    assert (jarvis["role"], jarvis["content"]) == ("jarvis", reply)

    record.notification("reminder", "Call the dentist", {"trigger": "rem00001"})
    sent = rows("role = 'jarvis_sent'")[0]
    assert (sent["source"], sent["content"]) == ("outbox", "Call the dentist")
    assert json.loads(sent["meta"]) == {"trigger": "rem00001", "event": "reminder"}


def test_store_failure_never_reaches_the_turn(use_llm, monkeypatch):
    def broken():
        raise OSError("database is locked")

    monkeypatch.setattr(store, "write", broken)
    use_llm(tool_turn())
    out = agent.ask_jarvis("hi", "t_broken")
    assert out.text == "Your memory has a reading list."


# --- Content hygiene -------------------------------------------------------------

def test_scrub_hides_credentials_and_keeps_ids():
    text = ("key AIzaSyA1234567890abcdefghijklmnopqrstuv and ghp_" + "a" * 36 +
            ' {"password": "hunter22", "api_key": "abcdef123456", "input_tokens": 123456}'
            " Authorization: Bearer abcdefghijklmnop1234 order 9f86d081884c7d659a2feaa0c55ad015")
    out = store.scrub(text)
    assert "AIza" not in out and "ghp_" not in out and "hunter22" not in out
    assert "abcdef123456" not in out and "abcdefghijklmnop1234" not in out
    assert '"password": "[redacted]"' in out and "Bearer [redacted]" in out
    assert '"input_tokens": 123456' in out, "token counts are not secrets"
    assert "9f86d081884c7d659a2feaa0c55ad015" in out, "hashes and IDs are kept"


def test_oversized_content_is_cut():
    with store.write() as con:
        store.insert(con, role="tool_result", source="chat", content="x" * (store.MAX_CONTENT_CHARS + 500))
    content = rows()[0]["content"]
    assert content.endswith("[… 500 more chars cut]")
    assert len(content) < store.MAX_CONTENT_CHARS + 100


# --- search_history --------------------------------------------------------------

def seed(ts, turn_id, scope, entries, source="chat"):
    with store.write() as con:
        for role, content, *tool in entries:
            store.insert(con, ts=ts, turn_id=turn_id, thread_id=OWNER_THREAD_ID, scope=scope,
                         role=role, source=source, tool_name=tool[0] if tool else None, content=content)


def test_search_finds_a_tool_result_with_its_conversation():
    seed("2026-10-12T07:00:00+00:00", "turn-a", "user", [
        ("owner", "plan the Lisbon trip"),
        ("tool_call", '{"city": "Lisbon"}', "manage_itinerary"),
        ("tool_result", "Day 2: Alfama walking tour, 10:00, booked", "manage_itinerary"),
        ("jarvis", "Itinerary saved."),
    ])
    seed("2026-10-13T07:00:00+00:00", "turn-b", "user", [("owner", "hello"), ("jarvis", "hi")])
    out = search_history.invoke({"query": "alfama"})
    assert "Alfama walking tour" in out
    assert "You: plan the Lisbon trip" in out and "Jarvis: Itinerary saved." in out
    assert "hello" not in out
    assert "2026-10-12 10:00" in out, "times are Israel time"


def test_search_prefix_match_and_any_term_fallback():
    seed("2026-10-12T07:00:00+00:00", "turn-w", "user", [("owner", "I walked 9000 steps"), ("jarvis", "great")])
    assert "walked 9000" in search_history.invoke({"query": "walk"}), "prefix match"
    assert "walked 9000" in search_history.invoke({"query": "walk zebra"}), "falls back to any term"
    assert search_history.invoke({"query": "zebra"}).startswith("Nothing found")


def test_search_kinds_and_empty_query():
    seed("2026-10-12T07:00:00+00:00", "turn-c", "user", [("owner", "coffee chat"), ("jarvis", "sure")])
    seed("2026-10-12T08:00:00+00:00", "turn-h", "heartbeat", [("system", "tick coffee"), ("jarvis", "done")],
         source="heartbeat")
    record.notification("heartbeat", "coffee reminder sent", {})
    assert "tick coffee" not in search_history.invoke({"query": "coffee", "kind": "chat"})
    assert "coffee chat" not in search_history.invoke({"query": "coffee", "kind": "background"})
    notes = search_history.invoke({"query": "coffee", "kind": "notifications"})
    assert "Sent: coffee reminder sent" in notes and "coffee chat" not in notes
    newest = search_history.invoke({})
    assert newest.index("coffee reminder sent") < newest.index("coffee chat"), "newest first"
    assert search_history.invoke({"kind": "everything"}).startswith("Unknown kind")


def test_search_hebrew():
    seed("2026-10-12T07:00:00+00:00", "turn-he", "user", [("owner", "תזכיר לי לקנות חלב"), ("jarvis", "בטח")])
    assert "לקנות חלב" in search_history.invoke({"query": "חלב"})


def test_search_history_replaces_the_old_history_tools():
    core = {t.name for t in registry.get_tools("user", set())}
    assert "search_history" in core
    assert not {"get_chat_history", "get_notification_history"} & core


# --- Today view ------------------------------------------------------------------

def ack(ts, turn_id, acted, notify, summary, source="heartbeat", task=None):
    with store.write() as con:
        store.insert(con, ts=ts, turn_id=turn_id, scope="heartbeat", role="system", source=source,
                     meta={"task": task} if task else None, content="tick")
        store.insert(con, ts=ts, turn_id=turn_id, scope="heartbeat", role="tool_call", source=source,
                     tool_name="heartbeat_respond",
                     content=json.dumps({"acted_tasks": acted, "notify": notify, "summary": summary}))


def test_today_view_lists_what_the_heartbeat_did_today():
    now = datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc)  # 17:00 Israel
    ack("2026-10-15T06:00:00+00:00", "y", ["daily-log"], False, "yesterday's log")
    ack("2026-10-16T05:00:00+00:00", "a", ["morning-readiness-check"], True, "sent the readiness briefing")
    ack("2026-10-16T06:00:00+00:00", "b", [], False, "nothing due")
    ack("2026-10-16T10:00:00+00:00", "c", [], True, "sent mid-day nudge", source="wake", task="step-challenge")
    out = today.background_today(now)
    assert out.splitlines() == [
        "--- Today in the background (heartbeat) ---",
        "- 08:00 morning-readiness-check: sent the readiness briefing [messaged you]",
        "- 13:00 wake (step-challenge): sent mid-day nudge [messaged you]",
    ]


def test_today_view_is_empty_without_activity_and_in_both_prompts(use_llm):
    assert today.background_today() == ""
    ack(datetime.now(timezone.utc).isoformat(), "x", ["inbox-check"], False, "inbox cleared")
    assert "inbox cleared" in agent.build_system_prompt("user", set(), None)
    assert "inbox cleared" in agent.build_system_prompt("heartbeat", set(), []), \
        "a tick keeps no conversation; the today view tells it what earlier ticks did"


# --- Day view --------------------------------------------------------------------

def test_day_view_shows_background_and_the_conversation():
    ack("2026-10-15T06:00:00+00:00", "y", ["step-check"], True, "sent a step nudge")
    ack("2026-10-15T07:00:00+00:00", "n", [], False, "nothing due")
    with store.write() as con:
        store.insert(con, ts="2026-10-15T06:00:01+00:00", role="jarvis_sent", source="outbox",
                     content="You're at 3,000 steps.")
        store.insert(con, ts="2026-10-15T09:00:00+00:00", turn_id="t", thread_id=OWNER_THREAD_ID,
                     scope="user", role="owner", source="chat", content="walked to work " + "x" * 300)
        store.insert(con, ts="2026-10-15T09:00:02+00:00", turn_id="t", thread_id=OWNER_THREAD_ID,
                     scope="user", role="jarvis", source="chat", content="a reply")
        store.insert(con, ts="2026-10-15T10:00:00+00:00", turn_id="s", thread_id=OWNER_THREAD_ID,
                     scope="user", role="owner", source="slash_command", content="/usage")
        store.insert(con, ts="2026-10-15T10:00:00+00:00", turn_id="s", thread_id=OWNER_THREAD_ID,
                     scope="user", role="jarvis", source="slash_command", content="usage reply")
        store.insert(con, ts="2026-10-16T09:00:00+00:00", turn_id="d", thread_id=OWNER_THREAD_ID,
                     scope="user", role="owner", source="chat", content="the next day")
    background, conversation = today.day_view(date(2026, 10, 15))
    assert background == ["09:00 step-check: sent a step nudge [messaged you]"]
    assert conversation[0] == "09:00 sent: You're at 3,000 steps."
    assert conversation[1].startswith("12:00 Roi: walked to work x") and conversation[1].endswith("…")
    assert len(conversation[1]) - len("12:00 Roi: ") == today.DAY_TEXT_CAP
    assert conversation[2] == "12:00 Jarvis: a reply"
    assert len(conversation) == 3, "no slash commands, quiet ticks or other days"
