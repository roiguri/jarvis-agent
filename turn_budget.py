"""How a turn ends, for every scope: the outcome vocabulary, the tool calls a
turn committed on the way, and the honest wording when it does not finish.

The runtime (``agent.ask_jarvis``) classifies; callers only decide where the
text goes — a user turn replies with it, a heartbeat tick turns a failure into
an owner notice. Scope differences belong here as data, never as branches in
the loop.
"""

import contextvars
from collections import Counter
from dataclasses import dataclass

COMPLETED = "completed"
WRAPPED_UP = "wrapped_up"
BUDGET_EXHAUSTED = "budget_exhausted"
FAILED = "failed"


@dataclass(frozen=True)
class TurnOutcome:
    kind: str
    # Owner-presentable; may be "" for a legitimately quiet turn.
    text: str
    # Why it did not complete normally, in plain language; None when it did.
    cause: str | None = None
    # (tool name, count) for every tool call that succeeded this turn.
    committed_calls: tuple[tuple[str, int], ...] = ()

    @property
    def finished(self) -> bool:
        return self.kind in (COMPLETED, WRAPPED_UP)


# Successful tool calls this turn, by name. Set per turn by ask_jarvis; the
# tool node records into it. Its effects are already saved when a later step
# fails, which is exactly what the owner and the next turn must be told.
COMMITTED: contextvars.ContextVar[Counter] = contextvars.ContextVar("turn_committed_calls")


_SUMMARY_MAX_NAMES = 6


def committed_summary(calls: tuple[tuple[str, int], ...]) -> str:
    parts = [f"{name} ×{n}" if n > 1 else name for name, n in calls[:_SUMMARY_MAX_NAMES]]
    rest = len(calls) - _SUMMARY_MAX_NAMES
    if rest > 0:
        parts.append(f"{rest} other tool{'s' if rest > 1 else ''}")
    return ", ".join(parts)


_STATUS_CAUSE = {
    502: "the model service was unavailable (overloaded upstream)",
    503: "the model service was unavailable (overloaded upstream)",
    504: "the model took too long to respond",
    429: "the model quota was exhausted",
    403: "the model service refused the API credentials",
}
# Fallback when no status code is attached: match status names, never digits.
_STATUS_NAMES = {
    503: ("UNAVAILABLE",),
    504: ("DEADLINE_EXCEEDED", "Timeout", "timed out"),
    429: ("RESOURCE_EXHAUSTED",),
    403: ("PERMISSION_DENIED",),
}


def describe_error(exc: BaseException) -> str:
    """A plain-language cause. Upstream conditions are told apart from our own
    faults because they call for different reactions: retry later vs. report.

    The provider's HTTP status wins when the error (or what it wraps) carries
    one; otherwise the message is matched, by status name, not bare digits."""
    err, code = exc, None
    while err is not None and not isinstance(code, int):
        code, err = getattr(err, "code", None), err.__cause__
    if not isinstance(code, int):
        text = str(exc)
        code = next((c for c, names in _STATUS_NAMES.items()
                     if any(n in text for n in names)), None)
    return _STATUS_CAUSE.get(code, f"an internal error on my side ({type(exc).__name__})")


def committed_sentence(calls: tuple[tuple[str, int], ...]) -> str:
    # Reads count too — the tool surface carries no read/write flag — so the
    # wording claims only what is true of every call: it ran.
    if not calls:
        return "No tool call had completed, so nothing was changed."
    return (
        f"Before it stopped I had already run: {committed_summary(calls)}. "
        "Anything those calls changed is saved — check before asking me to redo it."
    )


def failure_text(cause: str, calls: tuple[tuple[str, int], ...]) -> str:
    """The owner-facing reply for a turn that did not finish."""
    return f"I couldn't finish that: {cause}. {committed_sentence(calls)}"


def failure_note(cause: str, calls: tuple[tuple[str, int], ...]) -> str:
    """Written into the thread so the next turn knows what already landed.
    Scope-neutral: what the owner was told differs by scope."""
    ran = (
        f" Tool calls that completed: {committed_summary(calls)}. Any changes they "
        "made are saved — check current state before repeating a write."
        if calls else " No tool call completed."
    )
    return f"[This turn did not finish: {cause}.{ran}]"
