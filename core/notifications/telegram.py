from __future__ import annotations

import asyncio
import logging
import re
import time
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

from telegram.error import Conflict as TelegramConflict

from core.busy_mode import MessengerAdapter
from core.router.router import AIRouter, TaskType

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

    # Per-user rate limit: max AI calls per sliding window
    _AI_CALL_LIMIT = 30
    _AI_WINDOW_SEC = 3600  # 1 hour
    _MAX_MESSAGE_LEN = 500

    def __init__(
        self,
        bot_token: str,
        owner_name: str = "VOID",
        owner_telegram_id: Optional[int] = None,
        notify_manager=None,
        busy_manager=None,
        ai_router: Optional[AIRouter] = None,
        profile_mgr=None,
        task_mgr=None,
    ):
        self.bot_token = bot_token
        self._owner_name = owner_name
        self._owner_telegram_id = owner_telegram_id
        self._notify_mgr = notify_manager
        self._busy_mgr = busy_manager
        self._appointment_mgr = None
        self._ai_router = ai_router
        self._profile_mgr = profile_mgr
        self._task_mgr = task_mgr
        self._timetable_mgr = None
        self._app: Optional[Application] = None
        self._started = False
        self._stop_event: Optional[asyncio.Event] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._last_error: Optional[str] = None
        self._message_log: list[dict] = []
        self._ai_rate: dict[int, list[float]] = {}  # uid -> [timestamps]

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

            if len(text) > self._MAX_MESSAGE_LEN:
                await update.message.reply_text("Message too long. Please keep it under 500 characters.")
                return

            is_owner = self._is_owner(uid)

            # If owner is busy and caller is not high-priority/owner:
            # use the fast non-AI reply (no LLM cost)
            if not is_owner:
                try:
                    state = self._busy_mgr.get_state() if self._busy_mgr else None
                except Exception:
                    state = None
                is_busy = bool(state and state.is_busy)
                if is_busy:
                    priority = "normal"
                    if self._notify_mgr:
                        priority = self._notify_mgr.get_priority(uid) or "normal"
                    if priority != "high":
                        await self._reply_status(update, uid, name, text)
                        return

                # Guard off-topic messages before hitting the AI
                if self._is_off_topic(text):
                    await update.message.reply_text(
                        f"I can only help with {self._owner_name}'s schedule. "
                        f"Send /help to see available commands."
                    )
                    return

                # Rate limit check
                if not self._check_rate_limit(uid):
                    await update.message.reply_text(
                        "You've sent too many messages. Please wait an hour and try again."
                    )
                    return

            # If AI router isn't available, fall back to simple status reply
            if not self._ai_router:
                await self._reply_status(update, uid, name, text)
                return

            try:
                ctx = self._build_ai_context(uid, name, text)
                system = self._ai_system_prompt(ctx)
                result = await self._ai_router.run(
                    TaskType.CONVERSATION,
                    text,
                    system=system,
                )
                reply = result.text.strip()

                # Parse and execute any [ACTION:] directive
                action_match = re.search(r'\[ACTION:\s*(.*?)\]', reply)
                if action_match:
                    action_tag = action_match.group(1).strip()
                    reply_clean = re.sub(r'\s*\[ACTION:\s*.*?\]\s*', '', reply).strip()
                    action_result = await self._execute_action(
                        action_tag, uid, name,
                        reply_text=reply_clean,
                    )
                    reply = reply_clean
                    if action_result:
                        # send_to replaces the reply (message was forwarded to recipient)
                        if action_tag.lower().startswith("send_to"):
                            reply = action_result
                        elif reply:
                            reply = reply + "\n\n" + action_result
                        else:
                            reply = action_result

            except Exception as exc:
                logger.warning("AI handler failed for %s (%d): %s", name, uid, exc)
                await self._reply_status(update, uid, name, text)
                return

            if not reply:
                reply = f"I can only help with {self._owner_name}'s schedule. Send /help to see available commands."

            await update.message.reply_text(reply)

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

    # ------------------------------------------------------------------
    # Rate limiting for AI calls
    # ------------------------------------------------------------------
    def _check_rate_limit(self, uid: int) -> bool:
        now = time.time()
        window = self._AI_WINDOW_SEC
        limit = self._AI_CALL_LIMIT
        timestamps = self._ai_rate.get(uid, [])
        timestamps = [t for t in timestamps if now - t < window]
        self._ai_rate[uid] = timestamps
        if len(timestamps) >= limit:
            return False
        timestamps.append(now)
        return True

    # ------------------------------------------------------------------
    # AI system prompt builder
    # ------------------------------------------------------------------
    def _build_ai_context(self, uid: int, name: str, text: str) -> dict:
        owner = self._owner_name
        state = self._busy_mgr.get_state() if self._busy_mgr else None
        is_busy = bool(state and state.is_busy)
        note = state.note if state and state.note else None

        today = date.today().isoformat()
        now_str = datetime.now().strftime("%H:%M")
        return {
            "owner_name": owner,
            "is_owner": self._is_owner(uid),
            "caller_name": name,
            "caller_uid": uid,
            "is_busy": is_busy,
            "busy_note": note,
            "today": today,
            "now": now_str,
            "message": text,
            "owner_telegram_id": self._owner_telegram_id,
        }

    def _ai_system_prompt(self, ctx: dict) -> str:
        if ctx["is_owner"]:
            return self._owner_system_prompt(ctx)
        return self._guest_system_prompt(ctx)

    def _owner_system_prompt(self, ctx: dict) -> str:
        busy_str = "busy" if ctx["is_busy"] else "free"
        note_str = f" ({ctx['busy_note']})" if ctx["busy_note"] else ""
        return (
            f"You are Emma, VOID's personal AI assistant. "
            f"VOID is your owner. Refer to him as VOID. Use male pronouns (he/him/his) for him.\n\n"
            f"You help VOID manage his time, build daily schedules, organize his day, "
            f"and communicate with his contacts. You can chat casually with him.\n\n"
            f"You MUST obey VOID's commands. When he tells you to do something, do it.\n\n"
            f"Context:\n"
            f"- VOID: {busy_str}{note_str}\n"
            f"- Today: {ctx['today']} {ctx['now']}\n\n"
            f"Actions (put ONE at end of your response):\n"
            f"[ACTION: status] — check busy/free status\n"
            f"[ACTION: slots today|tomorrow|YYYY-MM-DD] — free slots\n"
            f"[ACTION: book DURATION DAY TIME] — book appointment\n"
            f"[ACTION: mybookings] — view your bookings\n"
            f"[ACTION: cancel ID] — cancel a booking\n"
            f"[ACTION: pending] — pending appointment requests\n"
            f"[ACTION: build_schedule DAYS] — generate timetable for N days\n"
            f"[ACTION: my_day] — show today's schedule\n"
            f"[ACTION: my_appointments DAY] — view appointments for a day\n"
            f"[ACTION: send_to NAME] — send a message to a contact. Put the message in your response text, send_to carries only the name.\n\n"
            f"RULES:\n"
            f"- Be concise but friendly. VOID is your owner, not a customer.\n"
            f"- Refer to him as VOID.\n"
            f"- Only use [ACTION:] when VOID wants you to DO something.\n"
            f"- For send_to, your response text IS the message that gets delivered.\n"
            f"- NEVER make up availability or booking info."
        )

    def _guest_system_prompt(self, ctx: dict) -> str:
        busy_str = "busy" if ctx["is_busy"] else "free"
        note_str = f" ({ctx['busy_note']})" if ctx["busy_note"] else ""
        return (
            f"You are Emma, a scheduling assistant for {ctx['owner_name']}. "
            f"You ONLY handle:\n"
            f"1. Check if {ctx['owner_name']} is busy/free\n"
            f"2. Book appointments\n"
            f"3. List free slots\n"
            f"4. View/cancel caller's bookings\n"
            f"5. Pending requests (owner only)\n\n"
            f"Context:\n"
            f"- {ctx['owner_name']}: {busy_str}{note_str}\n"
            f"- Today: {ctx['today']} {ctx['now']}\n"
            f"- Caller: {ctx['caller_name']}\n\n"
            f"Actions (put ONE at end of your response):\n"
            f"[ACTION: status]\n"
            f"[ACTION: slots today|tomorrow|YYYY-MM-DD]\n"
            f"[ACTION: book DURATION DAY TIME]  e.g. book 1h 2026-07-27 14:00\n"
            f"[ACTION: mybookings]\n"
            f"[ACTION: cancel ID]\n"
            f"[ACTION: pending]  (owner only)\n\n"
            f"RULES:\n"
            f"- Max 3 sentences. No chit-chat. No off-topic.\n"
            f"- If off-topic: \"I can only help with {ctx['owner_name']}'s schedule. Send /help.\"\n"
            f"- Only use [ACTION:] when user wants to DO something.\n"
            f"- For greetings, respond briefly then ask about the schedule.\n"
            f"- NEVER make up availability or booking info."
        )

    # ------------------------------------------------------------------
    # Parse and execute [ACTION:] directives from AI response
    # ------------------------------------------------------------------
    async def _execute_action(
        self, action: str, uid: int, name: str, reply_text: str = ""
    ) -> Optional[str]:
        m = re.match(r"(\w+)\s*(.*)", action)
        if not m:
            return None
        cmd, args = m.group(1).lower(), m.group(2).strip()

        if cmd == "status":
            return self._action_status()

        if cmd == "slots":
            return await self._action_slots(args)

        if cmd == "book":
            return await self._action_book(args, uid, name)

        if cmd == "mybookings":
            return self._action_mybookings(uid)

        if cmd == "cancel":
            return await self._action_cancel(args, uid, name)

        if cmd == "pending":
            return await self._action_pending(uid)

        if cmd == "build_schedule":
            return await self._action_build_schedule(args, context=reply_text)

        if cmd == "my_day":
            return await self._action_my_day()

        if cmd == "my_appointments":
            return self._action_my_appointments(args)

        if cmd == "send_to":
            return await self._action_send_to(args, reply_text)

        return None

    def _action_status(self) -> str:
        state = self._busy_mgr.get_state() if self._busy_mgr else None
        if state and state.is_busy:
            note = f" ({state.note})" if state.note else ""
            return f"\U0001f6ab {self._owner_name} is currently busy{note}."
        return f"\u2705 {self._owner_name} is currently free."

    async def _action_slots(self, args: str) -> Optional[str]:
        if not self._appointment_mgr:
            return None
        if not self._notify_mgr:
            return None
        sched = self._notify_mgr.schedule
        if not sched:
            return None
        target = date.today()
        clean = args.lower().strip()
        if clean in ("tomorrow",):
            target = date.today() + timedelta(days=1)
        elif clean:
            try:
                target = date.fromisoformat(clean)
            except ValueError:
                pass
        blocks = sched.list_day(target)
        free_slots = self._appointment_mgr.find_free_slots(target, blocks, 15)
        if not free_slots:
            return f"No free slots on {target.isoformat()}."
        lines = [f"Free slots for {target.isoformat()}:"]
        for s in free_slots[:10]:
            st = datetime.fromisoformat(s["start"])
            en = datetime.fromisoformat(s["end"])
            lines.append(f"  {_fmt_time(st)} \u2013 {_fmt_time(en)} ({s['duration_minutes']}min)")
        if len(free_slots) > 10:
            lines.append(f"  ... and {len(free_slots) - 10} more")
        return "\n".join(lines)

    async def _action_book(self, args: str, uid: int, name: str) -> Optional[str]:
        if not self._appointment_mgr:
            return "Appointment system not available."
        if not self._notify_mgr:
            return "Schedule system not available."
        sched = self._notify_mgr.schedule
        if not sched:
            return "Schedule system not available."

        # Flexible parsing: extract duration, date, time from anywhere in args
        dur_match = re.search(r"(\d+)\s*h(?:ours?)?", args)
        dur_match_min = re.search(r"(\d+)\s*min(?:utes?)?", args)
        duration_minutes = (int(dur_match.group(1)) * 60 if dur_match else 0) + (int(dur_match_min.group(1)) if dur_match_min else 0)
        if duration_minutes == 0:
            duration_minutes = 60  # default if no duration found

        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", args)
        if not date_match:
            return None
        target_day = date.fromisoformat(date_match.group(1))

        time_match = re.search(r"(\d{1,2}):(\d{2})", args)
        if not time_match:
            return None
        start_h, start_m = int(time_match.group(1)), int(time_match.group(2))

        start_dt = datetime(target_day.year, target_day.month, target_day.day, start_h, start_m)
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        # Check slot availability
        blocks = sched.list_day(target_day)
        free_slots = self._appointment_mgr.find_free_slots(target_day, blocks, duration_minutes)
        slot_free = any(
            datetime.fromisoformat(s["start"]) == start_dt
            for s in free_slots
        )
        if not slot_free:
            return f"Sorry, that slot isn't available on {target_day.isoformat()}. Try /myslots {target_day.isoformat()} to see free times."

        self._appointment_mgr.create(
            person_label=name,
            person_telegram_id=uid,
            day=target_day,
            start=_fmt_time(start_dt),
            end=_fmt_time(end_dt),
            title=f"Appointment with {name}",
            note=f"AI-booking: {args}",
        )
        self._log("ai_book", uid, name, f"booked {target_day} {_fmt_time(start_dt)}-{_fmt_time(end_dt)}")
        if self._notify_mgr:
            await self._notify_mgr.notify_owner(
                f"\U0001f4c5 AI booking from {name}: {args}"
            )
        return f"\u2705 Booked you {_fmt_time(start_dt)}\u2013{_fmt_time(end_dt)} on {target_day.isoformat()}. I'll notify {self._owner_name}."

    def _action_mybookings(self, uid: int) -> Optional[str]:
        if not self._appointment_mgr:
            return "Appointment system not available."
        all_appts = self._appointment_mgr.list()
        mine = [a for a in all_appts if a.get("person_telegram_id") == uid
                and a["status"] != "rejected"]
        if not mine:
            return "You have no upcoming bookings."
        lines = ["\U0001f4cb Your bookings:"]
        for a in mine:
            lines.append(f"  #{a['id']} {a['day']} {a['start']}\u2013{a['end']} [\u2705 {a['status']}]")
        return "\n".join(lines)

    async def _action_cancel(self, args: str, uid: int, name: str) -> Optional[str]:
        if not self._appointment_mgr:
            return "Appointment system not available."
        try:
            appt_id = int(args.strip())
        except ValueError:
            return None
        appt = self._appointment_mgr.get(appt_id)
        if not appt:
            return f"Booking #{appt_id} not found."
        if appt.get("person_telegram_id") != uid and not self._is_owner(uid):
            return "That's not your booking."
        self._appointment_mgr.delete(appt_id)
        self._log("ai_cancel", uid, name, f"cancelled #{appt_id}")
        if self._notify_mgr:
            await self._notify_mgr.notify_owner(
                f"\u274c {name} cancelled booking #{appt_id} via AI"
            )
        return f"\u274c Booking #{appt_id} cancelled."

    async def _action_pending(self, uid: int) -> Optional[str]:
        if not self._is_owner(uid):
            return None
        if not self._appointment_mgr:
            return "Appointment system not available."
        pending = self._appointment_mgr.list(status="pending")
        if not pending:
            return "No pending requests."
        lines = ["\U0001f4e5 Pending requests:"]
        for a in pending:
            lines.append(
                f"  #{a['id']} \u2014 {a['person_label']}"
                f"  {a['day']} {a['start']}\u2013{a['end']}"
                f"  /confirm {a['id']}  /reject {a['id']}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Owner-only actions
    # ------------------------------------------------------------------
    async def _action_build_schedule(self, args: str, context: str = "") -> Optional[str]:
        if not self._timetable_mgr:
            return "Schedule builder not available."
        days = 3
        if args.strip():
            try:
                days = max(1, min(14, int(args.strip().split()[0])))
            except ValueError:
                pass
        profile_data = self._profile_mgr.get_all() if self._profile_mgr else None
        pending_tasks = None
        if self._task_mgr:
            try:
                pending_tasks = self._task_mgr.list(status="pending")
            except Exception:
                pass
        prompt = context or f"Generate a {days}-day schedule."
        try:
            results = await self._timetable_mgr.build_multi_day(
                text=prompt,
                days=days,
                profile=profile_data,
                pending_tasks=pending_tasks,
            )
        except Exception as exc:
            logger.warning("build_multi_day failed: %s", exc)
            return "Sorry, I couldn't generate the schedule right now."
        if not results:
            return "No schedule generated."
        lines = []
        for day_str, blocks in results.items():
            lines.append(f"\n\U0001f4c5 {day_str}")
            if not blocks:
                lines.append("  No blocks scheduled.")
                continue
            for b in blocks:
                start = b.start.strftime("%H:%M")
                end = b.end.strftime("%H:%M")
                lines.append(f"  \U0001f534 {start}\u2013{end}  {b.title}")
        return "\n".join(lines)

    def _find_user_by_name(self, name: str) -> Optional[int]:
        if not self._notify_mgr:
            return None
        users = self._notify_mgr.list_users()
        name_lower = name.lower().strip()
        # Exact case-insensitive match on label
        for u in users:
            if u.get("label", "").lower() == name_lower:
                return u.get("chat_id")
        # Exact case-insensitive match on name
        for u in users:
            if u.get("name", "").lower() == name_lower:
                return u.get("chat_id")
        # Partial match: name is prefix of label/name
        for u in users:
            if u.get("label", "").lower().startswith(name_lower):
                return u.get("chat_id")
        for u in users:
            if u.get("name", "").lower().startswith(name_lower):
                return u.get("chat_id")
        return None

    async def _action_send_to(self, args: str, message: str) -> Optional[str]:
        if not args.strip():
            return "Tell me who to send it to."
        parts = args.strip().split(maxsplit=1)
        recipient_name = parts[0]
        chat_id = self._find_user_by_name(recipient_name)
        if not chat_id:
            known = []
            if self._notify_mgr:
                for u in self._notify_mgr.list_users():
                    label = u.get("label") or u.get("name") or ""
                    known.append(label)
            names = ", ".join(known[:10]) if known else "none registered"
            return f"Couldn't find \"{recipient_name}\". Known contacts: {names}"
        ok = await self.send_to_chat(chat_id, f"\U0001f4e9 From {self._owner_name}:\n\n{message}")
        if ok:
            return f"\u2705 Sent to {recipient_name}."
        return f"Failed to send to {recipient_name}."

    def _action_my_day(self) -> Optional[str]:
        if not self._notify_mgr or not self._notify_mgr.schedule:
            return "Schedule not available."
        today = date.today()
        blocks = self._notify_mgr.schedule.list_day(today)
        if not blocks:
            return f"No blocks scheduled for {today.strftime('%A, %b %d')}."
        lines = [f"\U0001f4c5 Today — {today.strftime('%A, %b %d')}"]
        for b in blocks:
            icon = "\U0001f534" if b.busy else "\U0001f7e2"
            lines.append(f"  {icon} {b.start.strftime('%H:%M')}\u2013{b.end.strftime('%H:%M')}  {b.title}")
        return "\n".join(lines)

    def _action_my_appointments(self, args: str) -> Optional[str]:
        if not self._appointment_mgr:
            return "Appointment system not available."
        target = date.today()
        clean = args.strip().lower()
        if clean in ("tomorrow",):
            target = date.today() + timedelta(days=1)
        elif clean:
            try:
                target = date.fromisoformat(clean)
            except ValueError:
                pass
        appts = self._appointment_mgr.list(day=target)
        if not appts:
            return f"No appointments on {target.isoformat()}."
        lines = [f"\U0001f4cb Appointments for {target.isoformat()}:"]
        for a in appts:
            lines.append(f"  #{a['id']} {a['start']}\u2013{a['end']}  {a.get('title', '')}  [{a['status']}]")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Off-topic guard
    # ------------------------------------------------------------------
    _SCHEDULE_KEYWORDS = {
        "free", "busy", "book", "slot", "schedule", "appointment", "available",
        "cancel", "booking", "meeting", "time", "when", "today", "tomorrow",
        "mybookings", "pending", "help", "hi", "hello", "hey", "status",
        "confirm", "reject",
    }

    def _is_off_topic(self, text: str) -> bool:
        words = set(re.sub(r"[^a-z0-9\s]", "", text.lower()).split())
        return not bool(words & self._SCHEDULE_KEYWORDS)

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
                await self._app.updater.start_polling(
                    drop_pending_updates=True,
                    bootstrap_retries=3,
                )
                break
            except TelegramConflict:
                logger.warning("Telegram Conflict (attempt %d) \u2014 closing stale session...", attempt + 1)
                await self._close_stale_session()
                await asyncio.sleep(delay)
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
                        await self._app.updater.start_polling(
                            drop_pending_updates=True,
                            bootstrap_retries=3,
                        )
                        self._last_error = None
                        break
                    except TelegramConflict:
                        logger.warning("Restart conflict \u2014 closing stale session...")
                        await self._close_stale_session()
                        await asyncio.sleep(delay)
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
