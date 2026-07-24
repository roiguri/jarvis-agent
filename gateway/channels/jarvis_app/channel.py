"""
JarvisAppChannel — the jarvis-app implementation of the Channel contract.

A thin adapter over HubClient. The hub is one-bot-one-owner: there is a single
conversation, so `chat_id` carries no routing and every send addresses the owner.
Text only for now; media sends raise NotImplementedError (the Outbox reports that
as a failed send) until the media step lands.
"""

from __future__ import annotations

import logging

from gateway.base import Channel
from gateway.channels.jarvis_app.client import HubClient

logger = logging.getLogger(__name__)


# thread_id mirrors telegram's "<channel>_<id>", parsed on the first underscore.
# The channel name contains no underscore, so the prefix stays unambiguous.
def thread_id_for(owner_id: str) -> str:
    return f"jarvis-app_{owner_id}"


class JarvisAppChannel(Channel):
    name = "jarvis-app"

    def __init__(self, client: HubClient, owner_id: str) -> None:
        self._client = client
        self._owner_id = owner_id

    # ------------------------------------------------------------------
    # Channel ABC
    # ------------------------------------------------------------------

    async def send(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
        # One owner, one conversation — chat_id is not a routing key here.
        await self._client.send_message({"text": text})

    async def send_media(
        self, chat_id: str, kind: str, payload: bytes, caption: str | None = None
    ) -> None:
        raise NotImplementedError(f"jarvis-app cannot send media kind={kind!r} yet")

    async def send_to_owner(self, text: str) -> None:
        await self._client.send_message({"text": text})

    async def send_to_owner_media(
        self, kind: str, payload: bytes, caption: str | None = None
    ) -> None:
        raise NotImplementedError(f"jarvis-app cannot send media kind={kind!r} yet")

    def authorize(self, raw_user_id: str) -> bool:
        # The bot token scopes the hub to the single owner, so inbound updates are
        # already authorized upstream; this checks against the configured owner
        # for completeness.
        return str(raw_user_id) == self._owner_id

    @property
    def owner_thread_id(self) -> str:
        return thread_id_for(self._owner_id)
