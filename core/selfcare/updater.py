"""
UpdateManager - handles checking and applying git updates, listing commits,
and updating python package dependencies in the virtual environment.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class UpdateManager:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def check_for_updates(self) -> dict[str, Any]:
        if not (self.base_dir / ".git").exists():
            return {
                "has_updates": False,
                "current_commit": "not a git repo",
                "behind_by": 0,
                "latest_message": "",
                "error": "No git repository found in workspace.",
            }

        try:
            # git fetch
            subprocess.run(["git", "fetch"], cwd=str(self.base_dir), check=True, capture_output=True, timeout=15)

            # git rev-list --count HEAD..@{u}
            rev_run = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=str(self.base_dir), capture_output=True, text=True, timeout=5)
            behind_by = 0
            if rev_run.returncode == 0:
                behind_by = int(rev_run.stdout.strip() or 0)

            has_updates = behind_by > 0

            # current commit
            commit_run = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(self.base_dir), capture_output=True, text=True, timeout=5)
            current_commit = commit_run.stdout.strip() if commit_run.returncode == 0 else "unknown"

            # latest commit message
            msg_run = subprocess.run(["git", "log", "-1", "--format=%s", "@{u}"], cwd=str(self.base_dir), capture_output=True, text=True, timeout=5)
            latest_message = msg_run.stdout.strip() if msg_run.returncode == 0 else ""

            return {
                "has_updates": has_updates,
                "current_commit": current_commit,
                "behind_by": behind_by,
                "latest_message": latest_message,
            }
        except Exception as e:
            return {
                "has_updates": False,
                "current_commit": "error",
                "behind_by": 0,
                "latest_message": "",
                "error": str(e),
            }

    def apply_updates(self, restart: bool = False) -> dict[str, Any]:
        if not (self.base_dir / ".git").exists():
            return {"success": False, "message": "No git repository found."}

        try:
            pull_run = subprocess.run(["git", "pull"], cwd=str(self.base_dir), capture_output=True, text=True, timeout=30)
            if pull_run.returncode != 0:
                return {
                    "success": False,
                    "message": f"Git pull failed: {pull_run.stderr.strip()}",
                }

            commit_run = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(self.base_dir), capture_output=True, text=True, timeout=5)
            new_commit = commit_run.stdout.strip() if commit_run.returncode == 0 else "unknown"

            return {
                "success": True,
                "message": "Successfully pulled changes from git.",
                "changes_summary": pull_run.stdout.strip(),
                "new_commit": new_commit,
            }
        except Exception as e:
            return {"success": False, "message": f"Error running git pull: {e}"}

    def update_dependencies(self) -> dict[str, Any]:
        venv_pip = self.base_dir / ".venv" / "bin" / "pip"
        if not venv_pip.exists():
            venv_pip = Path("pip")

        req_file = self.base_dir / "requirements.txt"
        if not req_file.exists():
            return {"success": False, "message": "requirements.txt not found."}

        try:
            pip_run = subprocess.run(
                [str(venv_pip), "install", "-r", str(req_file)],
                cwd=str(self.base_dir),
                capture_output=True,
                text=True,
                timeout=60
            )
            if pip_run.returncode != 0:
                return {
                    "success": False,
                    "message": f"Pip install failed: {pip_run.stderr.strip()}",
                    "output": pip_run.stdout.strip(),
                }
            return {
                "success": True,
                "message": "Dependencies updated successfully.",
                "output": pip_run.stdout.strip(),
            }
        except Exception as e:
            return {"success": False, "message": f"Error running pip: {e}"}

    def get_changelog(self, n: int = 10) -> list[dict[str, str]]:
        if not (self.base_dir / ".git").exists():
            return []
        try:
            log_run = subprocess.run(
                ["git", "log", f"-n", str(n), "--format=%h|%an|%ad|%s", "--date=short"],
                cwd=str(self.base_dir),
                capture_output=True,
                text=True,
                timeout=5
            )
            if log_run.returncode != 0:
                return []

            commits = []
            for line in log_run.stdout.splitlines():
                parts = line.split("|", 3)
                if len(parts) == 4:
                    commits.append({
                        "hash": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "message": parts[3],
                    })
            return commits
        except Exception:
            return []

    def get_version_info(self) -> dict[str, str]:
        if not (self.base_dir / ".git").exists():
            return {
                "commit": "not a git repo",
                "branch": "none",
                "date": "unknown",
            }
        try:
            commit_run = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(self.base_dir), capture_output=True, text=True, timeout=5)
            commit = commit_run.stdout.strip() if commit_run.returncode == 0 else "unknown"

            branch_run = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(self.base_dir), capture_output=True, text=True, timeout=5)
            branch = branch_run.stdout.strip() if branch_run.returncode == 0 else "unknown"

            date_run = subprocess.run(["git", "log", "-1", "--format=%cd", "--date=short"], cwd=str(self.base_dir), capture_output=True, text=True, timeout=5)
            date_str = date_run.stdout.strip() if date_run.returncode == 0 else "unknown"

            return {
                "commit": commit,
                "branch": branch,
                "date": date_str,
            }
        except Exception as e:
            return {
                "commit": "error",
                "branch": "error",
                "date": str(e),
            }
