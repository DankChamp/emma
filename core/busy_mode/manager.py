"""
BusyModeManager.

"I'm busy" -> reduce interruptions, notify selected contacts, only let
important things through.
"I'm free" -> back to normal.

This is the thing ReminderManager consults (via the interrupt_check hook)
before actually firing a non-important reminder, and it's what future
notification code (desktop/voice) should check before interrupting you.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .messenger import ConsoleMessenger, MessengerAdapter
from .models import BusyState, NotifyContact

SCHEMA = """
CREATE TABLE IF NOT EXISTS busy_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    is_busy INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    since TEXT
);

CREATE TABLE IF NOT EXISTS notify_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    busy_message TEXT NOT NULL,
    free_message TEXT,
    created_at TEXT NOT NULL
);
"""


class BusyModeManager:
    def __init__(self, db_path: Path, messenger: Optional[MessengerAdapter] = None):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Ensure the single busy_state row exists.
        self.conn.execute(
            "INSERT OR IGNORE INTO busy_state (id, is_busy, note, since) VALUES (1, 0, NULL, NULL)"
        )
        self.conn.commit()
        self.messenger = messenger or ConsoleMessenger()

    # ---------- State ----------
    def get_state(self) -> BusyState:
        row = self.conn.execute("SELECT * FROM busy_state WHERE id=1").fetchone()
        return BusyState(
            is_busy=bool(row["is_busy"]),
            note=row["note"],
            since=datetime.fromisoformat(row["since"]) if row["since"] else None,
        )

    async def go_busy(self, note: Optional[str] = None) -> BusyState:
        now = datetime.utcnow()
        self.conn.execute(
            "UPDATE busy_state SET is_busy=1, note=?, since=? WHERE id=1", (note, now.isoformat())
        )
        self.conn.commit()
        for contact in self.list_contacts():
            await self.messenger.send(contact.name, contact.busy_message)
        return self.get_state()

    async def go_free(self) -> BusyState:
        contacts = self.list_contacts()
        self.conn.execute("UPDATE busy_state SET is_busy=0, note=NULL, since=NULL WHERE id=1")
        self.conn.commit()
        for contact in contacts:
            if contact.free_message:
                await self.messenger.send(contact.name, contact.free_message)
        return self.get_state()

    # ---------- Interruption gating ----------
    def should_interrupt(self, important: bool) -> bool:
        """
        The core rule: while busy, only important things get through.
        This is what ReminderManager (and future notification code) calls.
        """
        state = self.get_state()
        if not state.is_busy:
            return True
        return important

    # ---------- Contacts to auto-notify ----------
    def add_contact(self, name: str, busy_message: str, free_message: Optional[str] = None) -> NotifyContact:
        now = datetime.utcnow()
        self.conn.execute(
            """
            INSERT INTO notify_contacts (name, busy_message, free_message, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET busy_message=excluded.busy_message, free_message=excluded.free_message
            """,
            (name, busy_message, free_message, now.isoformat()),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM notify_contacts WHERE name=?", (name,)).fetchone()
        return self._row_to_contact(row)

    def list_contacts(self) -> list[NotifyContact]:
        rows = self.conn.execute("SELECT * FROM notify_contacts").fetchall()
        return [self._row_to_contact(r) for r in rows]

    def remove_contact(self, name: str) -> bool:
        cur = self.conn.execute("DELETE FROM notify_contacts WHERE name=?", (name,))
        self.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row_to_contact(row: sqlite3.Row) -> NotifyContact:
        return NotifyContact(
            id=row["id"],
            name=row["name"],
            busy_message=row["busy_message"],
            free_message=row["free_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
