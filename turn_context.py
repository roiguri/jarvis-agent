"""Ambient per-turn context shared between the runtime layer and tool bodies.

Tool functions receive only their own (model-filled) arguments, so anything a
tool must know about the turn it runs in — and must not trust the model to
declare — is published here by ``ask_jarvis`` via ContextVars. Each
``asyncio.to_thread(ask_jarvis, ...)`` call runs in its own context copy, so
concurrent user and heartbeat turns never see each other's values.

This module deliberately imports nothing from the app: it must be importable
by ``agent.py`` (which sets values) and by any tool module (which reads them)
without creating a cycle.
"""

from contextvars import ContextVar

# "user" | "heartbeat" for the running turn; None outside a turn. Readers
# should treat None as "user" — the most conservative default for guards
# that restrict what background turns may do.
CURRENT_SCOPE: ContextVar[str | None] = ContextVar("current_scope", default=None)

# The running turn's conversation thread id (e.g. "telegram_42"); None outside a
# turn. Lets tool bodies discover which channel a turn originated on without the
# model declaring it — e.g. a destructive tool's confirmation resolves to the
# origin channel's handler. Set by ask_jarvis alongside CURRENT_SCOPE.
CURRENT_THREAD_ID: ContextVar[str | None] = ContextVar("current_thread_id", default=None)


def current_scope() -> str:
    """The running turn's scope, defaulting to 'user' outside a turn."""
    return CURRENT_SCOPE.get() or "user"


def current_thread_id() -> str | None:
    """The running turn's thread id, or None outside a turn."""
    return CURRENT_THREAD_ID.get()
