"""
TelegramConfirmationUI — the Telegram-specific half of Plane 3: render the
confirm/cancel inline keyboard and edit it into the final outcome. All
bookkeeping lives in the channel-agnostic store.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest

from gateway.confirmation.base import ConfirmationUI
from gateway.channels.telegram.channel import TelegramChannel
from gateway.channels.telegram._render import render_fences_only

logger = logging.getLogger(__name__)


class TelegramConfirmationUI(ConfirmationUI):
    def __init__(self, channel: TelegramChannel) -> None:
        self._channel = channel
        self._store = None  # set via bind_store() to break the construction cycle
        self._message_ids: dict[str, int] = {}  # callback_id -> prompt message_id

    def bind_store(self, store) -> None:
        self._store = store

    async def send_prompt(self, callback_id: str, description: str) -> None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:{callback_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{callback_id}"),
        ]])
        # The description is a neutral string; only here is it turned into
        # Telegram HTML. Fenced blocks (e.g. ```diff) render monospace so a
        # diff/preview is readable; everything else is escaped verbatim. The
        # global bold wrap is intentionally gone — it made diffs unreadable;
        # the ⚠️ line carries the prompt's salience.
        text = f"⚠️ Confirmation required:\n{render_fences_only(description)}"
        try:
            message = await self._channel.bot.send_message(
                chat_id=self._channel.owner_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except BadRequest as e:
            # Mirror channel._send_text: if Telegram rejects the HTML, deliver
            # the raw description as plain text rather than losing the prompt.
            # Other BadRequests propagate so the store backstop still fires.
            if "Can't parse entities" not in str(e):
                raise
            logger.warning(
                "Confirmation HTML parse failed for %s; sending plain: %s",
                callback_id, e,
            )
            message = await self._channel.bot.send_message(
                chat_id=self._channel.owner_id,
                text=f"⚠️ Confirmation required:\n{description}",
                parse_mode=None,
                reply_markup=keyboard,
            )
        self._message_ids[callback_id] = message.message_id

    async def edit_outcome(self, callback_id: str, outcome_text: str) -> None:
        message_id = self._message_ids.pop(callback_id, None)
        try:
            if message_id is not None:
                await self._channel.bot.edit_message_text(
                    chat_id=self._channel.owner_id,
                    message_id=message_id,
                    text=outcome_text,
                )
            else:
                await self._channel.send_to_owner(outcome_text)
        except Exception:
            logger.exception("Failed to edit confirmation outcome")

    async def expire(self, callback_id: str) -> None:
        await self.edit_outcome(callback_id, "⌛ Confirmation expired.")

    async def handle_callback(self, update: Update, context) -> None:
        """PTB CallbackQueryHandler entry point."""
        query = update.callback_query
        await query.answer()

        data = query.data or ""
        if ":" not in data:
            return
        action_type, callback_id = data.split(":", 1)

        if self._store is None:
            logger.error("Confirmation store not bound; cannot resolve %s", callback_id)
            return
        await self._store.resolve(callback_id, confirmed=(action_type == "confirm"))
