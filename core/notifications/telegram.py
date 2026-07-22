from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from core.busy_mode import MessengerAdapter

logger = logging.getLogger("emma.notifications.telegram")

WELCOME = (
    "You're connected to Emma, my personal AI assistant.\n\n"
    "Your Telegram ID: {telegram_id}\n"
    "Commands:\n"
    "/book <duration> — book time in my schedule\n"
    "/myslots <day> — see my free slots for a day\n\n"
    "Message me here anytime to check if I'm available."
)

HELP_BOOK = (
    "To book time with me:\n"
    "1. Find my free slots: /myslots today\n"
    "2. Book: /book 1h at 14:00 tomorrow\n\n"
    "Or just tell me when you want, and I'll figure it out."
)

_RETRY_DELAYS = [5, 15, 30, 60, 120]


class TelegramMessenger(MessengerAdapter):
    """Bot that answers availability queries and relays priority pings."""

    name = "telegram"

    def __init__(self, bot_token: str, notify_manager=None, busy_manager=None):
        self.bot_token = bot_token
        self._notify_mgr = notify_manager
        self._busy_mgr = busy_manager
        self._appointment_mgr = None
        self._app: Optional[Application] = None
        self._started = False
        self._stop_event: Optional[asyncio.Event] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._last_error: Optional[str] = None
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

        async def _book(update: Update, context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            chat_id = update.effective_chat.id
            self._register(uid, name, chat_id)
            self._log("command", uid, name, update.message.text)

            args = context.args
            if not args:
                await update.message.reply_text(HELP_BOOK)
                return

            sched = getattr(self._notify_mgr, "schedule", None)
            if not sched:
                await update.message.reply_text("Sorry, the schedule system isn't available right now.")
                return

            text = " ".join(args)
            target_day = date.today()
            duration_minutes = 60

            if "tomorrow" in text:
                target_day = date.today() + timedelta(days=1)

            import re
            dur_match = re.search(r"(\d+)\s*h", text)
            if dur_match:
                duration_minutes = int(dur_match.group(1)) * 60
            dur_match_min = re.search(r"(\d+)\s*min", text)
            if dur_match_min:
                duration_minutes = int(dur_match_min.group(1))

            blocks = sched.list_day(target_day)
            free_slots = []
            if self._appointment_mgr:
                free_slots = self._appointment_mgr.find_free_slots(target_day, blocks, duration_minutes)

            if not free_slots:
                await update.message.reply_text(
                    f"Sorry, no free slots found for {target_day.isoformat()}."
                )
                return

            slot = free_slots[0]
            start_dt = datetime.fromisoformat(slot["start"])
            end_dt = start_dt + timedelta(minutes=duration_minutes)

            if self._appointment_mgr:
                self._appointment_mgr.create(
                    person_label=name,
                    person_telegram_id=uid,
                    day=target_day,
                    start=start_dt.strftime("%H:%M"),
                    end=end_dt.strftime("%H:%M"),
                    title=f"Appointment with {name}",
                    note=text,
                )

            time_str = f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}"
            await update.message.reply_text(
                f"✅ Booked you at {time_str} on {target_day.isoformat()}. "
                f"I'll notify you if anything changes."
            )

            if self._notify_mgr:
                await self._notify_mgr.notify_owner(
                    f"📅 Appointment request from {name}: {text}\n"
                    f"Time: {time_str} on {target_day.isoformat()}"
                )

        async def _myslots(update: Update, context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, update.message.text)

            sched = getattr(self._notify_mgr, "schedule", None)
            if not sched:
                await update.message.reply_text("Schedule system not available.")
                return

            target_day = date.today()
            if context.args:
                arg = " ".join(context.args)
                if arg == "tomorrow":
                    target_day = date.today() + timedelta(days=1)
                else:
                    try:
                        target_day = date.fromisoformat(arg)
                    except ValueError:
                        pass

            blocks = sched.list_day(target_day)
            free_slots = []
            if self._appointment_mgr:
                free_slots = self._appointment_mgr.find_free_slots(target_day, blocks, 30)

            if not free_slots:
                await update.message.reply_text(f"No free slots on {target_day.isoformat()}.")
                return

            lines = [f"Free slots for {target_day.isoformat()}:"]
            for s in free_slots[:10]:
                start = datetime.fromisoformat(s["start"])
                end = datetime.fromisoformat(s["end"])
                lines.append(f"  {start.strftime('%H:%M')} - {end.strftime('%H:%M')} ({s['duration_minutes']}min)")
            if len(free_slots) > 10:
                lines.append(f"  ... and {len(free_slots) - 10} more")
            await update.message.reply_text("\n".join(lines))

        async def _handle_text(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            text = update.message.text
            chat_id = update.effective_chat.id
            self._register(uid, name, chat_id)
            self._log("message", uid, name, text)
            await self._reply_status(update, uid, name, text)

        app.add_handler(CommandHandler("start", _start))
        app.add_handler(CommandHandler("book", _book))
        app.add_handler(CommandHandler("myslots", _myslots))
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
        if not self.bot_token:
            logger.warning("No bot token — Telegram bot disabled.")
            return
        self._app = await self._build_app()
        await self._app.initialize()
        await self._app.start()
        self._stop_event = asyncio.Event()
        self._poll_task = asyncio.create_task(self._run_polling())
        self._started = True
        logger.info("Telegram bot started.")

    async def _run_polling(self):
        """Run polling with retry and auto-restart on crash."""
        for attempt, delay in enumerate(_RETRY_DELAYS):
            try:
                await self._app.updater.start_polling()
                break
            except Exception as exc:
                logger.warning(
                    "Telegram polling attempt %d/%d failed: %s",
                    attempt + 1, len(_RETRY_DELAYS), exc,
                )
                if attempt < len(_RETRY_DELAYS) - 1:
                    await asyncio.sleep(delay)
        else:
            self._last_error = "All startup retries exhausted"
            self._started = False
            return

        # Keep alive — detect crash and auto-restart
        while True:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=15)
                return  # Clean stop requested
            except asyncio.TimeoutError:
                pass

            if not self._app.updater.running:
                logger.warning("Updater stopped — restarting...")
                for attempt, delay in enumerate(_RETRY_DELAYS):
                    try:
                        await self._app.updater.start_polling()
                        self._last_error = None
                        break
                    except Exception as exc:
                        logger.warning("Restart attempt %d failed: %s", attempt + 1, exc)
                        await asyncio.sleep(delay)
                else:
                    self._last_error = "Restart retries exhausted"
                    self._started = False
                    return

    async def stop(self):
        if not self._started or not self._app:
            return
        if self._stop_event:
            self._stop_event.set()
        if self._poll_task and not self._poll_task.done():
            try:
                await asyncio.wait_for(self._poll_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._poll_task.cancel()
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
        if not self._started or not self._app:
            return False
        try:
            return self._app.updater.running
        except Exception:
            return False

    @property
    def error(self) -> Optional[str]:
        return self._last_error

    @property
    def message_log(self) -> list[dict]:
        return list(self._message_log)
