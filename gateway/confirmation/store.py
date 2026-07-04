"""
Channel-agnostic confirmation store.

Owns all bookkeeping for Plane 3: the pending-action table, TTL eviction, running
the action on approval, and dispatching the outcome. It delegates rendering to a
ConfirmationUI and posts a final outcome line to the owner via the Channel. It
does not know about Telegram, inline keyboards, or the agent.
"""

import asyncio
import concurrent.futures
import logging
import threading
from datetime import datetime
import uuid
from typing import Awaitable, Callable

from gateway.base import Channel
from gateway.confirmation.base import Confirmation, ConfirmationUI, PendingAction

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 60


class InMemoryConfirmationStore(Confirmation):
    """In-process confirmation registry shared by all destructive tools.

    Threading model: ``request_confirmation_sync`` is called from a sync tool
    worker thread and must not block. It registers the action and schedules the
    UI prompt onto the channel's event loop, returning a status string at once.
    ``resolve`` runs later on the event loop when the owner answers.
    """

    def __init__(
        self,
        ui: ConfirmationUI,
        channel: Channel,
        on_outcome: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._ui = ui
        self._channel = channel
        # Domain callback that turns a neutral outcome line into a
        # conversational acknowledgement (runs the agent + replies). Injected
        # by the host so the gateway never imports the agent layer. If unset,
        # the outcome is posted to the owner verbatim instead.
        self._on_outcome = on_outcome
        self._pending: dict[str, PendingAction] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sweeper: asyncio.Task | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running event loop. Called once at startup, from inside
        the loop, before any inbound traffic is accepted."""
        self._loop = loop

    def start_sweeper(self) -> None:
        """Begin the periodic TTL eviction task. Requires a bound loop."""
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_loop())

    # ------------------------------------------------------------------
    # Confirmation ABC
    # ------------------------------------------------------------------

    def request_confirmation_sync(
        self,
        description: str,
        action_fn: Callable[[], Awaitable[str]],
        result_ok_text: str = "Action completed.",
        result_cancel_text: str = "Action cancelled.",
    ) -> str:
        if self._loop is None:
            return "Error: confirmation system not ready, cannot request approval."

        callback_id = uuid.uuid4().hex[:8]
        with self._lock:
            self._pending[callback_id] = PendingAction(
                action_fn=action_fn,
                description=description,
                result_ok_text=result_ok_text,
                result_cancel_text=result_cancel_text,
            )

        future = asyncio.run_coroutine_threadsafe(
            self._ui.send_prompt(callback_id, description), self._loop
        )
        future.add_done_callback(
            lambda fut: self._on_prompt_done(callback_id, description, fut)
        )
        return (
            f"Confirmation request sent. Awaiting your approval to: {description}"
        )

    def _on_prompt_done(
        self,
        callback_id: str,
        description: str,
        future: "concurrent.futures.Future[None]",
    ) -> None:
        """Backstop for send_prompt failures.

        If the UI couldn't deliver the prompt, the owner will never see a button
        and the pending action would otherwise sit until TTL eviction while the
        tool already returned an optimistic 'awaiting approval' string. Drop the
        pending action and notify the owner that nothing was scheduled.
        """
        try:
            exc = future.exception()
        except concurrent.futures.CancelledError:
            exc = None
        if exc is None:
            return
        logger.exception(
            "Confirmation prompt delivery failed for %s", callback_id, exc_info=exc
        )
        with self._lock:
            self._pending.pop(callback_id, None)
        notice = (
            f"[System: The confirmation prompt could not be delivered. "
            f"Task: {description}. Action was NOT scheduled.]"
        )
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(
                self._deliver_outcome(notice), self._loop
            )

    # ------------------------------------------------------------------
    # Resolution — called on the event loop by the channel's callback handler
    # ------------------------------------------------------------------

    async def resolve(self, callback_id: str, confirmed: bool) -> None:
        with self._lock:
            action = self._pending.pop(callback_id, None)

        if action is None:
            await self._ui.edit_outcome(
                callback_id,
                "⚠️ This confirmation has already been handled or has expired.",
            )
            return

        desc = action.description
        if not confirmed:
            await self._ui.edit_outcome(callback_id, f"❌ {action.result_cancel_text}")
            await self._deliver_outcome(
                f"[System: The user cancelled the requested action. Task: {desc}]"
            )
            return

        try:
            result = await action.action_fn()
            await self._ui.edit_outcome(
                callback_id, f"✅ {action.result_ok_text}\n{result}"
            )
            feedback = (
                f"[System: The user confirmed the requested action. "
                f"Task: {desc}. Result: {result}]"
            )
        except Exception as e:
            logger.exception("Confirmed action raised")
            await self._ui.edit_outcome(callback_id, f"❌ Action failed: {e}")
            feedback = (
                f"[System: The confirmed action failed. "
                f"Task: {desc}. Error: {e}]"
            )

        await self._deliver_outcome(feedback)

    async def _deliver_outcome(self, system_text: str) -> None:
        """Hand the neutral outcome to the domain for a conversational
        acknowledgement, or post it verbatim if no handler is wired."""
        try:
            if self._on_outcome is not None:
                await self._on_outcome(system_text)
            else:
                await self._channel.send_to_owner(system_text)
        except Exception:
            logger.exception("Failed to deliver confirmation outcome")

    # ------------------------------------------------------------------
    # TTL eviction
    # ------------------------------------------------------------------

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
                await self._evict_expired()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Confirmation TTL sweep failed")

    async def _evict_expired(self) -> None:
        now = datetime.utcnow()
        with self._lock:
            expired = [
                (k, v.description)
                for k, v in self._pending.items()
                if v.expires_at < now
            ]
            for k, _ in expired:
                del self._pending[k]
        if not expired:
            return
        logger.info("Evicted %d expired confirmation(s)", len(expired))
        for callback_id, desc in expired:
            try:
                await self._ui.expire(callback_id)
            except Exception:
                logger.exception("UI.expire failed for %s", callback_id)
            await self._deliver_outcome(
                f"[System: The confirmation request expired without a response. "
                f"Task: {desc}]"
            )
