from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_label TEXT NOT NULL,
    person_telegram_id INTEGER,
    day TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);
"""


class AppointmentManager:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def create(self, person_label: str, person_telegram_id: Optional[int],
               day: date, start: str, end: str, title: str = "",
               note: str = "") -> dict:
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute(
            "INSERT INTO appointments (person_label, person_telegram_id, day, start, end, "
            "title, status, note, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (person_label, person_telegram_id, day.isoformat(), start, end,
             title, note, now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM appointments WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def confirm(self, appointment_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM appointments WHERE id=?", (appointment_id,)
        ).fetchone()
        if not row:
            return None
        self.conn.execute(
            "UPDATE appointments SET status='confirmed', confirmed_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), appointment_id),
        )
        self.conn.commit()
        return self.get(appointment_id)

    def reject(self, appointment_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM appointments WHERE id=?", (appointment_id,)
        ).fetchone()
        if not row:
            return None
        self.conn.execute(
            "UPDATE appointments SET status='rejected' WHERE id=?", (appointment_id,)
        )
        self.conn.commit()
        return self.get(appointment_id)

    def get(self, appointment_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM appointments WHERE id=?", (appointment_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(self, status: Optional[str] = None, day: Optional[date] = None) -> list[dict]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        if day:
            where.append("day=?")
            params.append(day.isoformat())
        sql = "SELECT * FROM appointments"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY day, start"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def find_free_slots(self, day: date, busy_blocks: list, min_duration_minutes: int = 30) -> list[dict]:
        """Find free time slots on a given day, given busy blocks."""
        slots = []
        day_start = datetime.combine(day, datetime.min.time().replace(hour=7))
        day_end = datetime.combine(day, datetime.min.time().replace(hour=23))

        busy_periods = []
        for b in busy_blocks:
            if hasattr(b, "start") and hasattr(b, "end"):
                if b.busy:
                    busy_periods.append((b.start, b.end))

        busy_periods.sort()
        cursor = day_start
        for bs, be in busy_periods:
            if bs > cursor:
                gap = (bs - cursor).total_seconds() / 60
                if gap >= min_duration_minutes:
                    slots.append({
                        "start": cursor.isoformat(),
                        "end": bs.isoformat(),
                        "duration_minutes": int(gap),
                    })
            cursor = max(cursor, be)

        if day_end > cursor:
            gap = (day_end - cursor).total_seconds() / 60
            if gap >= min_duration_minutes:
                slots.append({
                    "start": cursor.isoformat(),
                    "end": day_end.isoformat(),
                    "duration_minutes": int(gap),
                })

        return slots

    def delete(self, appointment_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM appointments WHERE id=?", (appointment_id,))
        self.conn.commit()
        return cur.rowcount > 0
