"""
TelegramMessenger — Emma's Telegram bot.

Purpose: let other people reach you through Emma. They message the bot; it
tells them whether you're free and, when you're busy, when you'll next be free
(read from your daily timetable). High-priority people are told they can reach
you anyway, and you're alerted immediately. No AI chat — just a smart relay.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from core.busy_mode import MessengerAdapter

logger = logging.getLogger("emma.notifications.telegram")

WELCOME = (
    "You're connected to Emma, my personal AI assistant.\n\n"
    "Your Telegram ID: {telegram_id}\n"
    "Message me here anytime to check if I'm available — I'll tell you when "
    "you can reach me."
)


class TelegramMessenger(MessengerAdapter):
    """Bot that answers availability queries and relays priority pings."""

    name = "telegram"

    def __init__(self, bot_token: str, notify_manager=None, busy_manager=None):
        self.bot_token = bot_token
        self._notify_mgr = notify_manager
        self._busy_mgr = busy_manager
        self._app: Optional[Application] = None
        self._started = False
        self._message_log: list[dict] = []

    async def _build_app(self) -> Application:
        app = Application.builder().token(self.bot_token).build()

        async def _start(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.username or str(uid)
            chat_id = update.effective_chat.id
            self._register(uid, name, chat_id)
            self._log("connect", uid, name, "/start")
            await update.message.reply_text(WELCOME.format(telegram_id=uid))

        async def _handle_text(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            text = update.message.text
            chat_id = update.effective_chat.id
            self._register(uid, name, chat_id)
            self._log("message", uid, name, text)
            await self._reply_status(update, uid, name, text)

        app.add_handler(CommandHandler("start", _start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))
        return app

    def _register(self, uid: int, name: str, chat_id: int):
        if self._notify_mgr:
            self._notify_mgr.register_user(uid, name)
            self._notify_mgr.set_chat_id(uid, chat_id)

    async def _reply_status(self, update: Update, uid: int, name: str, text: str):
        state = self._busy_mgr.get_state() if self._busy_mgr else None

        if not state or not state.is_busy:
            await update.message.reply_text(
                "I'm free right now — go ahead and message or call me, I'll see it."
            )
            return

        note = f" ({state.note})" if state.note else ""
        hint = self._free_hint()
        when = f" You can reach me {hint}." if hint and hint != "right now" else ""

        priority = self._notify_mgr.get_priority(uid) if self._notify_mgr else "normal"
        custom = None
        if self._notify_mgr:
            custom = self._notify_mgr._get_user_row(uid).get("busy_message")

        if priority == "high":
            reply = custom or (
                f"I'm busy right now{note}, but you're a priority for me — go ahead and "
                f"message or call, and I'll get to you as soon as I can.{when}"
            )
            await update.message.reply_text(reply)
            if self._notify_mgr:
                await self._notify_mgr.notify_owner(
                    f"📨 High-priority: {name} just messaged the bot:\n{text}"
                )
        else:
            reply = custom or f"I'm busy right now{note}. I'll get back to you when I'm free.{when}"
            await update.message.reply_text(reply)

    def _free_hint(self) -> Optional[str]:
        sched = getattr(self._notify_mgr, "schedule", None) if self._notify_mgr else None
        if not sched:
            return None
        try:
            return sched.free_hint(datetime.now())
        except Exception as exc:  # noqa: BLE001 - a bad schedule shouldn't break a reply
            logger.warning("free_hint failed: %s", exc)
            return None

    def _log(self, msg_type: str, uid: int, name: str, text: str):
        self._message_log.append({
            "type": msg_type,
            "user_id": uid,
            "name": name,
            "text": text,
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def start(self):
        if self._started:
            return
        self._app = await self._build_app()
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        self._started = True
        logger.info("Telegram bot started.")

    async def stop(self):
        if not self._started or not self._app:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        self._started = False
        logger.info("Telegram bot stopped.")

    async def send(self, contact_name: str, message: str) -> bool:
        if not self._notify_mgr:
            return False
        chat_id = self._notify_mgr.get_chat_id_by_label(contact_name)
        if not chat_id:
            logger.warning("No chat_id for contact: %s", contact_name)
            return False
        return await self.send_to_chat(chat_id, message)

    async def send_to_user(self, telegram_id: int, message: str) -> bool:
        chat_id = self._notify_mgr.get_chat_id(telegram_id) if self._notify_mgr else None
        if not chat_id:
            return False
        return await self.send_to_chat(chat_id, message)

    async def send_to_chat(self, chat_id: int, message: str) -> bool:
        if not self._app:
            return False
        try:
            await self._app.bot.send_message(chat_id=chat_id, text=message)
            return True
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def message_log(self) -> list[dict]:
        return list(self._message_log)
