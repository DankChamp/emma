from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from core.busy_mode import MessengerAdapter

logger = logging.getLogger("emma.notifications.telegram")

WELCOME = (
    "\U0001f916 Hi! I'm Emma, {owner_name}'s AI assistant.\n\n"
    "I help manage his schedule and keep track of his availability. "
    "You can reach me through this bot to book time or check when he's free.\n\n"
    "Your Telegram ID: {telegram_id}\n\n"
    "Send /menu for the interactive menu, or /help to see all commands."
)

HELP_BOOK = (
    "To book time with {owner_name}:\n"
    "1. Check free slots: /myslots today\n"
    "2. Book: /book 1h at 14:00 tomorrow\n\n"
    "Or just tell me when you want, and I'll figure it out."
)

HELP_TEXT = (
    "\U0001f916 Emma — {owner_name}'s AI Assistant\n\n"
    "I answer availability questions, manage the schedule, and relay messages.\n\n"
    "\U0001f4c5 Schedule:\n"
    "/myslots [today|tomorrow|YYYY-MM-DD] \u2014 See {owner_name}'s free slots\n"
    "/book <duration> \u2014 Book time (e.g. /book 1h tomorrow at 14:00)\n\n"
    "\U0001f4cb Your Bookings:\n"
    "/mybookings \u2014 View your upcoming appointments\n"
    "/cancel <id> \u2014 Cancel one of your bookings\n\n"
    "\u2753 General:\n"
    "/menu \u2014 Show interactive menu\n"
    "/help \u2014 This message"
)

OWNER_HELP = (
    "\n\n\U0001f451 Owner Commands:\n"
    "/pending \u2014 View pending appointment requests\n"
    "/confirm <id> \u2014 Confirm a booking\n"
    "/reject <id> \u2014 Reject a booking\n"
    "/appointments [day] \u2014 View all appointments for a day\n"
    "/status \u2014 View your busy/free status"
)

_RETRY_DELAYS = [5, 15, 30, 60, 120]


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


class TelegramMessenger(MessengerAdapter):
    """Bot that answers availability queries and relays priority pings."""

    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        owner_name: str = "VOID",
        owner_telegram_id: Optional[int] = None,
        notify_manager=None,
        busy_manager=None,
    ):
        self.bot_token = bot_token
        self._owner_name = owner_name
        self._owner_telegram_id = owner_telegram_id
        self._notify_mgr = notify_manager
        self._busy_mgr = busy_manager
        self._appointment_mgr = None
        self._app: Optional[Application] = None
        self._started = False
        self._stop_event: Optional[asyncio.Event] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._last_error: Optional[str] = None
        self._message_log: list[dict] = []

    # ------------------------------------------------------------------
    # Owner detection
    # ------------------------------------------------------------------
    def _is_owner(self, uid: int) -> bool:
        if self._owner_telegram_id is not None and uid == self._owner_telegram_id:
            return True
        if self._notify_mgr:
            row = self._notify_mgr._get_user_row(uid)
            return bool(row.get("is_owner"))
        return False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def _register(self, uid: int, name: str, chat_id: int):
        if self._notify_mgr:
            self._notify_mgr.register_user(uid, name)
            self._notify_mgr.set_chat_id(uid, chat_id)
            if self._owner_telegram_id is not None and uid == self._owner_telegram_id:
                self._notify_mgr.set_owner(uid)

    # ------------------------------------------------------------------
    # Inline keyboard menu
    # ------------------------------------------------------------------
    def _build_menu(self, is_owner: bool) -> InlineKeyboardMarkup:
        buttons = [
            [InlineKeyboardButton("\U0001f4c5 View free slots", callback_data="myslots")],
            [InlineKeyboardButton("\U0001f4dd Book time", callback_data="book")],
            [InlineKeyboardButton("\U0001f4cb My bookings", callback_data="mybookings")],
            [InlineKeyboardButton("\u2753 Commands", callback_data="help")],
        ]
        if is_owner:
            buttons.extend([
                [InlineKeyboardButton("\U0001f4e5 Pending requests", callback_data="pending")],
                [InlineKeyboardButton(f"\u2699\ufe0f {self._owner_name}'s status", callback_data="status")],
            ])
        return InlineKeyboardMarkup(buttons)

    # ------------------------------------------------------------------
    # Build application (all handlers)
    # ------------------------------------------------------------------
    async def _build_app(self) -> Application:
        app = Application.builder().token(self.bot_token).build()

        owner = self._owner_name

        # ---------- /start ----------
        async def _start(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.username or str(uid)
            chat_id = update.effective_chat.id
            self._register(uid, name, chat_id)
            self._log("connect", uid, name, "/start")
            menu = self._build_menu(self._is_owner(uid))
            await update.message.reply_text(
                WELCOME.format(owner_name=owner, telegram_id=uid),
                reply_markup=menu,
            )

        # ---------- /menu ----------
        async def _menu(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, "/menu")
            menu = self._build_menu(self._is_owner(uid))
            await update.message.reply_text(
                "What would you like to do?",
                reply_markup=menu,
            )

        # ---------- /help ----------
        async def _help(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, "/help")
            text = HELP_TEXT.format(owner_name=owner)
            if self._is_owner(uid):
                text += OWNER_HELP
            await update.message.reply_text(text)

        # ---------- /myslots ----------
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
                await update.message.reply_text(
                    f"No free slots on {target_day.isoformat()}."
                )
                return

            lines = [f"Free slots for {target_day.isoformat()}:"]
            for s in free_slots[:10]:
                start = datetime.fromisoformat(s["start"])
                end = datetime.fromisoformat(s["end"])
                lines.append(
                    f"  {_fmt_time(start)} \u2013 {_fmt_time(end)} ({s['duration_minutes']}min)"
                )
            if len(free_slots) > 10:
                lines.append(f"  ... and {len(free_slots) - 10} more")
            await update.message.reply_text("\n".join(lines))

        # ---------- /book ----------
        async def _book(update: Update, context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            chat_id = update.effective_chat.id
            self._register(uid, name, chat_id)
            self._log("command", uid, name, update.message.text)

            args = context.args
            if not args:
                await update.message.reply_text(HELP_BOOK.format(owner_name=owner))
                return

            sched = getattr(self._notify_mgr, "schedule", None)
            if not sched:
                await update.message.reply_text(
                    "Sorry, the schedule system isn't available right now."
                )
                return

            text = " ".join(args)
            target_day = date.today()
            duration_minutes = 60

            if "tomorrow" in text:
                target_day = date.today() + timedelta(days=1)

            dur_match = re.search(r"(\d+)\s*h", text)
            if dur_match:
                duration_minutes = int(dur_match.group(1)) * 60
            dur_match_min = re.search(r"(\d+)\s*min", text)
            if dur_match_min:
                duration_minutes = int(dur_match_min.group(1))

            blocks = sched.list_day(target_day)
            free_slots = []
            if self._appointment_mgr:
                free_slots = self._appointment_mgr.find_free_slots(
                    target_day, blocks, duration_minutes
                )

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
                    start=_fmt_time(start_dt),
                    end=_fmt_time(end_dt),
                    title=f"Appointment with {name}",
                    note=text,
                )

            time_str = f"{_fmt_time(start_dt)}\u2013{_fmt_time(end_dt)}"
            await update.message.reply_text(
                f"\u2705 Booked you at {time_str} on {target_day.isoformat()}. "
                f"I'll notify {owner} about it."
            )

            if self._notify_mgr:
                await self._notify_mgr.notify_owner(
                    f"\U0001f4c5 Appointment request from {name}: {text}\n"
                    f"Time: {time_str} on {target_day.isoformat()}"
                )

        # ---------- /mybookings ----------
        async def _mybookings(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, "/mybookings")

            if not self._appointment_mgr:
                await update.message.reply_text("Appointment system not available.")
                return

            all_appts = self._appointment_mgr.list()
            mine = [a for a in all_appts if a.get("person_telegram_id") == uid
                    and a["status"] != "rejected"]

            if not mine:
                await update.message.reply_text("You have no upcoming bookings.")
                return

            lines = ["\U0001f4cb Your bookings:"]
            for a in mine:
                lines.append(
                    f"  #{a['id']} {a['day']} {a['start']}\u2013{a['end']} "
                    f"[\u2705 {a['status']}]"
                )
            await update.message.reply_text("\n".join(lines))

        # ---------- /cancel ----------
        async def _cancel(update: Update, context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, update.message.text)

            if not context.args:
                await update.message.reply_text(
                    "Usage: /cancel <id>\n"
                    "Find your booking ID with /mybookings"
                )
                return

            try:
                appt_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("Please provide a numeric ID.")
                return

            if not self._appointment_mgr:
                await update.message.reply_text("Appointment system not available.")
                return

            appt = self._appointment_mgr.get(appt_id)
            if not appt:
                await update.message.reply_text("Booking not found.")
                return
            if appt.get("person_telegram_id") != uid and not self._is_owner(uid):
                await update.message.reply_text("That's not your booking.")
                return

            self._appointment_mgr.delete(appt_id)
            await update.message.reply_text(f"\u274c Booking #{appt_id} cancelled.")
            if self._notify_mgr:
                await self._notify_mgr.notify_owner(
                    f"\u274c {name} cancelled booking #{appt_id} "
                    f"({appt['day']} {appt['start']}\u2013{appt['end']})"
                )

        # ---------- /pending (owner only) ----------
        async def _pending(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, "/pending")

            if not self._is_owner(uid):
                await update.message.reply_text("That command is only available to the owner.")
                return

            if not self._appointment_mgr:
                await update.message.reply_text("Appointment system not available.")
                return

            pending = self._appointment_mgr.list(status="pending")
            if not pending:
                await update.message.reply_text("No pending requests.")
                return

            lines = ["\U0001f4e5 Pending requests:"]
            for a in pending:
                lines.append(
                    f"  #{a['id']} \u2014 {a['person_label']}"
                    f"  {a['day']} {a['start']}\u2013{a['end']}"
                    f"  /confirm {a['id']}  /reject {a['id']}"
                )
            await update.message.reply_text("\n".join(lines))

        # ---------- /confirm (owner only) ----------
        async def _confirm(update: Update, context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, update.message.text)

            if not self._is_owner(uid):
                await update.message.reply_text("That command is only available to the owner.")
                return

            if not context.args:
                await update.message.reply_text("Usage: /confirm <id>")
                return

            try:
                appt_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("Please provide a numeric ID.")
                return

            if not self._appointment_mgr:
                await update.message.reply_text("Appointment system not available.")
                return

            appt = self._appointment_mgr.confirm(appt_id)
            if not appt:
                await update.message.reply_text("Appointment not found.")
                return

            await update.message.reply_text(
                f"\u2705 Booking #{appt_id} confirmed: {appt['day']} {appt['start']}\u2013{appt['end']}"
            )
            if self._appointment_mgr and appt.get("person_telegram_id"):
                await self.send_to_user(
                    appt["person_telegram_id"],
                    f"\u2705 Good news \u2014 {owner} confirmed your booking "
                    f"for {appt['day']} at {appt['start']}\u2013{appt['end']}!"
                )

        # ---------- /reject (owner only) ----------
        async def _reject(update: Update, context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, update.message.text)

            if not self._is_owner(uid):
                await update.message.reply_text("That command is only available to the owner.")
                return

            if not context.args:
                await update.message.reply_text("Usage: /reject <id>")
                return

            try:
                appt_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("Please provide a numeric ID.")
                return

            if not self._appointment_mgr:
                await update.message.reply_text("Appointment system not available.")
                return

            appt = self._appointment_mgr.reject(appt_id)
            if not appt:
                await update.message.reply_text("Appointment not found.")
                return

            await update.message.reply_text(
                f"\u274c Booking #{appt_id} rejected: {appt['day']} {appt['start']}\u2013{appt['end']}"
            )
            if appt.get("person_telegram_id"):
                await self.send_to_user(
                    appt["person_telegram_id"],
                    f"\u274c Sorry \u2014 {owner} had to reject your booking "
                    f"for {appt['day']} at {appt['start']}\u2013{appt['end']}. "
                    f"Try booking a different time with /myslots"
                )

        # ---------- /appointments (owner only) ----------
        async def _appointments_handler(update: Update, context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, update.message.text)

            if not self._is_owner(uid):
                await update.message.reply_text("That command is only available to the owner.")
                return

            target_day = date.today()
            if context.args:
                try:
                    target_day = date.fromisoformat(context.args[0])
                except ValueError:
                    pass

            if not self._appointment_mgr:
                await update.message.reply_text("Appointment system not available.")
                return

            appts = self._appointment_mgr.list(day=target_day)
            if not appts:
                await update.message.reply_text(
                    f"No appointments on {target_day.isoformat()}."
                )
                return

            lines = [f"Appointments for {target_day.isoformat()}:"]
            for a in appts:
                lines.append(
                    f"  #{a['id']} {a['start']}\u2013{a['end']} "
                    f"{a['person_label']} [\u2705 {a['status']}]"
                )
            await update.message.reply_text("\n".join(lines))

        # ---------- /status (owner only) ----------
        async def _status(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            self._register(uid, name, update.effective_chat.id)
            self._log("command", uid, name, "/status")

            if not self._is_owner(uid):
                await update.message.reply_text("That command is only available to the owner.")
                return

            state = self._busy_mgr.get_state() if self._busy_mgr else None
            if state and state.is_busy:
                note = f" ({state.note})" if state.note else ""
                await update.message.reply_text(
                    f"\U0001f6ab You're currently busy{note}."
                )
            else:
                await update.message.reply_text(
                    "\u2705 You're currently free."
                )

        # ---------- Free-text handler ----------
        async def _handle_text(update: Update, _context):
            user = update.effective_user
            uid, name = user.id, user.full_name or user.first_name or str(uid)
            text = update.message.text
            chat_id = update.effective_chat.id
            self._register(uid, name, chat_id)
            self._log("message", uid, name, text)
            await self._reply_status(update, uid, name, text)

        # ---------- Callback query (inline keyboard) ----------
        async def _handle_callback(update: Update, context):
            query = update.callback_query
            await query.answer()
            uid = query.from_user.id
            self._register(uid, query.from_user.full_name or str(uid), query.message.chat_id)
            self._log("callback", uid, str(query.from_user.full_name or uid), query.data)

            data = query.data
            if data == "myslots":
                await query.edit_message_text(
                    f"Send /myslots today to see {owner}'s free slots."
                )
            elif data == "book":
                await query.edit_message_text(
                    f"Send /book 1h tomorrow at 14:00 to book time with {owner}."
                )
            elif data == "mybookings":
                if not self._appointment_mgr:
                    await query.edit_message_text("Appointment system not available.")
                    return
                all_appts = self._appointment_mgr.list()
                mine = [a for a in all_appts if a.get("person_telegram_id") == uid
                        and a["status"] != "rejected"]
                if not mine:
                    await query.edit_message_text("You have no upcoming bookings.")
                    return
                lines = ["\U0001f4cb Your bookings:"]
                for a in mine:
                    lines.append(
                        f"  #{a['id']} {a['day']} {a['start']}\u2013{a['end']} "
                        f"[\u2705 {a['status']}]"
                    )
                await query.edit_message_text("\n".join(lines))
            elif data == "help":
                text = HELP_TEXT.format(owner_name=owner)
                if self._is_owner(uid):
                    text += OWNER_HELP
                await query.edit_message_text(text)
            elif data == "pending":
                if not self._is_owner(uid):
                    await query.edit_message_text("Not available.")
                    return
                if not self._appointment_mgr:
                    await query.edit_message_text("Appointment system not available.")
                    return
                pending = self._appointment_mgr.list(status="pending")
                if not pending:
                    await query.edit_message_text("No pending requests.")
                    return
                lines = ["\U0001f4e5 Pending:"]
                for a in pending:
                    lines.append(
                        f"  #{a['id']} {a['person_label']} "
                        f"{a['day']} {a['start']}\u2013{a['end']}"
                    )
                await query.edit_message_text("\n".join(lines))
            elif data == "status":
                if not self._is_owner(uid):
                    await query.edit_message_text("Not available.")
                    return
                state = self._busy_mgr.get_state() if self._busy_mgr else None
                if state and state.is_busy:
                    note = f" ({state.note})" if state.note else ""
                    await query.edit_message_text(f"\U0001f6ab Busy{note}.")
                else:
                    await query.edit_message_text("\u2705 Free.")

        # ---------- Register all handlers ----------
        app.add_handler(CommandHandler("start", _start))
        app.add_handler(CommandHandler("menu", _menu))
        app.add_handler(CommandHandler("help", _help))
        app.add_handler(CommandHandler("myslots", _myslots))
        app.add_handler(CommandHandler("book", _book))
        app.add_handler(CommandHandler("mybookings", _mybookings))
        app.add_handler(CommandHandler("cancel", _cancel))
        app.add_handler(CommandHandler("pending", _pending))
        app.add_handler(CommandHandler("confirm", _confirm))
        app.add_handler(CommandHandler("reject", _reject))
        app.add_handler(CommandHandler("appointments", _appointments_handler))
        app.add_handler(CommandHandler("status", _status))
        app.add_handler(CallbackQueryHandler(_handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))
        return app

    # ------------------------------------------------------------------
    # Reply to free-text messages
    # ------------------------------------------------------------------
    async def _reply_status(self, update: Update, uid: int, name: str, text: str):
        owner = self._owner_name
        state = self._busy_mgr.get_state() if self._busy_mgr else None

        if not state or not state.is_busy:
            await update.message.reply_text(
                f"{owner} is free right now \u2014 go ahead and message or call him."
            )
            return

        note = f" ({state.note})" if state.note else ""
        hint = self._free_hint()
        when = f" You should be able to reach him {hint}." if hint and hint != "right now" else ""

        priority = self._notify_mgr.get_priority(uid) if self._notify_mgr else "normal"
        custom = None
        if self._notify_mgr:
            custom = self._notify_mgr._get_user_row(uid).get("busy_message")

        if priority == "high":
            reply = custom or (
                f"{owner} is busy right now{note}, but you're a priority \u2014 "
                f"go ahead and message him, and he'll get back to you as soon as he can.{when}"
            )
            await update.message.reply_text(reply)
            if self._notify_mgr:
                await self._notify_mgr.notify_owner(
                    f"\U0001f4e8 High-priority: {name} just messaged the bot:\n{text}"
                )
        else:
            reply = custom or (
                f"{owner} is busy right now{note}. "
                f"He'll get back to you when he's free.{when}"
            )
            await update.message.reply_text(reply)

    def _free_hint(self) -> Optional[str]:
        sched = getattr(self._notify_mgr, "schedule", None) if self._notify_mgr else None
        if not sched:
            return None
        try:
            return sched.free_hint(datetime.now())
        except Exception as exc:
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

    # ------------------------------------------------------------------
    # Start / stop / polling
    # ------------------------------------------------------------------
    async def start(self):
        if self._started:
            return
        if not self.bot_token:
            logger.warning("No bot token \u2014 Telegram bot disabled.")
            return
        self._app = await self._build_app()
        await self._app.initialize()
        await self._app.start()
        self._stop_event = asyncio.Event()
        self._poll_task = asyncio.create_task(self._run_polling())
        self._started = True
        logger.info("Telegram bot started.")

    async def _close_stale_session(self):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{self.bot_token}/close",
                    timeout=10,
                )
                if r.status_code == 200:
                    logger.info("Closed stale Telegram session (ok)")
        except Exception as exc:
            logger.warning("Failed to close stale session: %s", exc)

    async def _run_polling(self):
        await self._close_stale_session()

        for attempt, delay in enumerate(_RETRY_DELAYS):
            try:
                await self._app.updater.start_polling()
                break
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning(
                    "Telegram polling attempt %d/%d failed: %s",
                    attempt + 1, len(_RETRY_DELAYS), exc,
                )
                if attempt < len(_RETRY_DELAYS) - 1:
                    await asyncio.sleep(delay)
        else:
            self._started = False
            return

        self._last_error = None

        while True:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=15)
                return
            except asyncio.TimeoutError:
                pass

            if not self._app.updater.running:
                logger.warning("Updater stopped \u2014 restarting...")
                await self._close_stale_session()
                for attempt, delay in enumerate(_RETRY_DELAYS):
                    try:
                        await self._app.updater.start_polling()
                        self._last_error = None
                        break
                    except Exception as exc:
                        self._last_error = str(exc)
                        logger.warning("Restart attempt %d failed: %s", attempt + 1, exc)
                        await asyncio.sleep(delay)
                else:
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

    # ------------------------------------------------------------------
    # Outbound messaging
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
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
