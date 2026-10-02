"""How a turn ends, for every scope: the outcome vocabulary, the tool calls a
turn committed on the way, and the honest wording when it does not finish.

The runtime (``agent.ask_jarvis``) classifies; callers only decide where the
text goes — a user turn replies with it, a heartbeat tick turns a failure into
an owner notice. Scope differences belong here as data, never as branches in
the loop.
"""

import contextvars
from collections import Counter
from dataclasses import dataclass, field

COMPLETED = "completed"
WRAPPED_UP = "wrapped_up"
BUDGET_EXHAUSTED = "budget_exhausted"
FAILED = "failed"


@dataclass(frozen=True)
class TurnOutcome:
    kind: str
    # Owner-presentable; may be "" for a legitimately quiet turn.
    text: str
    # Why the turn did not complete normally, technical (logs); None when it did.
    reason: str | None = None
    # The same, in plain language for the owner; None when it completed.
    cause: str | None = None
    # (tool name, count) for every tool call that succeeded this turn.
    committed_calls: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    @property
    def finished(self) -> bool:
        return self.kind in (COMPLETED, WRAPPED_UP)


# Successful tool calls this turn, by name. Set per turn by ask_jarvis; the
# tool node records into it. Its effects are already saved when a later step
# fails, which is exactly what the owner and the next turn must be told.
_COMMITTED: contextvars.ContextVar[Counter | None] = contextvars.ContextVar(
    "turn_committed_calls", default=None
)


def begin_turn() -> contextvars.Token:
    return _COMMITTED.set(Counter())


def end_turn(token: contextvars.Token) -> None:
    _COMMITTED.reset(token)


def record_committed(tool_name: str) -> None:
    calls = _COMMITTED.get()
    if calls is not None:
        calls[tool_name] += 1


def committed_calls() -> tuple[tuple[str, int], ...]:
    calls = _COMMITTED.get()
    return tuple(calls.most_common()) if calls else ()


_SUMMARY_MAX_NAMES = 6


def committed_summary(calls: tuple[tuple[str, int], ...]) -> str:
    parts = [f"{name} ×{n}" if n > 1 else name for name, n in calls[:_SUMMARY_MAX_NAMES]]
    rest = len(calls) - _SUMMARY_MAX_NAMES
    if rest > 0:
        parts.append(f"{rest} other tool{'s' if rest > 1 else ''}")
    return ", ".join(parts)


def describe_error(exc: BaseException) -> str:
    """A plain-language cause. Upstream conditions are told apart from our own
    faults because they call for different reactions: retry later vs. report."""
    text = f"{type(exc).__name__}: {exc}"
    if any(s in text for s in ("503", "UNAVAILABLE", "502", "Bad Gateway", "high demand")):
        return "the model service was unavailable (overloaded upstream)"
    if any(s in text for s in ("504", "DEADLINE_EXCEEDED", "Timeout", "timed out")):
        return "the model took too long to respond"
    if any(s in text for s in ("429", "RESOURCE_EXHAUSTED")):
        return "the model quota was exhausted"
    if any(s in text for s in ("403", "PERMISSION_DENIED")):
        return "the model service refused the API credentials"
    return f"an internal error on my side ({type(exc).__name__})"


def committed_sentence(calls: tuple[tuple[str, int], ...]) -> str:
    if not calls:
        return "Nothing was changed before it stopped."
    return (
        f"Before it stopped I had already run: {committed_summary(calls)}. "
        "Those changes are saved — check them before asking me to redo anything."
    )


def failure_text(cause: str, calls: tuple[tuple[str, int], ...]) -> str:
    """The owner-facing reply for a turn that did not finish."""
    return f"I couldn't finish that: {cause}. {committed_sentence(calls)}"


def failure_note(cause: str, calls: tuple[tuple[str, int], ...]) -> str:
    """Written into the thread so the next turn knows what already landed."""
    done = (
        f" Tool calls that completed before it stopped: {committed_summary(calls)} — "
        "their effects are saved; do not re-run them, check current state first."
        if calls else " No tool call completed."
    )
    return f"[The previous turn did not finish: {cause}.{done}]"
