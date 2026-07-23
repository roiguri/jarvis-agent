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
from typing import Awaitable, Callable


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
    async def edit_outcome(self, callback_id: str, outcome_text: str) -> None:
        """Replace the prompt with the final outcome text."""

    async def expire(self, callback_id: str) -> None:
        """Retire a prompt whose pending action was TTL-evicted before resolution.

        Default: delegate to edit_outcome with a generic expiry message. Channels
        with cheaper cleanup paths may override.
        """
        await self.edit_outcome(callback_id, "⌛ Confirmation expired.")
