"""What gets written to the episode store, and when.

- ``turn_input`` at the start of a turn: the owner's message (or a tick or wake
  instruction, or a confirmation result), so a turn that crashes still keeps it.
- ``turn_steps`` at the end of a turn: every model step of the turn — reply
  text, each tool call with its arguments, each tool result — read from the
  final conversation state, so failed and cut-short turns are recorded too.
- ``turn_failure`` for a background turn that did not finish.
- ``notification`` for every delivered proactive send.
- ``slash_command`` for a command and its reply (they never reach the model).

Every writer is guarded: a failed write is logged and dropped, never raised
into a turn. Telemetry's turn_id joins the two stores.
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from episodes import store

logger = logging.getLogger(__name__)


def _guarded(what: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.exception("episode store write failed: %s", what)


def _text(content) -> str:
    """Text of a message's content; media blocks are left out (the input row
    carries their file paths instead)."""
    if isinstance(content, str):
        return content
    return "".join(
        (block.get("text") or "") if isinstance(block, dict) else str(block)
        for block in content or []
        if not isinstance(block, dict) or block.get("type", "text") == "text"
    )


def turn_input(*, turn_id: str, thread_id: str, scope: str, channel: str | None,
               role: str, source: str, content: str, attachments: list[str] | None = None,
               meta: dict | None = None, ts: str | None = None) -> None:
    def write():
        # Each attachment is its file marker, one per line after the message.
        text = "\n".join(([content] if content else []) + list(attachments or []))
        with store.write() as con:
            store.insert(con, ts=ts or store.now_iso(), turn_id=turn_id, thread_id=thread_id,
                         scope=scope, channel=channel, role=role, source=source,
                         meta=meta, content=text)
    _guarded("turn input", write)


def turn_steps(*, turn_id: str, thread_id: str, scope: str, channel: str | None,
               source: str, messages: list) -> None:
    """Record this turn's model steps. ``messages`` is the turn's slice of the
    thread (from its first input message on); input messages are skipped —
    the input row was written at the start, and a forwarded-notification block
    duplicates rows ``notification`` already wrote."""
    def write():
        common = dict(turn_id=turn_id, thread_id=thread_id, scope=scope, channel=channel, source=source)
        ts = store.now_iso()
        with store.write() as con:
            for m in messages:
                if isinstance(m, HumanMessage):
                    continue
                if isinstance(m, AIMessage):
                    text = _text(m.content)
                    if text.strip():
                        store.insert(con, ts=ts, role="jarvis", content=text, **common)
                    for call in m.tool_calls or []:
                        store.insert(con, ts=ts, role="tool_call", tool_name=call.get("name"),
                                     meta={"call_id": call.get("id")},
                                     content=json.dumps(call.get("args") or {}, ensure_ascii=False, default=str),
                                     **common)
                elif isinstance(m, ToolMessage):
                    meta = {"call_id": m.tool_call_id}
                    if getattr(m, "status", None) == "error":
                        meta["status"] = "error"
                    store.insert(con, ts=ts, role="tool_result", tool_name=m.name, meta=meta,
                                 content=_text(m.content), **common)
    _guarded("turn steps", write)


def turn_failure(*, turn_id: str, thread_id: str, scope: str, source: str, cause: str,
                 ts: str | None = None) -> None:
    """A background turn that did not finish: its cause, so the next turn's
    today view shows it (a heartbeat turn has no thread to carry a note)."""
    def write():
        with store.write() as con:
            store.insert(con, ts=ts or store.now_iso(), turn_id=turn_id, thread_id=thread_id,
                         scope=scope, role="system", source=source, meta={"failed": True},
                         content=f"Did not finish: {cause}")
    _guarded("turn failure", write)


def notification(event: str, text: str, metadata: dict | None = None) -> None:
    def write():
        meta = dict(metadata or {})
        meta["event"] = event
        with store.write() as con:
            store.insert(con, role="jarvis_sent", source="outbox", meta=meta, content=text)
    _guarded("notification", write)


def slash_command(*, thread_id: str, channel: str | None, command: str, reply: str) -> None:
    def write():
        ts = store.now_iso()
        common = dict(ts=ts, thread_id=thread_id, scope="user", channel=channel, source="slash_command")
        with store.write() as con:
            store.insert(con, role="owner", content=command, **common)
            store.insert(con, role="jarvis", content=reply, **common)
    _guarded("slash command", write)
