"""
JarvisAppChannel — the jarvis-app implementation of the Channel contract.

A thin adapter over HubClient. The hub is one-bot-one-owner: there is a single
conversation, so `chat_id` carries no routing and every send addresses the owner.
Media rides the attachment model: upload the bytes, then send a message that
references the returned id. The hub's kinds are image | audio | file; a kind this
channel can't represent raises NotImplementedError, which the Outbox reports as a
failed send (base.Channel.send_media contract) rather than mislabeling the blob.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    # Pillow only powers optional upload metadata (dimensions + blur-up
    # placeholder). The factory imports this module unconditionally, so a deploy
    # without Pillow must still import cleanly — metadata just degrades to none.
    Image = None

from gateway.base import Channel
from gateway.channels.jarvis_app.client import HubClient

logger = logging.getLogger(__name__)

# Longest edge of the blur-up placeholder thumbnail, matching the app's own
# outbound encoder (AttachmentBlurEncoder): ~24px, JPEG quality 60.
_BLUR_EDGE = 24
_BLUR_QUALITY = 60


# thread_id mirrors telegram's "<channel>_<id>", parsed on the first underscore.
# The channel name contains no underscore, so the prefix stays unambiguous.
def thread_id_for(owner_id: str) -> str:
    return f"jarvis-app_{owner_id}"


# The kinds this channel can represent, mapped to the (filename, mime_type) the
# upload needs. The hub infers its Attachment.kind from the mime type, and its own
# enum is image | audio | file — Telegram's "video" has no equivalent here, so it
# (and any other kind) falls through to NotImplementedError rather than being
# mislabeled. Images sniff PNG vs JPEG since posters arrive as either.
def _upload_meta(kind: str, payload: bytes) -> tuple[str, str]:
    if kind == "image":
        if payload[:8] == b"\x89PNG\r\n\x1a\n":
            return "image.png", "image/png"
        return "image.jpg", "image/jpeg"
    if kind == "audio":
        return "audio.ogg", "audio/ogg"
    raise NotImplementedError(f"jarvis-app cannot send media kind={kind!r}")


# Pixel dimensions + a tiny base64 blur-up thumbnail for an image, so the app
# reserves the right aspect ratio (no thread reflow) and shows a placeholder
# while the full image loads. Best-effort: any decode/encode failure returns
# empty metadata and the upload proceeds without it (the hub treats every field
# as optional and the app degrades to a plain placeholder). blur_preview is not a
# perceptual hash — it is base64 of a real ~24px thumbnail the app just decodes.
def _image_metadata(payload: bytes) -> dict:
    if Image is None:
        return {}
    try:
        with Image.open(BytesIO(payload)) as im:
            im.load()
            width, height = im.size
            thumb = im.copy()
            thumb.thumbnail((_BLUR_EDGE, _BLUR_EDGE))
            # JPEG has no alpha/palette — normalize so the thumbnail always saves.
            if thumb.mode not in ("RGB", "L"):
                thumb = thumb.convert("RGB")
            buf = BytesIO()
            thumb.save(buf, "JPEG", quality=_BLUR_QUALITY)
        blur = base64.b64encode(buf.getvalue()).decode("ascii")
        return {"width": width, "height": height, "blur_preview": blur}
    except Exception as exc:
        logger.warning("jarvis-app image metadata skipped: %s", exc)
        return {}


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
        # One owner, one conversation — chat_id is not a routing key here.
        await self._upload_and_send(kind, payload, caption)

    async def send_to_owner(self, text: str) -> None:
        await self._client.send_message({"text": text})

    async def send_to_owner_media(
        self, kind: str, payload: bytes, caption: str | None = None
    ) -> None:
        await self._upload_and_send(kind, payload, caption)

    async def _upload_and_send(
        self, kind: str, payload: bytes, caption: str | None
    ) -> None:
        # Raises NotImplementedError for an unrepresentable kind — before any
        # upload — so the Outbox reports a failed send with nothing half-sent.
        filename, mime_type = _upload_meta(kind, payload)
        # Images carry dimensions + a blur-up placeholder; other kinds carry none.
        meta = _image_metadata(payload) if kind == "image" else {}
        att_id = await self._client.upload_attachment(
            payload, filename=filename, mime_type=mime_type, **meta
        )
        body: dict = {"attachment_ids": [att_id]}
        if caption:
            body["text"] = caption
        await self._client.send_message(body)

    def authorize(self, raw_user_id: str) -> bool:
        # The bot token scopes the hub to the single owner, so inbound updates are
        # already authorized upstream; this checks against the configured owner
        # for completeness.
        return str(raw_user_id) == self._owner_id

    @property
    def owner_thread_id(self) -> str:
        return thread_id_for(self._owner_id)
