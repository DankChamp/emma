"""
TaskManager - CRUD over tasks.db, matching the schema that already ships in
data/tasks.db. Same pattern as the other managers: plain SQL, no ORM, one
connection, everything else goes through this class.

Sorting rule everywhere: pending before done, then high > medium > low,
then nearest deadline, then newest first - i.e. the order you'd actually
want to read your list in.
"""
# Deferred annotations: the `list` method below shadows the builtin inside
# the class body, which breaks `-> list[dict]` annotations on Python < 3.14.
from __future__ import annotations

import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from .models import PRIORITIES

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    project TEXT,
    priority TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'pending',
    deadline TEXT,
    estimated_hours REAL,
    completed_hours REAL DEFAULT 0,
    milestone_id INTEGER,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);
"""

MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN estimated_hours REAL",
    "ALTER TABLE tasks ADD COLUMN completed_hours REAL DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN milestone_id INTEGER",
]

_ORDER = """
ORDER BY
    CASE status WHEN 'pending' THEN 0 ELSE 1 END,
    CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
    deadline IS NULL, deadline ASC,
    id DESC
"""


class TaskManager:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for migration in MIGRATIONS:
            try:
                self.conn.execute(migration)
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def create(self, title: str, project: Optional[str] = None,
               priority: str = "medium", deadline: Optional[str] = None,
               estimated_hours: Optional[float] = None,
               milestone_id: Optional[int] = None) -> dict:
        if priority not in PRIORITIES:
            priority = "medium"
        cur = self.conn.execute(
            "INSERT INTO tasks (title, project, priority, status, deadline, "
            "estimated_hours, milestone_id, created_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
            (title.strip(), project or None, priority, deadline or None,
             estimated_hours, milestone_id, datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, task_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def update(self, task_id: int, *, title: Optional[str] = None,
               project: Optional[str] = None, priority: Optional[str] = None,
               deadline: Optional[str] = None,
               estimated_hours: Optional[float] = None,
               milestone_id: Optional[int] = None) -> Optional[dict]:
        task = self.get(task_id)
        if not task:
            return None
        if title is not None:
            task["title"] = title.strip() or task["title"]
        if project is not None:
            task["project"] = project or None
        if priority in PRIORITIES:
            task["priority"] = priority
        if deadline is not None:
            task["deadline"] = deadline or None
        if estimated_hours is not None:
            task["estimated_hours"] = estimated_hours
        if milestone_id is not None:
            task["milestone_id"] = milestone_id or None
        self.conn.execute(
            "UPDATE tasks SET title=?, project=?, priority=?, deadline=?, "
            "estimated_hours=?, milestone_id=? WHERE id=?",
            (task["title"], task["project"], task["priority"], task["deadline"],
             task.get("estimated_hours"), task.get("milestone_id"), task_id),
        )
        self.conn.commit()
        return self.get(task_id)

    def set_done(self, task_id: int, done: bool = True) -> Optional[dict]:
        self.conn.execute(
            "UPDATE tasks SET status=?, completed_at=? WHERE id=?",
            ("done" if done else "pending",
             datetime.utcnow().isoformat() if done else None, task_id),
        )
        self.conn.commit()
        return self.get(task_id)

    def delete(self, task_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list(self, status: Optional[str] = None, project: Optional[str] = None) -> list[dict]:
        where, params = [], []
        if status in ("pending", "done"):
            where.append("status=?")
            params.append(status)
        if project:
            where.append("project=?")
            params.append(project)
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self.conn.execute(sql + _ORDER, params).fetchall()
        return [dict(r) for r in rows]

    def clear_done(self) -> int:
        cur = self.conn.execute("DELETE FROM tasks WHERE status='done'")
        self.conn.commit()
        return cur.rowcount

    # ---------- Summaries (fed to chat context and the daily brief) ----------
    def pending_summary(self, limit: int = 15) -> str:
        """Compact human-readable list of open tasks, '' when there are none."""
        rows = self.conn.execute(
            f"SELECT * FROM tasks WHERE status='pending' {_ORDER} LIMIT ?", (limit,)
        ).fetchall()
        if not rows:
            return ""
        today = date.today().isoformat()
        lines = []
        for r in rows:
            bits = [r["title"]]
            if r["project"]:
                bits.append(f"[{r['project']}]")
            if r["priority"] == "high":
                bits.append("(high priority)")
            if r["deadline"]:
                overdue = r["deadline"][:10] < today
                bits.append(f"(OVERDUE, was due {r['deadline'][:10]})" if overdue
                            else f"(due {r['deadline'][:10]})")
            lines.append("- " + " ".join(bits))
        return "\n".join(lines)

    def counts(self) -> dict:
        today = date.today().isoformat()
        c = self.conn
        return {
            "pending": c.execute("SELECT COUNT(*) FROM tasks WHERE status='pending'").fetchone()[0],
            "done": c.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0],
            "overdue": c.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='pending' AND deadline IS NOT NULL "
                "AND substr(deadline,1,10) < ?", (today,)).fetchone()[0],
            "due_today": c.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='pending' AND substr(deadline,1,10)=?",
                (today,)).fetchone()[0],
        }

    def completed_today(self) -> list[dict]:
        today = date.today().isoformat()
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE status='done' AND substr(completed_at,1,10)=? ORDER BY completed_at",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]
