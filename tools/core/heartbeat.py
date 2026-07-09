"""Heartbeat management tools.

``heartbeat_respond`` is the structured end-of-tick acknowledgement: the
heartbeat runner reads its payload (which tasks were acted on, whether Roi
should be notified) rather than inferring the tick's outcome from reply
text. Bound only in heartbeat scope — a user turn has no tick to
acknowledge.
"""

from langchain_core.tools import tool

from tools.registry import tool_register


@tool_register(namespace="core", scopes=("heartbeat",))
@tool
def heartbeat_respond(
    acted_tasks: list[str],
    notify: bool,
    summary: str,
    notification_text: str = "",
) -> dict:
    """Report the outcome of this heartbeat tick. Call exactly once, as your
    last tool call of the tick, after all task work is done.

    Args:
        acted_tasks: Exact names (from HEARTBEAT.md) of every task you acted
            on this tick — completed its work and updated its state file.
            Empty list if nothing was due or nothing was done. Do NOT list
            tasks you only checked and skipped.
        notify: True only if Roi should receive a message from this tick.
        summary: One line for the internal log — what this tick did (or why
            nothing was done). Always required.
        notification_text: The user-facing message, required when notify is
            True. Ignored when notify is False. Defaults to summary.
    """
    acted = [name.strip() for name in acted_tasks if name and name.strip()]
    payload = {
        "acted_tasks": acted,
        "notify": bool(notify),
        "summary": summary.strip(),
        "notification_text": (notification_text or summary).strip(),
    }
    return payload
