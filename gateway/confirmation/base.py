"""
Confirmation contracts — Plane 3 of the gateway (destructive tool -> user -> action).

See docs/architecture/GATEWAY.md ("Plane 3 — Confirmation"). The sync model is
deliberate and must be preserved: destructive tools run on sync worker threads and
cannot block on user input. `request_confirmation_sync` returns *immediately* with a
status string; the action runs later, only if the owner approves, and the outcome is
delivered out-of-band.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Awaitable, Callable


class ConfirmationOutcome(str, Enum):
    """What a resolved confirmation turned out to be, independent of any
    channel's rendered text — a channel whose native protocol tracks
    resolution state (rather than free-form prose) needs this instead of
    parsing outcome_text."""

    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    FAILED = "failed"  # confirmed, but the action itself raised
    EXPIRED = "expired"
    ALREADY_HANDLED = "already_handled"


@dataclass
class PendingAction:
    """A destructive action awaiting owner approval."""

    action_fn: Callable[[], Awaitable[str]]
    description: str
    result_ok_text: str
    result_cancel_text: str
    expires_at: datetime = field(
        default_factory=lambda: datetime.utcnow() + timedelta(minutes=5)
    )


class Confirmation(ABC):
    """Channel-agnostic confirmation entry point, called from sync tool workers."""

    @abstractmethod
    def request_confirmation_sync(
        self,
        description: str,
        action_fn: Callable[[], Awaitable[str]],
        result_ok_text: str = "Action completed.",
        result_cancel_text: str = "Action cancelled.",
    ) -> str:
        """Called from a sync tool worker thread. Registers the pending action,
        schedules the owner-facing prompt, and returns immediately with a status
        string for the LLM to relay. The action fires later iff the owner approves."""


class ConfirmationUI(ABC):
    """The only channel-specific half of Plane 3: rendering the prompt and outcome.

    The store owns bookkeeping, TTL eviction, and outcome dispatch; a channel
    implements just these two methods, rendering the prompt in whatever native
    UI it has (inline buttons, a reply, …).
    A channel may also expose a native callback handler that calls the store's
    resolve(callback_id, outcome).
    """

    @abstractmethod
    async def send_prompt(self, callback_id: str, description: str) -> None:
        """Render the confirm/cancel prompt to the owner."""

    @abstractmethod
    async def apply_outcome(
        self, callback_id: str, outcome: ConfirmationOutcome, outcome_text: str
    ) -> None:
        """Deliver the final state of a resolved or expired confirmation.
        `outcome` is the structured result (fixed vocabulary); `outcome_text`
        is the human-readable prose. Implementations read whichever fits their
        channel's wire — most read exactly one, and ignore the other. Must not
        raise: the store treats this as best-effort delivery and isolates each
        call so a failure here can never skip the conversational follow-up."""

    async def expire(self, callback_id: str) -> None:
        """Retire a prompt whose pending action was TTL-evicted before resolution.

        Default: deliver a generic expiry outcome. Channels with cheaper
        cleanup paths may override.
        """
        await self.apply_outcome(
            callback_id, ConfirmationOutcome.EXPIRED, "⌛ Confirmation expired."
        )
