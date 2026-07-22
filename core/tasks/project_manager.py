from __future__ import annotations

import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'active',
    deadline TEXT,
    priority TEXT NOT NULL DEFAULT 'medium',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    deadline TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    hours REAL NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class ProjectManager:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---------- Projects ----------

    def create_project(self, name: str, description: str = "", deadline: Optional[str] = None,
                       priority: str = "medium") -> dict:
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute(
            "INSERT INTO projects (name, description, deadline, priority, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name.strip(), description, deadline or None, priority, now, now),
        )
        self.conn.commit()
        return self.get_project(cur.lastrowid)

    def get_project(self, project_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if row:
            d = dict(row)
            d["milestones"] = self.list_milestones(project_id)
            try:
                d["task_count"] = self.conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE project=?", (d["name"],)
                ).fetchone()[0]
            except sqlite3.OperationalError:
                d["task_count"] = 0
            return d
        return None

    def update_project(self, project_id: int, **fields) -> Optional[dict]:
        allowed = {"name", "description", "progress", "status", "deadline", "priority"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_project(project_id)
        updates["updated_at"] = datetime.utcnow().isoformat()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [project_id]
        self.conn.execute(f"UPDATE projects SET {set_clause} WHERE id=?", values)
        self.conn.commit()
        return self.get_project(project_id)

    def list_projects(self, status: Optional[str] = None) -> list[dict]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        sql = "SELECT * FROM projects"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY priority DESC, updated_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def delete_project(self, project_id: int) -> bool:
        project = self.get_project(project_id)
        if not project:
            return False
        self.conn.execute("DELETE FROM milestones WHERE project_id=?", (project_id,))
        cur = self.conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- Milestones ----------

    def create_milestone(self, project_id: int, title: str,
                         deadline: Optional[str] = None) -> dict:
        cur = self.conn.execute(
            "INSERT INTO milestones (project_id, title, deadline, created_at) VALUES (?, ?, ?, ?)",
            (project_id, title.strip(), deadline or None, datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM milestones WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def list_milestones(self, project_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM milestones WHERE project_id=? ORDER BY deadline IS NULL, deadline, id",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_milestone_done(self, milestone_id: int, done: bool = True) -> Optional[dict]:
        self.conn.execute(
            "UPDATE milestones SET done=? WHERE id=?",
            (1 if done else 0, milestone_id),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM milestones WHERE id=?", (milestone_id,)).fetchone()
        if row:
            self._recalc_progress(row["project_id"])
        return dict(row) if row else None

    def delete_milestone(self, milestone_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM milestones WHERE id=?", (milestone_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def _recalc_progress(self, project_id: int) -> None:
        rows = self.conn.execute(
            "SELECT done FROM milestones WHERE project_id=?", (project_id,)
        ).fetchall()
        if not rows:
            return
        done = sum(1 for r in rows if r["done"])
        progress = round((done / len(rows)) * 100, 1)
        self.conn.execute(
            "UPDATE projects SET progress=? WHERE id=?", (progress, project_id)
        )
        self.conn.commit()

    # ---------- Study Logs ----------

    def log_study(self, subject: str, hours: float, notes: str = "",
                  day: Optional[date] = None) -> dict:
        day = day or date.today()
        cur = self.conn.execute(
            "INSERT INTO study_logs (subject, hours, notes, date, created_at) VALUES (?, ?, ?, ?, ?)",
            (subject.strip(), hours, notes, day.isoformat(), datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM study_logs WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def list_study_logs(self, subject: Optional[str] = None,
                        days: Optional[int] = None) -> list[dict]:
        where, params = [], []
        if subject:
            where.append("subject=?")
            params.append(subject)
        if days:
            cutoff = date.today().isoformat()
            where.append("date >= date('now', ?)")
            params.append(f"-{days} days")
        sql = "SELECT * FROM study_logs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY date DESC, id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def study_summary(self, days: int = 7) -> list[dict]:
        cutoff = date.today().isoformat()
        rows = self.conn.execute(
            "SELECT subject, SUM(hours) as total_hours, COUNT(*) as sessions "
            "FROM study_logs WHERE date >= date('now', ?) "
            "GROUP BY subject ORDER BY total_hours DESC",
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_study_log(self, log_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM study_logs WHERE id=?", (log_id,))
        self.conn.commit()
        return cur.rowcount > 0
