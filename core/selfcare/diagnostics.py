"""
DiagnosticsManager - checks SQLite databases, dependencies, disk space, config,
and AI provider availability. Runs database and config auto-repairs.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any


class DiagnosticsManager:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def _check_single_db(self, db_path: Path) -> str:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            row = cursor.fetchone()
            conn.close()
            if row and row[0] == "ok":
                return "ok"
            return f"integrity check failed: {row}"
        except Exception as e:
            return f"error opening database: {e}"

    def _recreate_db(self, db_name: str, db_path: Path):
        if db_path.exists():
            db_path.unlink()
        if db_name == "memory.db":
            from core.memory import MemoryManager
            MemoryManager(db_path)
        elif db_name == "busy_mode.db":
            from core.busy_mode import BusyModeManager
            BusyModeManager(db_path)
        else:
            conn = sqlite3.connect(db_path)
            conn.close()

    def check_database_integrity(self) -> dict[str, str]:
        results = {}
        data_dir = self.base_dir / "data"
        data_dir.mkdir(exist_ok=True)
        for db_name in ["memory.db", "busy_mode.db"]:
            db_path = data_dir / db_name
            if not db_path.exists():
                results[db_name] = "missing"
            else:
                results[db_name] = self._check_single_db(db_path)
        return results

    def check_dependencies(self) -> list[dict[str, Any]]:
        import importlib.util
        import importlib.metadata
        results = []
        req_file = self.base_dir / "requirements.txt"
        if not req_file.exists():
            return results

        lines = req_file.read_text().splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pkg_name = re.split(r'[>=<\[]', line)[0].strip()
            if not pkg_name:
                continue
            # Handle standard imports vs package name mapping if necessary
            import_name = pkg_name.lower().replace("-", "_")
            if import_name == "pydantic_settings":
                import_name = "pydantic_settings"
            elif import_name == "apscheduler":
                import_name = "apscheduler"
            elif import_name == "pyside6":
                import_name = "PySide6"
            elif import_name == "pyttsx3":
                import_name = "pyttsx3"

            spec = importlib.util.find_spec(import_name)
            installed = spec is not None
            version = None
            if installed:
                try:
                    version = importlib.metadata.version(pkg_name)
                except Exception:
                    try:
                        version = importlib.metadata.version(import_name)
                    except Exception:
                        pass
            results.append({"name": pkg_name, "installed": installed, "version": version})
        return results

    async def check_providers(self) -> list[dict[str, Any]]:
        from config import get_settings
        from core.router import AIRouter
        router = AIRouter(get_settings())
        return await router.provider_status()

    def check_disk_space(self) -> dict[str, int]:
        total, used, free = shutil.disk_usage(str(self.base_dir))
        return {"total": total, "used": used, "free": free}

    def check_config(self) -> dict[str, str]:
        results = {}
        env_file = self.base_dir / ".env"
        if not env_file.exists():
            results[".env"] = "missing"
            return results
        results[".env"] = "present"
        content = env_file.read_text()
        required_keys = ["GROQ_API_KEY", "NVIDIA_NIM_API_KEY", "OLLAMA_BASE_URL", "VOICE_VOSK_MODEL_PATH"]
        for key in required_keys:
            # check if key is set (i.e. KEY=something)
            match = re.search(fr"^{key}\s*=\s*(.*)$", content, re.MULTILINE)
            if match:
                val = match.group(1).strip()
                if val:
                    results[key] = "configured"
                else:
                    results[key] = "empty"
            else:
                results[key] = "missing"
        return results

    def repair_databases(self) -> dict[str, str]:
        results = {}
        data_dir = self.base_dir / "data"
        data_dir.mkdir(exist_ok=True)
        for db_name in ["memory.db", "busy_mode.db"]:
            db_path = data_dir / db_name
            if not db_path.exists():
                try:
                    self._recreate_db(db_name, db_path)
                    results[db_name] = "recreated (was missing)"
                except Exception as e:
                    results[db_name] = f"failed to recreate: {e}"
                continue

            integrity = self._check_single_db(db_path)
            if integrity == "ok":
                results[db_name] = "ok"
                continue

            # attempt repair (VACUUM and REINDEX)
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("VACUUM")
                # SQLite doesn't have REINDEX as a global run-all-indexes command, but it runs on tables/indices
                # We can just vacuum to rebuild the file
                conn.close()
                if self._check_single_db(db_path) == "ok":
                    results[db_name] = "repaired with VACUUM"
                    continue
            except Exception:
                pass

            # recreate corrupted db
            try:
                backup_path = db_path.with_suffix(".db.corrupt")
                if backup_path.exists():
                    backup_path.unlink()
                db_path.rename(backup_path)
                self._recreate_db(db_name, db_path)
                results[db_name] = f"corrupted; backed up to {backup_path.name} and recreated"
            except Exception as e:
                results[db_name] = f"repair failed: {e}"
        return results

    def repair_config(self) -> dict[str, str]:
        results = {}
        env_file = self.base_dir / ".env"
        env_example = self.base_dir / ".env.example"
        if not env_file.exists():
            if env_example.exists():
                shutil.copy(str(env_example), str(env_file))
                results[".env"] = "restored from .env.example"
            else:
                env_file.write_text("# Emma Configuration\n")
                results[".env"] = "recreated empty file"
        else:
            results[".env"] = "present"

        content = env_file.read_text()
        required_keys = ["GROQ_API_KEY", "NVIDIA_NIM_API_KEY", "OLLAMA_BASE_URL", "VOICE_VOSK_MODEL_PATH"]
        updates = {}
        for key in required_keys:
            if not re.search(fr"^{key}\s*=", content, re.MULTILINE):
                # add missing key
                updates[key] = ""
                results[key] = "added missing empty key"
            else:
                results[key] = "present"
        if updates:
            from config import update_env_file
            update_env_file(updates)
        return results

    async def full_diagnostic(self) -> dict[str, Any]:
        db_integrity = self.check_database_integrity()
        deps = self.check_dependencies()
        disk = self.check_disk_space()
        config = self.check_config()
        providers = await self.check_providers()
        return {
            "databases": db_integrity,
            "dependencies": deps,
            "disk": disk,
            "config": config,
            "providers": providers,
        }

    async def auto_repair(self) -> dict[str, Any]:
        report = {}
        # 1. repair config first
        config_repairs = self.repair_config()
        report["config"] = config_repairs

        # 2. repair databases
        db_repairs = self.repair_databases()
        report["databases"] = db_repairs

        # 3. re-run diagnostic to verify status
        report["final_status"] = await self.full_diagnostic()
        return report
