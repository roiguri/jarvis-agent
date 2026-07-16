"""
TelegramChannel — the Telegram implementation of the Channel contract.

Owns the PTB Bot reference and the running event loop, and adapts Telegram's
send primitives onto the channel-agnostic contract in gateway.base. All neutral
text is treated as Markdown and rendered to Telegram HTML here; callers never
deal in HTML.
"""

import logging

from telegram import Bot, BotCommand
from telegram.error import BadRequest

from gateway.base import Channel
from gateway.commands import list_commands as _list_slash_commands
from gateway.markdown_to_html import convert as md_to_html

logger = logging.getLogger(__name__)

# Telegram's hard caps: 4096 chars for text, 1024 for photo captions. We
# truncate the *source markdown* (not the converted HTML, which could be cut
# mid-tag) with headroom for HTML entity expansion. This is a safety net,
# not a feature — see issue #52 for proper paragraph-aware pagination.
_TEXT_LIMIT = 3800
_CAPTION_LIMIT = 900
_TRUNCATION_TAIL = "\n\n_…(truncated — output too long for one message)_"


def _truncate_markdown(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + _TRUNCATION_TAIL


# thread_id format is frozen at telegram_<user_id> for Phase 1. The ":" separator
# change is a Phase 2 concern coupled to the checkpointer-key migration; changing
# it here would orphan every existing LangGraph checkpoint and history record.
def thread_id_for(user_id: int) -> str:
    return f"telegram_{user_id}"


class TelegramChannel(Channel):
    name = "telegram"
    supports_streaming = False

    def __init__(self, owner_id: int) -> None:
        self._owner_id = owner_id
        self._bot: Bot | None = None

    # ------------------------------------------------------------------
    # Lifecycle — TelegramHost owns the PTB Application; it calls attach()
    # once the bot is live.
    # ------------------------------------------------------------------

    def attach(self, bot: Bot) -> None:
        self._bot = bot
        logger.info("TelegramChannel attached (owner_id=%d)", self._owner_id)

    def _require_bot(self) -> Bot:
        if self._bot is None:
            raise RuntimeError("TelegramChannel.attach() must be called before sending.")
        return self._bot

    # ------------------------------------------------------------------
    # Internal send primitives (Markdown -> HTML, with plain-text fallback)
    # ------------------------------------------------------------------

    async def _send_text(self, chat_id: int, text: str) -> None:
        bot = self._require_bot()
        text = _truncate_markdown(text, _TEXT_LIMIT)
        html = md_to_html(text)
        try:
            await bot.send_message(chat_id=chat_id, text=html, parse_mode="HTML")
        except BadRequest as e:
            if "Can't parse entities" in str(e):
                logger.warning("HTML parse failed for chat %s; sending plain: %s", chat_id, e)
                await bot.send_message(chat_id=chat_id, text=text, parse_mode=None)
                return
            raise

    async def _send_photo(self, chat_id: int, payload: bytes, caption: str | None) -> None:
        bot = self._require_bot()
        raw_caption = _truncate_markdown(caption, _CAPTION_LIMIT) if caption else ""
        html = md_to_html(raw_caption) if raw_caption else None
        try:
            await bot.send_photo(chat_id=chat_id, photo=payload, caption=html, parse_mode="HTML")
        except BadRequest as e:
            if "Can't parse entities" in str(e):
                logger.warning("HTML caption parse failed for chat %s; sending plain: %s", chat_id, e)
                await bot.send_photo(
                    chat_id=chat_id, photo=payload, caption=raw_caption or None, parse_mode=None
                )
                return
            raise

    # ------------------------------------------------------------------
    # Channel ABC
    # ------------------------------------------------------------------

    async def send(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
        await self._send_text(int(chat_id), text)

    async def send_media(
        self, chat_id: str, kind: str, payload: bytes, caption: str | None = None
    ) -> None:
        if kind != "image":
            raise NotImplementedError(f"TelegramChannel cannot send media kind={kind!r}")
        await self._send_photo(int(chat_id), payload, caption)

    async def send_to_owner(self, text: str) -> None:
        await self._send_text(self._owner_id, text)

    async def send_to_owner_media(
        self, kind: str, payload: bytes, caption: str | None = None
    ) -> None:
        if kind != "image":
            raise NotImplementedError(f"TelegramChannel cannot send media kind={kind!r}")
        await self._send_photo(self._owner_id, payload, caption)

    def authorize(self, raw_user_id: str) -> bool:
        try:
            return int(raw_user_id) == self._owner_id
        except (TypeError, ValueError):
            return False

    @property
    def owner_thread_id(self) -> str:
        return thread_id_for(self._owner_id)

    # ------------------------------------------------------------------
    # Telegram-specific helpers used by the router / confirmation UI
    # (same package — concrete coupling is intentional and contained).
    # ------------------------------------------------------------------

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            await self._require_bot().send_chat_action(chat_id=chat_id, action=action)
        except Exception:
            logger.debug("send_chat_action failed (non-fatal)", exc_info=True)

    async def register_command_menu(self) -> None:
        """Populate the Telegram client's slash-command autocomplete from the
        gateway-shared command registry. Telegram-specific UX, but the list of
        commands comes from `gateway/commands/` so it stays in sync."""
        cmds = [
            BotCommand(c.name[:32], c.description[:256]) for c in _list_slash_commands()
        ]
        try:
            await self._require_bot().set_my_commands(cmds)
            logger.info("Registered %d slash commands with Telegram", len(cmds))
        except Exception:
            logger.warning("Failed to register Telegram command menu", exc_info=True)

    async def download_media(self, file_id: str) -> bytes | None:
        try:
            file = await self._require_bot().get_file(file_id)
            return bytes(await file.download_as_bytearray())
        except Exception:
            logger.exception("Failed to download media file %s", file_id)
            return None

    @property
    def owner_id(self) -> int:
        return self._owner_id

    @property
    def bot(self) -> Bot:
        return self._require_bot()
