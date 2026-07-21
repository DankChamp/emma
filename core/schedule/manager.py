"""
TimetableManager — Emma's daily timetable.

A timetable is just an ordered set of TimeBlocks for a given day. Two things
consume it:
  * the Schedule tab (create/edit/view the day), and
  * the Telegram bot, which reads it to tell people when you're next free.

`build_from_text` turns a freeform "here's what's on today" note into a
structured schedule using the AI router, and degrades gracefully to a naive
sequential layout when no provider is available (per the router's own
resilience, cf. core/router/router.py).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Optional

from .models import TimeBlock

logger = logging.getLogger("emma.schedule")

SCHEMA = """
CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    title TEXT NOT NULL,
    busy INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_timetable_day ON timetable(day);
"""

_DEFAULT_START_HOUR = 9


def format_time(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{h}:{dt.minute:02d} {ampm}"


class TimetableManager:
    def __init__(self, db_path: Path, ai_router=None):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.ai_router = ai_router
        self._on_block_notify: Optional[Callable[[str, datetime], None]] = None

    def set_block_notify_callback(self, callback: Callable[[str, datetime], None]) -> None:
        """Called for each future busy block when saving a schedule."""
        self._on_block_notify = callback

    def set_day(self, day: date, blocks: list[dict], create_reminders: bool = False) -> list[TimeBlock]:
        now_dt = datetime.utcnow()
        self.conn.execute("DELETE FROM timetable WHERE day=?", (day.isoformat(),))
        for b in blocks:
            start = self._combine(day, b["start"])
            end = self._combine(day, b.get("end") or b["start"])
            self.conn.execute(
                "INSERT INTO timetable (day, start, end, title, busy, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (day.isoformat(), start.isoformat(), end.isoformat(),
                 b.get("title", "").strip() or "Busy", int(bool(b.get("busy", True))), now_dt.isoformat()),
            )
        self.conn.commit()
        saved = self.list_day(day)
        if create_reminders and self._on_block_notify:
            now = datetime.now()
            for b in saved:
                if b.busy and b.start > now:
                    self._on_block_notify(b.title, b.start)
        return saved

    def list_day(self, day: date) -> list[TimeBlock]:
        rows = self.conn.execute(
            "SELECT * FROM timetable WHERE day=? ORDER BY start ASC", (day.isoformat(),)
        ).fetchall()
        return [self._row_to_block(r) for r in rows]

    def delete_day(self, day: date) -> None:
        self.conn.execute("DELETE FROM timetable WHERE day=?", (day.isoformat(),))
        self.conn.commit()

    def current_busy_block(self, dt: datetime) -> Optional[TimeBlock]:
        for block in self.list_day(dt.date()):
            if block.busy and block.start <= dt < block.end:
                return block
        return None

    def next_free(self, dt: datetime) -> Optional[datetime]:
        block = self.current_busy_block(dt)
        if not block:
            return None
        free_at = block.end
        blocks = [b for b in self.list_day(dt.date()) if b.busy]
        advanced = True
        while advanced:
            advanced = False
            for b in blocks:
                if b.start <= free_at < b.end:
                    free_at = b.end
                    advanced = True
        return free_at

    def free_hint(self, dt: datetime) -> Optional[str]:
        free_at = self.next_free(dt)
        if free_at is None:
            return "right now" if self.list_day(dt.date()) else None
        return f"after {format_time(free_at)}"

    async def build_from_text(self, text: str, day: date, create_reminders: bool = False, memory_context: Optional[str] = None) -> list[TimeBlock]:
        blocks = None
        if self.ai_router:
            blocks = await self._ai_blocks(text, day, memory_context=memory_context)
        if not blocks:
            blocks = self._naive_blocks(text)
        return self.set_day(day, blocks, create_reminders=create_reminders)

    async def _ai_blocks(self, text: str, day: date, memory_context: Optional[str] = None) -> Optional[list[dict]]:
        from core.router import TaskType
        extra = ""
        if memory_context:
            extra = f"\n\nHere is additional context about the person's life, projects, and notes — use it to suggest realistic and relevant activities for today:\n{memory_context}\n\n"
        prompt = (
            "Turn the following list of a person's tasks for the day into a realistic "
            "timetable. Respond with ONLY a JSON array, no prose. Each element must be "
            '{"start":"HH:MM","end":"HH:MM","title":"...","busy":true}. Use 24-hour times, '
            "order them through the day, leave short gaps between blocks, and set busy=false "
            "for breaks/free time." + extra + "Tasks for today:\n" + text
        )
        try:
            result = await self.ai_router.run(TaskType.GENERAL_ASSISTANT, prompt)
        except Exception as exc:
            logger.warning("AI timetable build failed (%s); using naive layout.", exc)
            return None
        return self._parse_blocks(result.text)

    @staticmethod
    def _parse_blocks(raw: str) -> Optional[list[dict]]:
        if not raw:
            return None
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return None
        blocks = []
        for item in data:
            if not isinstance(item, dict) or "start" not in item:
                continue
            blocks.append({
                "start": str(item["start"])[:5],
                "end": str(item.get("end") or item["start"])[:5],
                "title": str(item.get("title", "")).strip() or "Busy",
                "busy": bool(item.get("busy", True)),
            })
        return blocks or None

    @staticmethod
    def _naive_blocks(text: str) -> list[dict]:
        lines = [ln.strip("-*• \t") for ln in text.splitlines() if ln.strip()]
        blocks = []
        cursor = time(_DEFAULT_START_HOUR, 0)
        for line in lines:
            start = cursor
            end_dt = (datetime.combine(date.today(), start) + timedelta(hours=1)).time()
            blocks.append({
                "start": start.strftime("%H:%M"),
                "end": end_dt.strftime("%H:%M"),
                "title": line,
                "busy": True,
            })
            cursor = end_dt
        return blocks

    @staticmethod
    def _combine(day: date, hhmm: str) -> datetime:
        hhmm = hhmm.strip()
        parts = hhmm.split(":")
        hour = int(parts[0]) if parts and parts[0].isdigit() else 0
        minute = int(parts[1]) if len(parts) > 1 and parts[1][:2].isdigit() else 0
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))
        return datetime.combine(day, time(hour, minute))

    @staticmethod
    def _row_to_block(row: sqlite3.Row) -> TimeBlock:
        return TimeBlock(
            id=row["id"],
            day=date.fromisoformat(row["day"]),
            start=datetime.fromisoformat(row["start"]),
            end=datetime.fromisoformat(row["end"]),
            title=row["title"],
            busy=bool(row["busy"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
