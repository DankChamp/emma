import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(category, key)
);
"""


class ProfileManager:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def get(self, category: str, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM profile WHERE category=? AND key=?", (category, key)
        ).fetchone()
        return row["value"] if row else None

    def set(self, category: str, key: str, value: str) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            "INSERT INTO profile (category, key, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(category, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (category, key, value, now),
        )
        self.conn.commit()

    def get_category(self, category: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT key, value FROM profile WHERE category=?", (category,)
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def get_all(self) -> dict[str, dict[str, str]]:
        rows = self.conn.execute(
            "SELECT category, key, value FROM profile ORDER BY category, key"
        ).fetchall()
        result: dict[str, dict[str, str]] = {}
        for r in rows:
            result.setdefault(r["category"], {})[r["key"]] = r["value"]
        return result

    def list_categories(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT category FROM profile ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]

    def delete(self, category: str, key: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM profile WHERE category=? AND key=?", (category, key)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_category(self, category: str) -> int:
        cur = self.conn.execute("DELETE FROM profile WHERE category=?", (category,))
        self.conn.commit()
        return cur.rowcount
