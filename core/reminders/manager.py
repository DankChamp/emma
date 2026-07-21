"""
ReminderManager - reminders that actually fire.

Matches the reminders.db schema that already ships in data/. Delivery is a
single check_due() sweep that main.py runs every 30s on the shared
APScheduler - no per-reminder jobs to leak or lose on restart, and reminders
created while the server was down still fire on the next sweep.

Semantics per the spec:
  * trigger_at    - specific time ("remind me at 5pm") or now+duration
                    ("remind me in 20 minutes" - the API does the math)
  * repeat        - none | daily | weekly: after firing, trigger_at rolls
                    forward instead of the row being marked dismissed
  * persistent    - keep re-firing every `nag_minutes` until dismissed
  * important     - delivered even in busy mode; normal ones wait
Delivery: desktop notification (notify-send) + Telegram to the owner.
"""
import logging
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("emma.reminders")

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    trigger_at TEXT NOT NULL,
    repeat TEXT NOT NULL DEFAULT 'none',
    persistent INTEGER NOT NULL DEFAULT 0,
    important INTEGER NOT NULL DEFAULT 0,
    dismissed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

REPEATS = ("none", "daily", "weekly")
NAG_MINUTES = 5  # persistent reminders re-fire this often until dismissed


class ReminderManager:
    def __init__(self, db_path: Path, notifications=None, busy_mode=None):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        # Both optional and injected from main.py: notifications delivers to
        # Telegram, busy_mode gates non-important reminders.
        self.notifications = notifications
        self.busy_mode = busy_mode

    def _migrate(self) -> None:
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(reminders)")}
        if "last_fired_at" not in existing:
            self.conn.execute("ALTER TABLE reminders ADD COLUMN last_fired_at TEXT")
            self.conn.commit()

    # ---------- CRUD ----------
    def create(self, message: str, trigger_at: datetime, repeat: str = "none",
               persistent: bool = False, important: bool = False) -> dict:
        if repeat not in REPEATS:
            repeat = "none"
        cur = self.conn.execute(
            "INSERT INTO reminders (message, trigger_at, repeat, persistent, important, dismissed, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (message.strip(), trigger_at.isoformat(), repeat,
             int(persistent), int(important), datetime.now().isoformat()),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, reminder_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
        return dict(row) if row else None

    def list(self, include_dismissed: bool = False) -> list[dict]:
        sql = "SELECT * FROM reminders"
        if not include_dismissed:
            sql += " WHERE dismissed=0"
        rows = self.conn.execute(sql + " ORDER BY trigger_at ASC").fetchall()
        return [dict(r) for r in rows]

    def dismiss(self, reminder_id: int) -> bool:
        cur = self.conn.execute("UPDATE reminders SET dismissed=1 WHERE id=?", (reminder_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def delete(self, reminder_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- Firing ----------
    async def check_due(self) -> int:
        """Fire everything due. Called by the scheduler sweep. Returns count fired."""
        now = datetime.now()
        fired = 0
        for r in self.list():
            if datetime.fromisoformat(r["trigger_at"]) > now:
                continue
            # Persistent nag spacing: skip if we fired within the last NAG_MINUTES.
            if r["persistent"] and r["last_fired_at"]:
                last = datetime.fromisoformat(r["last_fired_at"])
                if now - last < timedelta(minutes=NAG_MINUTES):
                    continue
            # Busy mode holds back normal reminders; important ones go through.
            if not r["important"] and self.busy_mode is not None:
                try:
                    if self.busy_mode.get_state().is_busy:
                        continue
                except Exception:  # noqa: BLE001 - busy check must never block delivery
                    pass
            await self._deliver(r)
            self._after_fire(r, now)
            fired += 1
        return fired

    async def _deliver(self, r: dict) -> None:
        prefix = "❗ " if r["important"] else "⏰ "
        text = prefix + r["message"]
        self._desktop_notify(text, urgent=bool(r["important"]))
        if self.notifications:
            try:
                await self.notifications.notify_owner(text)
            except Exception as exc:  # noqa: BLE001 - Telegram down ≠ reminder lost
                logger.warning("Telegram delivery failed for reminder %s: %s", r["id"], exc)

    def _after_fire(self, r: dict, now: datetime) -> None:
        if r["repeat"] in ("daily", "weekly"):
            step = timedelta(days=1 if r["repeat"] == "daily" else 7)
            next_at = datetime.fromisoformat(r["trigger_at"])
            while next_at <= now:
                next_at += step
            self.conn.execute(
                "UPDATE reminders SET trigger_at=?, last_fired_at=? WHERE id=?",
                (next_at.isoformat(), now.isoformat(), r["id"]),
            )
        elif r["persistent"]:
            self.conn.execute(
                "UPDATE reminders SET last_fired_at=? WHERE id=?", (now.isoformat(), r["id"])
            )
        else:
            self.conn.execute(
                "UPDATE reminders SET dismissed=1, last_fired_at=? WHERE id=?",
                (now.isoformat(), r["id"]),
            )
        self.conn.commit()

    @staticmethod
    def _desktop_notify(text: str, urgent: bool = False) -> None:
        cmd = shutil.which("notify-send")
        if not cmd:
            return
        args = [cmd, "-a", "Emma"]
        if urgent:
            args += ["-u", "critical"]
        args += ["Emma", text]
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            logger.warning("notify-send failed: %s", exc)
