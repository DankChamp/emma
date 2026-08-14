"""
VoiceBackendClient - the voice loop's only path to Emma. Same rule as
emma_cli.py and gui/api_client.py: just an HTTP client, no business logic.
"""
from __future__ import annotations

import json
from typing import Any, Iterator, Optional

import httpx


class VoiceBackendClient:
    def __init__(self, base_url: str, local_only: bool = True):
        self.base_url = base_url.rstrip("/")
        self.local_only = local_only

    def chat(self, message: str, session_id: str = "voice", system: Optional[str] = None) -> str:
        """
        Send a transcribed command to Emma and return the reply text to
        speak. Uses task_type "conversation" and Manual/Auto routing exactly
        like every other client - the voice loop doesn't get special
        treatment, it's just another way of talking to Emma.
        """
        body = {
            "message": message,
            "session_id": session_id,
            "task_type": "conversation",
            "local_only": self.local_only,
        }
        if system:
            body["system"] = system
        resp = httpx.post(
            f"{self.base_url}/chat",
            json=body,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["reply"]

    def chat_stream(
        self,
        message: str,
        session_id: str = "voice",
        system: Optional[str] = None,
    ) -> Iterator[str]:
        """
        Send a command to Emma and stream her reply back chunk by chunk
        (SSE), so the voice loop can start synthesizing speech while the
        reply is still being generated. Raises RuntimeError with Emma's
        reason if the stream reports an error event or dies early.
        """
        body = {
            "message": message,
            "session_id": session_id,
            "task_type": "conversation",
            "stream": True,
            "local_only": self.local_only,
        }
        if system:
            body["system"] = system

        with httpx.stream(
            "POST",
            f"{self.base_url}/chat",
            json=body,
            timeout=120.0,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = json.loads(line[len("data:"):].strip())
                event = data.get("event")
                if event == "text":
                    yield data.get("text", "")
                elif event == "error":
                    raise RuntimeError(data.get("detail", "unknown streaming error"))
                elif event == "done":
                    return

    def judge(self, message: str) -> dict[str, Any]:
        """
        Wake-word intent gate: asks Emma whether the wake-word utterance is
        actually addressed to her, and if so what the intent is. The reply
        is a JSON object (`should_respond` bool + `intent` string) consumed
        by the voice loop - it is never spoken.
        """
        resp = httpx.post(
            f"{self.base_url}/chat/judge",
            json={"message": message, "local_only": self.local_only},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

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
