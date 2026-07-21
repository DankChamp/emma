"""
EmmaClient - every widget's only path to the backend.

Requests run on a QThreadPool worker so a slow model response or an
unreachable provider never freezes the window. Call sites use:

    self.client.call(
        lambda: self.client.chat(message, task_type="coding"),
        on_success=self._handle_reply,
        on_error=self._handle_error,
    )

`call()` runs the given zero-arg function on a worker thread and marshals
its result (or exception) back onto the Qt main thread via signals.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import requests
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class _WorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)


class _CancellableWorker(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _WorkerSignals()
        self._cancelled = False

    def run(self):
        try:
            if self._cancelled:
                return
            result = self.fn()
        except requests.exceptions.ConnectionError:
            if not self._cancelled:
                self.signals.error.emit(
                    "Can't reach Emma's backend. Is `uvicorn main:app` running?"
                )
        except requests.exceptions.HTTPError as exc:
            if not self._cancelled:
                detail = ""
                try:
                    detail = exc.response.json().get("detail", "")
                except Exception:
                    detail = exc.response.text if exc.response is not None else str(exc)
                self.signals.error.emit(str(detail) or str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            if not self._cancelled:
                self.signals.error.emit(str(exc))
        else:
            if not self._cancelled:
                self.signals.success.emit(result)

    def cancel(self):
        self._cancelled = True


class EmmaClient(QObject):
    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.pool = QThreadPool.globalInstance()

    # ---------- plumbing ----------
    def call(self, fn: Callable[[], Any], on_success=None, on_error=None) -> _CancellableWorker:
        worker = _CancellableWorker(fn)
        if on_success:
            worker.signals.success.connect(on_success)
        if on_error:
            worker.signals.error.connect(on_error)
        self.pool.start(worker)
        return worker

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = requests.get(self._url(path), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json: Optional[dict] = None) -> Any:
        resp = requests.post(self._url(path), json=json or {}, timeout=90)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, json: Optional[dict] = None) -> Any:
        resp = requests.patch(self._url(path), json=json or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> Any:
        resp = requests.delete(self._url(path), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def ping(self) -> Any:
        return self._get("/health")

    # ---------- chat ----------
    def chat(
        self,
        message: str,
        session_id: str = "default",
        task_type: str = "conversation",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        system: Optional[str] = None,
    ) -> Any:
        return self._post(
            "/chat",
            {
                "message": message,
                "session_id": session_id,
                "task_type": task_type,
                "provider": provider,
                "model": model,
                "system": system,
            },
        )

    def chat_history(self, session_id: str = "default") -> Any:
        return self._get(f"/chat/history/{session_id}")

    # ---------- providers / settings ----------
    def list_providers(self) -> Any:
        return self._get("/settings/providers")

    def provider_models(self, provider: str) -> Any:
        return self._get(f"/settings/providers/{provider}/models")

    def set_provider_key(self, provider: str, api_key: str) -> Any:
        return self._post(f"/settings/providers/{provider}/key", {"api_key": api_key})

    def set_provider_model(self, provider: str, model: str) -> Any:
        return self._post(f"/settings/providers/{provider}/model", {"model": model})

    def set_ollama_base_url(self, base_url: str) -> Any:
        return self._post("/settings/ollama/base-url", {"base_url": base_url})

    def set_provider_base_url(self, provider: str, base_url: str) -> Any:
        return self._post(f"/settings/providers/{provider}/base-url", {"base_url": base_url})

    def test_provider(self, provider: str) -> Any:
        return self._post(f"/settings/providers/{provider}/test")

    # ---------- memory ----------
    def save_memory(
        self,
        targets: list[str],
        key: str,
        value: str,
        category: Optional[str] = None,
        project: Optional[str] = None,
    ) -> Any:
        return self._post(
            "/memory/save",
            {
                "targets": targets,
                "key": key,
                "value": value,
                "category": category,
                "project": project,
            },
        )

    def list_long_term_categories(self) -> Any:
        return self._get("/memory/long-term")

    def get_long_term_category(self, category: str) -> Any:
        return self._get(f"/memory/long-term/{category}")

    def list_projects(self) -> Any:
        return self._get("/memory/project")

    def get_project_memory(self, project: str) -> Any:
        return self._get(f"/memory/project/{project}")

    def get_daily_memory(self) -> Any:
        return self._get("/memory/daily")

    def set_daily_memory(self, key: str, value: str) -> Any:
        return self._post("/memory/daily", {"key": key, "value": value})

    # ---------- schedule ----------
    def list_schedule(self, day: str = "today") -> Any:
        return self._get(f"/schedule/{day}")

    def save_schedule(self, day: str, blocks: list[dict]) -> Any:
        return self._post("/schedule", {"day": day, "blocks": blocks})

    def build_schedule(self, day: str, text: str) -> Any:
        return self._post("/schedule/build", {"day": day, "text": text})

    def get_free_hint(self) -> Any:
        return self._get("/schedule/free-hint")

    def delete_day(self, day: str) -> Any:
        return self._post("/schedule/delete", {"day": day})

    # ---------- busy mode / status ----------
    def get_status(self) -> Any:
        return self._get("/status")

    def go_busy(self, note: Optional[str] = None) -> Any:
        return self._post("/status/busy", {"note": note})

    def go_free(self) -> Any:
        return self._post("/status/free")

    def list_contacts(self) -> Any:
        return self._get("/status/contacts")

    def add_contact(self, name: str, busy_message: str, free_message: Optional[str] = None) -> Any:
        return self._post(
            "/status/contacts",
            {"name": name, "busy_message": busy_message, "free_message": free_message},
        )

    def remove_contact(self, name: str) -> Any:
        return self._delete(f"/status/contacts/{name}")

    # ---------- self-care ----------
    def run_diagnostics(self) -> Any:
        return self._get("/selfcare/diagnostics")

    def auto_repair(self) -> Any:
        return self._post("/selfcare/repair")

    def check_updates(self) -> Any:
        return self._get("/selfcare/updates")

    def apply_updates(self) -> Any:
        return self._post("/selfcare/updates/apply")

    def update_dependencies(self) -> Any:
        return self._post("/selfcare/updates/deps")

    def get_changelog(self) -> Any:
        return self._get("/selfcare/changelog")

    def get_version_info(self) -> Any:
        return self._get("/selfcare/version")

    # ---------- persona ----------
    def get_persona(self) -> Any:
        return self._get("/memory/persona")

    def set_persona(self, text: str) -> Any:
        return self._post("/memory/persona", {"text": text})

    # ---------- notifications / telegram ----------
    def bot_status(self) -> Any:
        return self._get("/notifications/bot-status")

    def start_bot(self) -> Any:
        return self._post("/notifications/bot/start")

    def stop_bot(self) -> Any:
        return self._post("/notifications/bot/stop")

    def list_telegram_users(self) -> Any:
        return self._get("/notifications/users")

    def label_telegram_user(self, telegram_id: int, label: str, role: str) -> Any:
        return self._post("/notifications/users/label", {"telegram_id": telegram_id, "label": label, "role": role})

    def set_telegram_chat_id(self, telegram_id: int, chat_id: int) -> Any:
        return self._post("/notifications/users/chat-id", {"telegram_id": telegram_id, "chat_id": chat_id})

    def set_telegram_priority(self, telegram_id: int, priority: str) -> Any:
        return self._post("/notifications/users/priority", {"telegram_id": telegram_id, "priority": priority})

    def set_telegram_notify_on_busy(self, telegram_id: int, notify_on_busy: bool) -> Any:
        return self._post("/notifications/users/notify", {"telegram_id": telegram_id, "notify_on_busy": notify_on_busy})

    def set_telegram_messages(self, telegram_id: int, busy_message: str | None = None, free_message: str | None = None) -> Any:
        return self._post(
            "/notifications/users/messages",
            {"telegram_id": telegram_id, "busy_message": busy_message, "free_message": free_message},
        )

    def set_telegram_owner(self, telegram_id: int) -> Any:
        return self._post("/notifications/users/owner", {"telegram_id": telegram_id})

    def set_telegram_prompt(self, telegram_id: int, prompt: str | None = None) -> Any:
        return self._post("/notifications/users/prompt", {"telegram_id": telegram_id, "prompt": prompt})

    def telegram_messages(self) -> Any:
        return self._get("/notifications/messages")

    # ---------- freeform text memory ----------
    def get_long_term_text(self) -> Any:
        return self._get("/memory/long-term-text")

    def set_long_term_text(self, text: str) -> Any:
        return self._post("/memory/long-term-text", {"text": text})

    def get_project_text(self, project: str) -> Any:
        return self._get(f"/memory/project-text/{project}")

    def set_project_text(self, project: str, text: str) -> Any:
        return self._post("/memory/project-text", {"project": project, "text": text})

    def get_daily_text(self) -> Any:
        return self._get("/memory/daily-text")

    def set_daily_text(self, text: str) -> Any:
        return self._post("/memory/daily-text", {"text": text})
