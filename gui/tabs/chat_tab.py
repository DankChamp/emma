"""
Chat tab - Emma's "normal talk mode".

Auto mode (default): you just type, Emma's router picks the right model
per message based on the task-type dropdown (or leaves it on Conversation
for plain chit-chat). Manual mode: you pin a specific provider + model and
every message goes straight to it, bypassing routing entirely - useful
when you know exactly which model you want, or you're testing one.

Also includes:
  - Interrupt button to stop Emma mid-reply (cancels the in-flight request).
  - Identity / Persona editor so you can inject her system prompt and she
    never forgets who she is.
"""
import html
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from gui.api_client import EmmaClient
from gui.dialogs.save_memory_dialog import SaveMemoryDialog
from gui import theme

TASK_TYPES = ["conversation", "coding", "reasoning", "creative", "general"]
PROVIDERS = ["ollama", "groq", "nvidia_nim"]


class ChatInput(QPlainTextEdit):
    """Enter sends, Shift+Enter inserts a newline."""

    def __init__(self, on_send):
        super().__init__()
        self.on_send = on_send
        self.setFixedHeight(72)
        self.setPlaceholderText("Talk to Emma... (Enter to send, Shift+Enter for a new line)")

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self.on_send()
            return
        super().keyPressEvent(event)


class ChatTab(QWidget):
    def __init__(self, client: EmmaClient):
        super().__init__()
        self.client = client
        self.last_exchange = ("", "")  # (user_message, assistant_reply) for the save dialog
        self._pending_worker = None   # _CancellableWorker for interrupt

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # ---- top bar: session + routing controls ----
        bar = QHBoxLayout()

        bar.addWidget(QLabel("Session:"))
        self.session_edit = QLineEdit("default")
        self.session_edit.setFixedWidth(120)
        bar.addWidget(self.session_edit)

        bar.addWidget(QLabel("Task:"))
        self.task_combo = QComboBox()
        self.task_combo.addItems(TASK_TYPES)
        bar.addWidget(self.task_combo)

        self.manual_check = QCheckBox("Manual mode")
        self.manual_check.setToolTip(
            "When checked, Emma skips the router and always uses the provider\n"
            "and model picked here, no matter which task type is selected."
        )
        bar.addWidget(self.manual_check)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(PROVIDERS)
        self.provider_combo.setEnabled(False)
        bar.addWidget(self.provider_combo)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setEnabled(False)
        self.model_combo.setMinimumWidth(220)
        bar.addWidget(self.model_combo)

        bar.addStretch()
        root.addLayout(bar)

        self.manual_check.toggled.connect(self._toggle_manual)
        self.provider_combo.currentTextChanged.connect(self._refresh_models)

        # ---- chat log ----
        self.log = QTextBrowser()
        self.log.setObjectName("ChatLog")
        self.log.setOpenExternalLinks(False)
        root.addWidget(self.log, stretch=1)

        self.status_label = QLabel(" ")
        self.status_label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        root.addWidget(self.status_label)

        # ---- input row ----
        input_row = QHBoxLayout()
        self.input_box = ChatInput(self._send)
        input_row.addWidget(self.input_box, stretch=1)

        btn_col = QVBoxLayout()
        self.send_btn = QPushButton("Send")
        self.send_btn.setProperty("class", "Primary")
        self.send_btn.setStyleSheet(
            f"background-color: {theme.ACCENT}; color: white; border: 1px solid {theme.ACCENT};"
        )
        self.send_btn.clicked.connect(self._send)
        btn_col.addWidget(self.send_btn)

        self.interrupt_btn = QPushButton("Interrupt")
        self.interrupt_btn.setEnabled(False)
        self.interrupt_btn.clicked.connect(self._interrupt)
        btn_col.addWidget(self.interrupt_btn)

        self.save_btn = QPushButton("Save to Memory")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._open_save_dialog)
        btn_col.addWidget(self.save_btn)

        input_row.addLayout(btn_col)
        root.addLayout(input_row)

        # ---- collapsible identity / persona editor ----
        self._build_persona_section(root)

        self._append_system("Emma is ready. Ask her anything, or switch to Manual mode to pin a model.")
        self._load_persona()

    # ---------- identity / persona ----------
    def _build_persona_section(self, parent: QVBoxLayout):
        self.persona_toggle = QPushButton("▸ Identity & System Prompt")
        self.persona_toggle.setStyleSheet(
            f"text-align: left; padding: 6px 12px; background: transparent; border: none; "
            f"color: {theme.TEXT_DIM}; font-size: 12px;"
        )
        self.persona_toggle.clicked.connect(self._toggle_persona_editor)
        parent.addWidget(self.persona_toggle)

        self.persona_widget = QWidget()
        self.persona_widget.setVisible(False)
        pw = QVBoxLayout(self.persona_widget)
        pw.setContentsMargins(16, 4, 16, 4)

        hint = QLabel(
            "Emma's system prompt: who she is, who you are, her purpose. "
            "Sent to every model so she stays in character."
        )
        hint.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")
        hint.setWordWrap(True)
        pw.addWidget(hint)

        self.persona_edit = QPlainTextEdit()
        self.persona_edit.setPlaceholderText(
            "Example:\n"
            "You are Emma, a helpful AI personal assistant. "
            "You are warm, efficient, and communicative. "
            "The user is your creator and primary user..."
        )
        self.persona_edit.setFixedHeight(120)
        pw.addWidget(self.persona_edit)

        persona_btn_row = QHBoxLayout()
        self.persona_save_btn = QPushButton("Save Identity")
        self.persona_save_btn.clicked.connect(self._save_persona)
        persona_btn_row.addWidget(self.persona_save_btn)

        self.persona_status = QLabel("")
        self.persona_status.setStyleSheet(f"color: {theme.OK}; font-size: 11px;")
        persona_btn_row.addWidget(self.persona_status, stretch=1)
        persona_btn_row.addStretch()

        pw.addLayout(persona_btn_row)
        parent.addWidget(self.persona_widget)

    def _toggle_persona_editor(self):
        visible = not self.persona_widget.isVisible()
        self.persona_widget.setVisible(visible)
        self.persona_toggle.setText("▾ Identity & System Prompt" if visible else "▸ Identity & System Prompt")

    def _load_persona(self):
        self.client.call(
            lambda: self.client.get_persona(),
            on_success=self._on_persona_loaded,
            on_error=lambda e: None,
        )

    def _on_persona_loaded(self, data: dict):
        text = data.get("text", "")
        self.persona_edit.setPlainText(text)
        self.persona_status.setText("")

    def _save_persona(self):
        text = self.persona_edit.toPlainText()
        self.persona_save_btn.setEnabled(False)
        self.client.call(
            lambda: self.client.set_persona(text),
            on_success=lambda _: self._on_persona_saved(),
            on_error=lambda e: self._on_persona_save_error(e),
        )

    def _on_persona_saved(self):
        self.persona_save_btn.setEnabled(True)
        self.persona_status.setText("✓ Identity saved")
        self._append_system("Identity updated - Emma will use this system prompt on next message.")

    def _on_persona_save_error(self, msg: str):
        self.persona_save_btn.setEnabled(True)
        self.persona_status.setText(f"✗ {msg}")
        self.persona_status.setStyleSheet(f"color: {theme.ERR}; font-size: 11px;")

    # ---------- routing controls ----------
    def _toggle_manual(self, checked: bool):
        self.provider_combo.setEnabled(checked)
        self.model_combo.setEnabled(checked)
        self.task_combo.setEnabled(not checked)
        if checked:
            self._refresh_models(self.provider_combo.currentText())

    def _refresh_models(self, provider: str):
        if not provider:
            return
        self.client.call(
            lambda: self.client.provider_models(provider),
            on_success=self._populate_models,
            on_error=lambda e: None,  # non-fatal - the field stays editable
        )

    def _populate_models(self, data: dict):
        models = data.get("models", [])
        current = self.model_combo.currentText()
        self.model_combo.clear()
        if models:
            self.model_combo.addItems(models)
        if current:
            self.model_combo.setCurrentText(current)

    # ---------- chat log rendering ----------
    def _append_system(self, text: str):
        self.log.append(f'<p style="color:{theme.TEXT_DIM}; font-size:12px;">{html.escape(text)}</p>')

    def _append_user(self, text: str):
        safe = html.escape(text).replace("\n", "<br>")
        self.log.append(
            f'<p style="margin:6px 0;"><b style="color:{theme.ACCENT_HOVER};">You</b><br>{safe}</p>'
        )

    def _append_assistant(self, text: str, provider: str, model: str):
        safe = html.escape(text).replace("\n", "<br>")
        self.log.append(
            f'<p style="margin:6px 0;"><b style="color:{theme.OK};">Emma</b> '
            f'<span style="color:{theme.TEXT_DIM}; font-size:11px;">({html.escape(provider)} / {html.escape(model)})</span>'
            f'<br>{safe}</p>'
        )

    # ---------- send / receive / interrupt ----------
    def _send(self):
        message = self.input_box.toPlainText().strip()
        if not message:
            return
        self.input_box.clear()
        self._append_user(message)
        self.status_label.setText("Emma is thinking...")
        self.send_btn.setEnabled(False)
        self.interrupt_btn.setEnabled(True)

        session_id = self.session_edit.text().strip() or "default"
        task_type = self.task_combo.currentText()
        provider = self.provider_combo.currentText() if self.manual_check.isChecked() else None
        model = self.model_combo.currentText().strip() if self.manual_check.isChecked() else None

        chat_fn = lambda: self.client.chat(
            message,
            session_id=session_id,
            task_type=task_type,
            provider=provider or None,
            model=model or None,
            system=self._get_persona_text(),
        )

        self._pending_worker = self.client.call(
            chat_fn,
            on_success=lambda data: self._on_reply(message, data),
            on_error=self._on_error,
        )

    def _get_persona_text(self) -> str:
        text = self.persona_edit.toPlainText().strip()
        return text or None

    def _interrupt(self):
        if self._pending_worker is None:
            return
        self._pending_worker.cancel()
        self._pending_worker = None
        self._append_system("⏹ Emma was interrupted.")
        self.status_label.setText("Interrupted.")
        self.send_btn.setEnabled(True)
        self.interrupt_btn.setEnabled(False)

    def _on_reply(self, user_message: str, data: dict):
        if self._pending_worker is None:
            return
        self._pending_worker = None
        reply = data.get("reply", "")
        provider = data.get("provider", "?")
        model = data.get("model", "?")
        self._append_assistant(reply, provider, model)
        self.status_label.setText(f"Last reply via {provider} / {model} at {datetime.now().strftime('%H:%M:%S')}")
        self.send_btn.setEnabled(True)
        self.interrupt_btn.setEnabled(False)
        self.last_exchange = (user_message, reply)
        self.save_btn.setEnabled(True)

    def _on_error(self, message: str):
        if self._pending_worker is None:
            return
        self._pending_worker = None
        self._append_system(f"⚠ {message}")
        self.status_label.setText("Something went wrong - see message above.")
        self.send_btn.setEnabled(True)
        self.interrupt_btn.setEnabled(False)

    # ---------- save to memory ----------
    def _open_save_dialog(self):
        """Fetch categories/projects, then open the save dialog on the main thread."""
        self._save_categories = []
        self._save_projects = []
        self._save_fetches_done = 0
        self.save_btn.setEnabled(False)

        def _on_fetch_done():
            self._save_fetches_done += 1
            if self._save_fetches_done < 2:
                return
            self.save_btn.setEnabled(True)
            self._show_save_dialog(self._save_categories, self._save_projects)

        self.client.call(
            lambda: self.client.list_long_term_categories(),
            on_success=lambda cats: (setattr(self, '_save_categories', cats if isinstance(cats, list) else []), _on_fetch_done()),
            on_error=lambda _: _on_fetch_done(),
        )
        self.client.call(
            lambda: self.client.list_projects(),
            on_success=lambda projs: (setattr(self, '_save_projects', projs if isinstance(projs, list) else []), _on_fetch_done()),
            on_error=lambda _: _on_fetch_done(),
        )

    def _show_save_dialog(self, categories: list, projects: list):
        """Open the save dialog - must only be called on the main thread."""
        user_msg, reply = self.last_exchange
        prefill = f"Me: {user_msg}\nEmma: {reply}"

        dialog = SaveMemoryDialog(self, prefill, categories, projects)
        if dialog.exec():
            payload = dialog.result_payload()
            if not payload["targets"] or not payload["key"] or not payload["value"]:
                self._append_system("Save skipped - pick at least one target and fill in key/value.")
                return
            targets = list(payload["targets"])
            key = str(payload["key"])
            value = str(payload["value"])
            category = str(payload["category"]) if payload.get("category") else None
            project = str(payload["project"]) if payload.get("project") else None
            self.client.call(
                lambda: self.client.save_memory(
                    targets=targets, key=key, value=value,
                    category=category, project=project,
                ),
                on_success=lambda _, _k=key: self._append_system(f"Saved to memory: {_k}"),
                on_error=self._on_error,
            )