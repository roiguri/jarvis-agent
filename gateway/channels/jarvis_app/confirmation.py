"""
AppConfirmationUI — the jarvis-app half of Plane 3: send a confirmation block,
and keep the hub's wire-level state in sync with what the store decided.

Unlike Telegram, this channel has no way to rewrite a message's free-form text
after the fact — its only lever is PATCHing the block's `state` to one of
confirmed/cancelled/expired. apply_outcome reads only `outcome` (the structured
result) and ignores `outcome_text`, the prose half a text-rendering channel
would use instead. Resolution flows the other way through handle_action, the
router's entry point for an ActionUpdate.
"""

import logging

from gateway.confirmation.base import ConfirmationOutcome, ConfirmationUI
from gateway.channels.jarvis_app.client import HubClient, MessageAlreadyResolved

logger = logging.getLogger(__name__)

# Maps a resolved confirmation to the hub's wire vocabulary for a confirmation
# block's `state`. FAILED still means the tap itself was honored (the owner
# confirmed) — the wire has no separate "the action then failed" state, only
# what the human decided. ALREADY_HANDLED maps to None: this process no longer
# knows which value it originally intended, and the hub's own terminal-state
# guard is what actually protects the stored value either way.
_WIRE_STATE: dict[ConfirmationOutcome, str | None] = {
    ConfirmationOutcome.CONFIRMED: "confirmed",
    ConfirmationOutcome.FAILED: "confirmed",
    ConfirmationOutcome.CANCELLED: "cancelled",
    ConfirmationOutcome.EXPIRED: "expired",
    ConfirmationOutcome.ALREADY_HANDLED: None,
}


class AppConfirmationUI(ConfirmationUI):
    def __init__(self, client: HubClient) -> None:
        self._client = client
        self._store = None  # set via bind_store() to break the construction cycle
        self._message_ids: dict[str, int] = {}  # callback_id -> prompt message_id

    def bind_store(self, store) -> None:
        self._store = store

    async def send_prompt(self, callback_id: str, description: str) -> None:
        # summary == payload.body verbatim: the architecture's own canonical
        # example does the same — the confirmation's question has one source
        # of truth, restated only where the wire's block envelope requires it.
        message = await self._client.send_message({
            "blocks": [{
                "kind": "confirmation",
                "summary": description,
                "payload": {"callback_id": callback_id, "body": description},
            }]
        })
        self._message_ids[callback_id] = message["id"]

    async def apply_outcome(
        self, callback_id: str, outcome: ConfirmationOutcome, outcome_text: str
    ) -> None:
        message_id = self._message_ids.pop(callback_id, None)
        if message_id is None:
            return  # prompt was never successfully sent — nothing to PATCH
        wire_state = _WIRE_STATE[outcome]
        if wire_state is None:
            return
        try:
            await self._client.patch_message_state(message_id, wire_state)
        except MessageAlreadyResolved as e:
            logger.warning(
                "jarvis-app PATCH state=%s for callback %s lost to already-settled state=%s",
                wire_state, callback_id, e.state,
            )
        except Exception:
            logger.exception("jarvis-app PATCH state failed for callback %s", callback_id)

    async def handle_action(
        self, *, action_id: str, message_id: int, block_kind: str, callback_id: str | None
    ) -> None:
        """Router entry point for an ActionUpdate — mirrors
        TelegramConfirmationUI.handle_callback. Confirmation is the only kind
        with a registered below-LLM resolution today; any other block_kind
        (buttons/card/form) is Stage D territory and falls through."""
        if block_kind != "confirmation":
            logger.info(
                "jarvis-app ignoring action for block_kind=%r (no handler yet)", block_kind
            )
            return
        if callback_id is None:
            logger.warning(
                "jarvis-app confirmation action with no callback_id (message_id=%s) — "
                "contract violation, ignoring",
                message_id,
            )
            return
        if self._store is None:
            logger.error("Confirmation store not bound; cannot resolve %s", callback_id)
            return
        await self._store.resolve(callback_id, confirmed=(action_id == "confirm"))
