import logging

from aiogram import Bot, Router
from aiogram.types import ErrorEvent

router = Router()
logger = logging.getLogger(__name__)


@router.errors()
async def error_handler(event: ErrorEvent):
    logger.exception("Update error: %s", event.exception)

    update = event.update
    if not update:
        return

    chat_id = None
    if update.message:
        chat_id = update.message.chat.id
    elif update.callback_query:
        chat_id = update.callback_query.message.chat.id if update.callback_query.message else None
    elif update.edited_message:
        chat_id = update.edited_message.chat.id
    elif update.my_chat_member:
        chat_id = update.my_chat_member.chat.id

    if chat_id:
        try:
            from aiogram import Bot
            bot = Bot.get_current(raise_error=False)
            if bot:
                await bot.send_message(
                    chat_id,
                    "\u26a0\ufe0f Сталася помилка. Спробуйте /cancel щоб скинути стан.",
                )
        except Exception:
            logger.debug("Could not send error notification (Bot.get_current unavailable)")
