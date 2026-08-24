"""
AppConfirmationUI — the jarvis-app half of Plane 3: send a confirmation block,
and keep the hub's wire-level state in sync with what the store decided.

Unlike Telegram, this channel has no way to rewrite a message's free-form text
after the fact — its only lever is PATCHing the block's `state` to one of
confirmed/cancelled/expired. apply_outcome reads only `outcome` (the structured
result) and ignores `outcome_text`, the prose half a text-rendering channel
would use instead. Resolution flows the other way through handle_action, the
router's entry point for an ActionUpdate.

`_message_ids` holds the prompt id from send time (the TTL-expiry path has no
tap to read one from); `_resolved` holds what this process settled a block to.
Neither survives a restart and neither has to — a tap names its own message_id.
"""

import logging
from collections import OrderedDict

from gateway.confirmation.base import ConfirmationOutcome, ConfirmationUI
from gateway.channels.jarvis_app.client import HubClient, MessageAlreadyResolved

logger = logging.getLogger(__name__)

# Maps a resolved confirmation to the hub's wire vocabulary for a confirmation
# block's `state`. FAILED still means the tap itself was honored (the owner
# confirmed) — the wire has no separate "the action then failed" state, only
# what the human decided. ALREADY_HANDLED is absent: it names the absence of a
# pending action, not an outcome, so its state is resolved per call below.
_WIRE_STATE: dict[ConfirmationOutcome, str] = {
    ConfirmationOutcome.CONFIRMED: "confirmed",
    ConfirmationOutcome.FAILED: "confirmed",
    ConfirmationOutcome.CANCELLED: "cancelled",
    ConfirmationOutcome.EXPIRED: "expired",
}

# A bound on the re-affirmation memory, not an expected eviction.
_RESOLVED_MEMORY = 64


class AppConfirmationUI(ConfirmationUI):
    def __init__(self, client: HubClient) -> None:
        self._client = client
        self._store = None  # set via bind_store() to break the construction cycle
        self._message_ids: dict[str, int] = {}  # callback_id -> prompt message_id
        # callback_id -> the wire state this process settled it to.
        self._resolved: "OrderedDict[str, str]" = OrderedDict()

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
        wire_state = self._wire_state_for(callback_id, outcome)
        try:
            await self._client.patch_message_state(message_id, wire_state)
        except MessageAlreadyResolved as e:
            logger.warning(
                "jarvis-app PATCH state=%s for callback %s lost to already-settled state=%s",
                wire_state, callback_id, e.state,
            )
            # Record what stands, not what we attempted, so a further tap agrees.
            self._remember_resolved(callback_id, e.state)
        except Exception:
            logger.exception("jarvis-app PATCH state failed for callback %s", callback_id)
        else:
            self._remember_resolved(callback_id, wire_state)

    def _wire_state_for(
        self, callback_id: str, outcome: ConfirmationOutcome
    ) -> str:
        """The `state` to PATCH. ALREADY_HANDLED means nothing was pending —
        two cases the store cannot separate but this side can: a block this
        process already settled (re-affirm it; the hub refuses a change), or a
        handle that was never ours (expire it, or the card waits forever).
        """
        if outcome is not ConfirmationOutcome.ALREADY_HANDLED:
            return _WIRE_STATE[outcome]
        return self._resolved.get(callback_id, "expired")

    def _remember_resolved(self, callback_id: str, state: str) -> None:
        self._resolved[callback_id] = state
        self._resolved.move_to_end(callback_id)
        while len(self._resolved) > _RESOLVED_MEMORY:
            self._resolved.popitem(last=False)

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
        # The tap names its own message — the only handle a restart cannot lose.
        self._message_ids[callback_id] = message_id
        await self._store.resolve(callback_id, confirmed=(action_id == "confirm"))
