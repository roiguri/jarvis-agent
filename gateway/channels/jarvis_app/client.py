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
PINNED_CONTRACT_VERSION = "509c222e84e2c915"

# The hanging poll's server-side wait. The client read timeout sits a little above
# it so a genuinely dead connection eventually errors instead of hanging forever.
POLL_TIMEOUT_S = 25.0


class HubUnavailable(Exception):
    """The hub could not be reached or returned a server error — raised so the
    router enters degraded mode instead of the fetch loop dying."""


class MessageAlreadyResolved(Exception):
    """A PATCH tried to move a block's state to a value that differs from what
    the hub already has stored — the terminal-state guard refused it (wire
    error code "already_resolved"). Carries the state that actually stands, so
    the caller can log the settled value instead of a bare failure."""

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(f"block already resolved to {state!r}")


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

    async def send_message(self, body: dict) -> dict:
        """POST an assistant message to the hub and return the created Message
        (in particular its `id`, needed to later PATCH a block's state). `body`
        carries any of text / attachment_ids / blocks (the hub requires at
        least one)."""
        r = await self._client.post("/bot/v1/messages", json=body)
        r.raise_for_status()
        return r.json()

    async def patch_message_state(self, message_id: int, state: str) -> None:
        """PATCH a sent message's sole interactive block to `state` (e.g.
        confirmed/cancelled/expired for a confirmation). The hub's terminal-state
        guard makes re-sending the value already stored a harmless no-op; a
        PATCH to a *different* value than what is stored raises
        MessageAlreadyResolved carrying the value that stands. Raises
        HubUnavailable on a server error or transport failure."""
        try:
            r = await self._client.patch(
                f"/bot/v1/messages/{message_id}", json={"state": state}
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise HubUnavailable(f"hub returned {e.response.status_code}") from e
            if e.response.status_code == 422:
                try:
                    error = e.response.json()["error"]
                except (ValueError, KeyError):
                    error = {}
                if error.get("code") == "already_resolved":
                    raise MessageAlreadyResolved(error["detail"]["state"]) from e
            raise
        except httpx.HTTPError as e:
            raise HubUnavailable(str(e)) from e

    async def upload_attachment(
        self,
        payload: bytes,
        *,
        filename: str,
        mime_type: str,
        width: int | None = None,
        height: int | None = None,
        blur_preview: str | None = None,
    ) -> str:
        """Upload one blob and return its `att_…` id, to be referenced by a later
        send_message(attachment_ids=[…]). The hub infers kind from the mime type.
        Optional metadata lets the app render without reflow (width/height reserve
        the aspect ratio) and show a blur-up placeholder (blur_preview); all are
        omitted when absent and the hub does not validate them. Raises
        HubUnavailable on a server error or transport failure so a proactive media
        send degrades to a failed send rather than crashing the caller."""
        # Numeric metadata rides the multipart body as decimal strings (FastAPI
        # coerces them back to int); blur_preview is passed through verbatim.
        data: dict[str, str] = {}
        if width is not None:
            data["width"] = str(width)
        if height is not None:
            data["height"] = str(height)
        if blur_preview:
            data["blur_preview"] = blur_preview
        try:
            r = await self._client.post(
                "/bot/v1/attachments",
                files={"file": (filename, payload, mime_type)},
                data=data or None,
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
        command menu. Each entry is {"name", "description"}. A declare replaces
        the whole list, so re-sending the same payload is idempotent."""
        r = await self._client.post("/bot/v1/commands", json=commands)
        r.raise_for_status()

    async def post_app_result(self, query_id: str, body: dict) -> bool:
        """Answer a parked app query. `body` carries exactly one of data/error —
        the discriminator is field presence, so this leg's own status describes
        whether the hub accepted the answer, not whether the query succeeded.

        Returns False when nobody is waiting any more (404 `unknown_query`): the
        query timed out, the client hung up, or the hub restarted after queuing
        it. That is expected traffic, not a fault — the caller logs and drops it,
        and must never retry, since no retry can find a park that is gone.
        Raises HubUnavailable on a server error or transport failure.
        """
        try:
            r = await self._client.post(f"/bot/v1/apps/{query_id}/results", json=body)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise HubUnavailable(f"hub returned {e.response.status_code}") from e
            if e.response.status_code == 404:
                try:
                    code = e.response.json()["error"]["code"]
                except (ValueError, KeyError, TypeError):
                    code = None
                if code == "unknown_query":
                    return False
            raise
        except httpx.HTTPError as e:
            raise HubUnavailable(str(e)) from e
        return True

    async def post_event(self, event_type: str, data: dict) -> None:
        """Relay one ephemeral event to the client's stream. Never persisted and
        carries no cursor, so nothing comes back to correlate — hence no
        HubUnavailable translation and no 404 handling: there is no parked state
        to miss and no retry question, the caller's next event is the retry."""
        r = await self._client.post(
            "/bot/v1/events", json={"type": event_type, "data": data}
        )
        r.raise_for_status()

    async def declare_apps(self, apps: list[dict]) -> None:
        """Publish the app manifest to the hub so the app can draw its Apps
        screen. Each entry is {"ns", "name", "entries": [{"id", "method",
        "params"}]}. Like commands, a declare replaces the whole list, so
        re-sending the same payload is idempotent."""
        r = await self._client.post("/bot/v1/apps", json=apps)
        r.raise_for_status()
