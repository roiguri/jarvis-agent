"""
Telegram inbound router — protocol-specific update parsing, authorization, and
media-group (album) batching. Produces channel-agnostic InboundMessage objects,
hands them to the domain on_message handler, and posts the reply back via the
channel.
"""

import asyncio
import logging
import mimetypes

from telegram import Update

from gateway.base import InboundMessage, OnMessage
from gateway.channels.telegram.channel import TelegramChannel

logger = logging.getLogger(__name__)


def _neutral_kind(mime_type: str) -> str:
    """Resolve a document's mime into the gateway's neutral media kind.

    A Telegram document is a container, not a type — it is how anything that is
    not a compressed photo, a video or a voice note arrives, so a PDF, a video
    sent as a file, and a photo sent at original quality all land here. Classify
    by prefix and let the model boundary decide which formats of a kind it can
    actually read; the channel must not carry the model's format table.
    """
    mime = mime_type.split(";")[0].strip().lower()
    if mime == "application/pdf":
        return "document"
    for prefix, kind in (("image/", "image"), ("audio/", "audio"), ("video/", "video")):
        if mime.startswith(prefix):
            return kind
    return "file"  # unidentified — the agent surfaces it as text

class TelegramInboundRouter:
    def __init__(
        self,
        channel: TelegramChannel,
        on_message: OnMessage,
        album_flush_seconds: float = 1.2,
    ) -> None:
        self._channel = channel
        self._on_message = on_message
        self._album_flush_seconds = album_flush_seconds
        self._media_group_buffers: dict[str, dict] = {}
        self._media_group_tasks: dict[str, asyncio.Task] = {}
        self._media_group_lock = asyncio.Lock()

    def _authorized(self, user_id: int) -> bool:
        if not self._channel.authorize(str(user_id)):
            logger.warning("Unauthorized access attempt from user ID: %s", user_id)
            return False
        return True

    async def handle_text(self, update: Update, context) -> None:
        msg = update.message
        user_id = msg.from_user.id
        if not self._authorized(user_id):
            return
        await self._dispatch(InboundMessage(
            user_id=user_id,
            chat_id=update.effective_chat.id,
            thread_id=self._channel.owner_thread_id,
            user_text=msg.text or "",
        ))

    async def handle_photo(self, update: Update, context) -> None:
        msg = update.message
        user_id = msg.from_user.id
        if not self._authorized(user_id):
            return

        media_group_id = msg.media_group_id
        if not media_group_id:
            attachment = await self._download(msg.photo[-1].file_id, "image", "image/jpeg")
            if not attachment:
                await self._channel.send(str(update.effective_chat.id), "Failed to download media. Please try again.")
                return
            await self._dispatch(InboundMessage(
                user_id=user_id,
                chat_id=update.effective_chat.id,
                thread_id=self._channel.owner_thread_id,
                user_text=msg.caption or "",
                attachments=[attachment],
            ))
            return

        async with self._media_group_lock:
            payload = self._media_group_buffers.get(media_group_id)
            if payload is None:
                payload = {
                    "user_id": user_id,
                    "chat_id": update.effective_chat.id,
                    "caption": msg.caption,
                    "items": [],
                }
                self._media_group_buffers[media_group_id] = payload

            payload["items"].append((msg.photo[-1].file_id, "image", "image/jpeg"))
            if msg.caption and not payload["caption"]:
                payload["caption"] = msg.caption

            existing_task = self._media_group_tasks.get(media_group_id)
            if existing_task and not existing_task.done():
                existing_task.cancel()

            self._media_group_tasks[media_group_id] = asyncio.create_task(
                self._flush_media_group(media_group_id)
            )

    async def handle_video(self, update: Update, context) -> None:
        msg = update.message
        user_id = msg.from_user.id
        if not self._authorized(user_id):
            return

        mime_type = msg.video.mime_type or "video/mp4"
        attachment = await self._download(msg.video.file_id, "video", mime_type)
        if not attachment:
            await self._channel.send(str(update.effective_chat.id), "Failed to download media. Please try again.")
            return
        await self._dispatch(InboundMessage(
            user_id=user_id,
            chat_id=update.effective_chat.id,
            thread_id=self._channel.owner_thread_id,
            user_text=msg.caption or "",
            attachments=[attachment],
        ))

    async def handle_voice(self, update: Update, context) -> None:
        msg = update.message
        user_id = msg.from_user.id
        if not self._authorized(user_id):
            return

        mime_type = msg.voice.mime_type or "audio/ogg"
        attachment = await self._download(msg.voice.file_id, "audio", mime_type)
        if not attachment:
            await self._channel.send(str(update.effective_chat.id), "Failed to download media. Please try again.")
            return
        await self._dispatch(InboundMessage(
            user_id=user_id,
            chat_id=update.effective_chat.id,
            thread_id=self._channel.owner_thread_id,
            user_text=msg.caption or "",
            attachments=[attachment],
        ))

    async def handle_document(self, update: Update, context) -> None:
        msg = update.message
        user_id = msg.from_user.id
        if not self._authorized(user_id):
            return

        doc = msg.document
        # mime_type is client-supplied and may be absent — fall back to the
        # filename, which Telegram always carries for a document.
        mime_type = doc.mime_type or ""
        if not mime_type and doc.file_name:
            mime_type = mimetypes.guess_type(doc.file_name)[0] or ""
        kind = _neutral_kind(mime_type)

        attachment = await self._download(doc.file_id, kind, mime_type, filename=doc.file_name)
        if not attachment:
            await self._channel.send(str(update.effective_chat.id), "Failed to download media. Please try again.")
            return
        await self._dispatch(InboundMessage(
            user_id=user_id,
            chat_id=update.effective_chat.id,
            thread_id=self._channel.owner_thread_id,
            user_text=msg.caption or "",
            attachments=[attachment],
        ))

    async def _download(self, file_id: str, kind: str, mime_type: str,
                        filename: str | None = None) -> dict | None:
        """The neutral attachment for one Telegram file: its bytes, for core to store."""
        media_bytes = await self._channel.download_media(file_id)
        if not media_bytes:
            return None
        return {"kind": kind, "mime_type": mime_type, "data": media_bytes,
                "filename": filename, "source": self._channel.name, "origin_id": file_id}

    async def _flush_media_group(self, group_id: str) -> None:
        try:
            await asyncio.sleep(self._album_flush_seconds)

            async with self._media_group_lock:
                payload = self._media_group_buffers.pop(group_id, None)
                self._media_group_tasks.pop(group_id, None)

            if not payload:
                return

            attachments: list[dict] = []
            for file_id, kind, mime_type in payload["items"]:
                attachment = await self._download(file_id, kind, mime_type)
                if attachment:
                    attachments.append(attachment)

            if not attachments:
                await self._channel.send(str(payload["chat_id"]), "Failed to download media. Please try again.")
                return

            user_text = payload["caption"] or ""
            await self._dispatch(InboundMessage(
                user_id=payload["user_id"],
                chat_id=payload["chat_id"],
                thread_id=self._channel.owner_thread_id,
                user_text=user_text,
                attachments=attachments,
            ))
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Error flushing media group %s", group_id)

    async def _dispatch(self, inbound: InboundMessage) -> None:
        # One funnel serves every handler — stamp the origin once here.
        inbound.channel = self._channel.name

        async def keep_typing() -> None:
            while True:
                await self._channel.send_chat_action(inbound.chat_id, "typing")
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        try:
            response = await self._on_message(inbound)
            if response:
                await self._channel.send(str(inbound.chat_id), response)
            else:
                await self._channel.send(str(inbound.chat_id), "I'm sorry, I encountered an error generating a response.")
        except Exception:
            logger.exception("Error while dispatching inbound message")
            await self._channel.send(str(inbound.chat_id), "A system error occurred while processing your request.")
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Typing indicator task ended with error", exc_info=True)
