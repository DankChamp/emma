"""
VoiceBackendClient - the voice loop's only path to Emma. Same rule as
emma_cli.py and gui/api_client.py: just an HTTP client, no business logic.
"""
from __future__ import annotations

from typing import Optional, Any

import httpx


class VoiceBackendClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def chat(self, message: str, session_id: str = "voice", system: Optional[str] = None) -> str:
        """
        Send a transcribed command to Emma and return the reply text to
        speak. Uses task_type "conversation" and Manual/Auto routing exactly
        like every other client - the voice loop doesn't get special
        treatment, it's just another way of talking to Emma.
        """
        body = {"message": message, "session_id": session_id, "task_type": "conversation"}
        if system:
            body["system"] = system
        resp = httpx.post(
            f"{self.base_url}/chat",
            json=body,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["reply"]

    def get_persona(self) -> str:
        try:
            resp = httpx.get(f"{self.base_url}/memory/persona", timeout=5.0)
            resp.raise_for_status()
            return resp.json().get("text", "")
        except Exception:
            return ""

    def is_reachable(self) -> bool:
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=3.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def create_task(self, title: str, project: Optional[str] = None, priority: str = "medium") -> dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/tasks",
            json={"title": title, "project": project, "priority": priority},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def list_tasks(self) -> list[dict[str, Any]]:
        resp = httpx.get(f"{self.base_url}/tasks", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def create_reminder(self, message: str, minutes: int) -> dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/reminders/after",
            json={"message": message, "minutes": minutes},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def go_busy(self, note: Optional[str] = None) -> dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/status/busy",
            json={"note": note},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def go_free(self) -> dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/status/free",
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def get_status(self) -> dict[str, Any]:
        resp = httpx.get(f"{self.base_url}/status", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def save_memory(self, category: str, key: str, value: str) -> dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/memory/save",
            json={
                "targets": ["long_term"],
                "key": key,
                "value": value,
                "category": category,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()
