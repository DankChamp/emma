"""
NotificationManager — tracks Telegram users and their chat IDs, and is the
single source of truth for "people" in Emma.

Auto-registers anyone who messages the bot. Beyond knowing who they are, each
person carries a priority, whether Emma should message them when you go
busy/free, optional custom messages, and one of them is flagged as the owner
(you) — the chat where reminders and high-priority alerts are delivered.
"""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .telegram import TelegramMessenger

logger = logging.getLogger("emma.notifications")

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    chat_id INTEGER,
    name TEXT NOT NULL,
    label TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'friend',
    can_receive INTEGER NOT NULL DEFAULT 1,
    priority TEXT NOT NULL DEFAULT 'normal',
    notify_on_busy INTEGER NOT NULL DEFAULT 0,
    busy_message TEXT,
    free_message TEXT,
    is_owner INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_seen TEXT
);
"""

# Columns added after the first release; applied idempotently on startup so an
# existing notifications.db upgrades without a manual migration.
_ADDED_COLUMNS = [
    ("priority", "TEXT NOT NULL DEFAULT 'normal'"),
    ("notify_on_busy", "INTEGER NOT NULL DEFAULT 0"),
    ("busy_message", "TEXT"),
    ("free_message", "TEXT"),
    ("is_owner", "INTEGER NOT NULL DEFAULT 0"),
    ("prompt", "TEXT"),
]


class NotificationManager:
    def __init__(self, db_path: Path, telegram: Optional[TelegramMessenger] = None):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.telegram = telegram
        # Wired in main.py; lets availability replies quote your next free slot.
        self.schedule = None

    def _migrate(self) -> None:
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(telegram_users)")}
        for col, decl in _ADDED_COLUMNS:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE telegram_users ADD COLUMN {col} {decl}")
        self.conn.commit()

    # ---------- Registration ----------
    def register_user(self, telegram_id: int, name: str, label: str = "", role: str = "friend") -> dict:
        now = datetime.utcnow().isoformat()
        label = label or name
        self.conn.execute(
            """
            INSERT INTO telegram_users (telegram_id, name, label, role, can_receive, created_at, last_seen)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                name=excluded.name, last_seen=excluded.last_seen
            """,
            (telegram_id, name, label, role, now, now),
        )
        self.conn.commit()
        return self._get_user_row(telegram_id)

    def set_chat_id(self, telegram_id: int, chat_id: int) -> None:
        self.conn.execute(
            "UPDATE telegram_users SET chat_id=? WHERE telegram_id=?",
            (chat_id, telegram_id),
        )
        self.conn.commit()

    def get_chat_id(self, telegram_id: int) -> Optional[int]:
        row = self.conn.execute(
            "SELECT chat_id FROM telegram_users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        return row["chat_id"] if row and row["chat_id"] else None

    def get_chat_id_by_label(self, label: str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT chat_id FROM telegram_users WHERE label=? AND chat_id IS NOT NULL",
            (label,),
        ).fetchone()
        return row["chat_id"] if row else None

    # ---------- Per-person settings ----------
    def set_label(self, telegram_id: int, label: str, role: str) -> None:
        self.conn.execute(
            "UPDATE telegram_users SET label=?, role=? WHERE telegram_id=?",
            (label, role, telegram_id),
        )
        self.conn.commit()

    def set_priority(self, telegram_id: int, priority: str) -> None:
        if priority not in ("high", "normal", "low"):
            priority = "normal"
        self.conn.execute(
            "UPDATE telegram_users SET priority=? WHERE telegram_id=?", (priority, telegram_id)
        )
        self.conn.commit()

    def set_notify_on_busy(self, telegram_id: int, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE telegram_users SET notify_on_busy=? WHERE telegram_id=?",
            (int(bool(enabled)), telegram_id),
        )
        self.conn.commit()

    def set_custom_messages(self, telegram_id: int, busy_message: Optional[str], free_message: Optional[str]) -> None:
        self.conn.execute(
            "UPDATE telegram_users SET busy_message=?, free_message=? WHERE telegram_id=?",
            (busy_message or None, free_message or None, telegram_id),
        )
        self.conn.commit()

    def set_prompt(self, telegram_id: int, prompt: Optional[str]) -> None:
        self.conn.execute(
            "UPDATE telegram_users SET prompt=? WHERE telegram_id=?",
            (prompt or None, telegram_id),
        )
        self.conn.commit()

    def set_owner(self, telegram_id: int) -> None:
        """Mark one person as the owner (you). Clears any previous owner."""
        self.conn.execute("UPDATE telegram_users SET is_owner=0")
        self.conn.execute("UPDATE telegram_users SET is_owner=1 WHERE telegram_id=?", (telegram_id,))
        self.conn.commit()

    def get_priority(self, telegram_id: int) -> str:
        row = self.conn.execute(
            "SELECT priority FROM telegram_users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        return row["priority"] if row else "normal"

    def get_owner_chat_id(self) -> Optional[int]:
        row = self.conn.execute(
            "SELECT chat_id FROM telegram_users WHERE is_owner=1 AND chat_id IS NOT NULL"
        ).fetchone()
        return row["chat_id"] if row else None

    def list_users(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM telegram_users ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_notify_targets(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM telegram_users WHERE notify_on_busy=1 AND chat_id IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_user_row(self, telegram_id: int) -> dict:
        row = self.conn.execute(
            "SELECT * FROM telegram_users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        return dict(row) if row else {}

    # ---------- Outbound messaging ----------
    async def broadcast_availability(self, is_busy: bool, note: Optional[str] = None) -> int:
        """Message everyone flagged notify-on-busy that you've gone busy/free.

        Uses each person's custom message when they have one, otherwise a
        sensible default that folds in your next-free time from the timetable.
        Returns how many messages were sent.
        """
        if not self.telegram:
            return 0
        owner = getattr(self.telegram, "_owner_name", "Champ")
        free_hint = self._free_hint()
        note_txt = f" ({note})" if note else ""
        sent = 0
        for user in self.list_notify_targets():
            if is_busy:
                msg = user.get("busy_message") or self._default_busy(note_txt, free_hint, owner)
            else:
                msg = user.get("free_message") or f"{owner} is free now — feel free to reach out."
            if await self.telegram.send_to_user(user["telegram_id"], msg):
                sent += 1
        return sent

    async def notify_user(self, label: str, text: str) -> bool:
        """Send a message to the first Telegram user whose name/label matches.

        Falls back to the owner chat if no match is found.
        """
        if not self.telegram:
            return False
        chat_id = self.get_chat_id_by_label(label)
        if not chat_id:
            chat_id = self.get_owner_chat_id()
        if not chat_id:
            return False
        return await self.telegram.send_to_chat(chat_id, text)

    async def notify_owner(self, text: str) -> bool:
        """Send a message to the owner's chat (reminders, high-priority alerts)."""
        chat_id = self.get_owner_chat_id()
        if not chat_id or not self.telegram:
            return False
        return await self.telegram.send_to_chat(chat_id, text)

    # ---------- helpers ----------
    def _free_hint(self) -> Optional[str]:
        if not self.schedule:
            return None
        try:
            return self.schedule.free_hint(datetime.now())
        except Exception as exc:  # noqa: BLE001 - never let scheduling break a broadcast
            logger.warning("free_hint failed: %s", exc)
            return None

    @staticmethod
    def _default_busy(note_txt: str, free_hint: Optional[str], owner: str = "Champ") -> str:
        base = f"Heads up — {owner} is busy right now{note_txt}. He'll get back to you when he's free."
        if free_hint and free_hint.startswith("after"):
            base += f" You should be able to reach him {free_hint}."
        return base

    # ---------- Bot lifecycle ----------
    async def start_bot(self):
        if self.telegram and not self.telegram.is_running:
            await self.telegram.start()

    async def stop_bot(self):
        if self.telegram and self.telegram.is_running:
            await self.telegram.stop()

    @property
    def bot_status(self) -> dict:
        return {
            "running": self.telegram.is_running if self.telegram else False,
            "has_token": bool(self.telegram.bot_token) if self.telegram else False,
        }

    def get_message_log(self) -> list[dict]:
        return self.telegram.message_log if self.telegram else []
