"""
HubClient — httpx client for the jarvis-app hub's bot API.

Talks to the hub the way python-telegram-bot talks to Telegram's Bot API:
long-poll for updates, POST replies. Everything hub-specific — endpoints, bearer
auth, and the contract version this adapter was written against — lives here; the
channel and router deal in Python objects, never URLs.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# The version the adapter was written against. The hub reports its own
# contract_version on GET /v1/health; a mismatch is logged, never fatal — the hub
# already 422s a malformed payload, so this only gives a *silent* skew a voice.
PINNED_CONTRACT_VERSION = "3b3a48f330f09a39"

# The hanging poll's server-side wait. The client read timeout sits a little above
# it so a genuinely dead connection eventually errors instead of hanging forever.
POLL_TIMEOUT_S = 25.0


class HubUnavailable(Exception):
    """The hub could not be reached or returned a server error — raised so the
    router enters degraded mode instead of the fetch loop dying."""


class HubClient:
    def __init__(
        self, base_url: str, token: str, *, poll_timeout: float = POLL_TIMEOUT_S
    ) -> None:
        self._poll_timeout = poll_timeout
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            # Connect fails fast; the read window covers the long-poll plus slack.
            timeout=httpx.Timeout(10.0, read=poll_timeout + 5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_health(self) -> dict:
        """The hub's self-report, including contract_version. Not wrapped in
        HubUnavailable — its one caller is a startup check that treats any failure
        as 'skip the check', not 'degrade'."""
        r = await self._client.get("/v1/health")
        r.raise_for_status()
        return r.json()

    async def get_updates(self, offset: int) -> list[dict]:
        """Long-poll for updates at or after `offset`. Raises HubUnavailable on a
        server error or transport failure so the fetch loop can back off."""
        try:
            r = await self._client.get(
                "/bot/v1/updates",
                params={"offset": offset, "timeout": self._poll_timeout},
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise HubUnavailable(f"hub returned {e.response.status_code}") from e
            raise
        except httpx.HTTPError as e:
            raise HubUnavailable(str(e)) from e
        return r.json()

    async def send_message(self, body: dict) -> None:
        """POST an assistant message to the hub. `body` carries any of text /
        attachment_ids (the hub requires at least one)."""
        r = await self._client.post("/bot/v1/messages", json=body)
        r.raise_for_status()

    async def upload_attachment(
        self, payload: bytes, *, filename: str, mime_type: str
    ) -> str:
        """Upload one blob and return its `att_…` id, to be referenced by a later
        send_message(attachment_ids=[…]). The hub infers kind from the mime type.
        Raises HubUnavailable on a server error or transport failure so a proactive
        media send degrades to a failed send rather than crashing the caller."""
        try:
            r = await self._client.post(
                "/bot/v1/attachments",
                files={"file": (filename, payload, mime_type)},
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise HubUnavailable(f"hub returned {e.response.status_code}") from e
            raise
        except httpx.HTTPError as e:
            raise HubUnavailable(str(e)) from e
        return r.json()["id"]

    async def download_attachment(self, attachment_id: str) -> bytes:
        """Fetch an inbound attachment's bytes by id. Raises HubUnavailable on a
        server error or transport failure so the router can skip that one
        attachment without taking the turn down."""
        try:
            r = await self._client.get(f"/bot/v1/attachments/{attachment_id}")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise HubUnavailable(f"hub returned {e.response.status_code}") from e
            raise
        except httpx.HTTPError as e:
            raise HubUnavailable(str(e)) from e
        return r.content

    async def declare_commands(self, commands: list[dict]) -> None:
        """Publish the bot's slash-command list to the hub so the app can show a
        command menu. Each entry is {"name", "description"}."""
        r = await self._client.post("/bot/v1/commands", json=commands)
        r.raise_for_status()
