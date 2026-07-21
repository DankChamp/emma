"""
MemoryManager - the single entry point for reading/writing any of Emma's
four memory tiers. Nothing else in Emma should touch sqlite3 directly;
they go through this class.
"""
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from .db import get_connection


# Reserved long-term slot holding Emma's free-form identity/persona - who she
# is, who the user is, what her purpose is. Kept in long_term_memory (no new
# table) under a category the normal browse UI never suggests, so it can't
# collide with a user's own categories.
PERSONA_CATEGORY = "system"
PERSONA_KEY = "persona"

# Same reserved category: which project's memory Emma should carry into chat.
ACTIVE_PROJECT_KEY = "active_project"


class MemoryManager:
    def __init__(self, db_path: Path):
        self.conn = get_connection(db_path)

    # ---------- Persona / identity (who Emma is, who you are, her purpose) ----------
    def get_persona(self) -> str:
        """Return Emma's persona text, or '' if none has been written yet."""
        return self.recall(PERSONA_CATEGORY, PERSONA_KEY) or ""

    def set_persona(self, text: str) -> None:
        """Store (or overwrite) Emma's free-form identity text."""
        self.remember(PERSONA_CATEGORY, PERSONA_KEY, text)

    # ---------- Long-term (freeform text) ----------
    def get_long_term_text(self) -> str:
        rows = self.conn.execute(
            "SELECT value FROM long_term_memory WHERE category='_text' AND key='content'"
        ).fetchone()
        return rows["value"] if rows else ""

    def set_long_term_text(self, text: str) -> None:
        self.remember("_text", "content", text)

    # ---------- Long-term (key-value) ----------
    def remember(self, category: str, key: str, value: str) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            INSERT INTO long_term_memory (category, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(category, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (category, key, value, now, now),
        )
        self.conn.commit()

    def recall(self, category: str, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM long_term_memory WHERE category=? AND key=?", (category, key)
        ).fetchone()
        return row["value"] if row else None

    def recall_category(self, category: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT key, value FROM long_term_memory WHERE category=?", (category,)
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def list_long_term_categories(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT category FROM long_term_memory WHERE category != ? ORDER BY category",
            (PERSONA_CATEGORY,),
        ).fetchall()
        return [r["category"] for r in rows]

    # ---------- Project (freeform text) ----------
    def get_project_text(self, project: str) -> str:
        rows = self.conn.execute(
            "SELECT value FROM project_memory WHERE project=? AND key='_text'", (project,)
        ).fetchone()
        return rows["value"] if rows else ""

    def set_project_text(self, project: str, text: str) -> None:
        self.set_project_memory(project, "_text", text)

    # ---------- Project (key-value, kept for internal use) ----------
    def set_project_memory(self, project: str, key: str, value: str) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            INSERT INTO project_memory (project, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (project, key, value, now, now),
        )
        self.conn.commit()

    def get_project_memory(self, project: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT key, value FROM project_memory WHERE project=?", (project,)
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def list_projects(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT project FROM project_memory ORDER BY project"
        ).fetchall()
        return [r["project"] for r in rows]

    def list_projects_meta(self) -> list[dict]:
        """Project list with entry count and last-touched time, for the UI browser."""
        rows = self.conn.execute(
            """
            SELECT project, COUNT(*) AS entries, MAX(updated_at) AS updated_at
            FROM project_memory GROUP BY project ORDER BY project COLLATE NOCASE
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_project(self, project: str) -> int:
        cur = self.conn.execute("DELETE FROM project_memory WHERE project=?", (project,))
        self.conn.commit()
        if self.get_active_project() == project:
            self.set_active_project(None)
        return cur.rowcount

    def rename_project(self, old: str, new: str) -> None:
        if self.conn.execute(
            "SELECT 1 FROM project_memory WHERE project=? LIMIT 1", (new,)
        ).fetchone():
            raise ValueError(f"A project named '{new}' already exists")
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            "UPDATE project_memory SET project=?, updated_at=? WHERE project=?", (new, now, old)
        )
        self.conn.commit()
        if self.get_active_project() == old:
            self.set_active_project(new)

    # ---------- Active project (whose memory chat should load) ----------
    def get_active_project(self) -> Optional[str]:
        return self.recall(PERSONA_CATEGORY, ACTIVE_PROJECT_KEY) or None

    def set_active_project(self, project: Optional[str]) -> None:
        self.remember(PERSONA_CATEGORY, ACTIVE_PROJECT_KEY, project or "")

    # ---------- Daily (freeform text, auto-scoped to today, rolls over) ----------
    def get_daily_text(self, day: Optional[date] = None) -> str:
        day = day or date.today()
        rows = self.conn.execute(
            "SELECT value FROM daily_memory WHERE day=? AND key='_text'", (day.isoformat(),)
        ).fetchone()
        return rows["value"] if rows else ""

    def set_daily_text(self, text: str, day: Optional[date] = None) -> None:
        self.set_daily("_text", text, day=day)

    # ---------- Daily (key-value) ----------
    def set_daily(self, key: str, value: str, day: Optional[date] = None) -> None:
        day = day or date.today()
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            INSERT INTO daily_memory (day, key, value, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(day, key) DO UPDATE SET value=excluded.value
            """,
            (day.isoformat(), key, value, now),
        )
        self.conn.commit()

    def get_daily(self, day: Optional[date] = None) -> dict[str, str]:
        day = day or date.today()
        rows = self.conn.execute(
            "SELECT key, value FROM daily_memory WHERE day=?", (day.isoformat(),)
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def purge_daily_before(self, day: date) -> None:
        """Clear out daily memory older than a given date - keeps the table small."""
        self.conn.execute("DELETE FROM daily_memory WHERE day < ?", (day.isoformat(),))
        self.conn.commit()

    # ---------- Conversation (short rolling window) ----------
    def add_turn(self, session_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO conversation_memory (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def get_recent_turns(self, session_id: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT role, content, created_at FROM conversation_memory
            WHERE session_id=? ORDER BY id DESC LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def list_sessions(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT session_id, COUNT(*) AS turns, MAX(created_at) AS last_at
            FROM conversation_memory GROUP BY session_id ORDER BY last_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def clear_session(self, session_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM conversation_memory WHERE session_id=?", (session_id,)
        )
        self.conn.commit()
        return cur.rowcount

    # ---------- Overview (counts per tier, for the Memory tab header) ----------
    def overview(self) -> dict:
        c = self.conn
        return {
            "long_term_entries": c.execute(
                "SELECT COUNT(*) FROM long_term_memory WHERE category != ?",
                (PERSONA_CATEGORY,),
            ).fetchone()[0],
            "projects": c.execute(
                "SELECT COUNT(DISTINCT project) FROM project_memory"
            ).fetchone()[0],
            "daily_days": c.execute(
                "SELECT COUNT(DISTINCT day) FROM daily_memory"
            ).fetchone()[0],
            "sessions": c.execute(
                "SELECT COUNT(DISTINCT session_id) FROM conversation_memory"
            ).fetchone()[0],
            "turns": c.execute("SELECT COUNT(*) FROM conversation_memory").fetchone()[0],
        }
