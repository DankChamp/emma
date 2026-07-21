from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.api_client import EmmaClient
from gui import theme

PRIORITIES = ["normal", "high", "low"]


class NotificationsBusyTab(QWidget):
    def __init__(self, client: EmmaClient):
        super().__init__()
        self.client = client
        layout = QVBoxLayout(self)

        title = QLabel("Notifications & Busy Mode")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        # ---- Busy / Free toggle ----
        self.state_label = QLabel("Checking status...")
        self.state_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.state_label)

        row = QHBoxLayout()
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("optional note, e.g. 'coding session'")
        self.busy_btn = QPushButton("I'm Busy")
        self.busy_btn.setStyleSheet(f"border: 1px solid {theme.WARN}; color: {theme.WARN};")
        self.busy_btn.clicked.connect(self._go_busy)
        self.free_btn = QPushButton("I'm Free")
        self.free_btn.setStyleSheet(f"border: 1px solid {theme.OK}; color: {theme.OK};")
        self.free_btn.clicked.connect(self._go_free)
        row.addWidget(self.note_edit, stretch=1)
        row.addWidget(self.busy_btn)
        row.addWidget(self.free_btn)
        layout.addLayout(row)

        # ---- Telegram bot ----
        bot_row = QHBoxLayout()
        self.status_label = QLabel("Bot: checking...")
        self.status_label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 13px;")
        bot_row.addWidget(self.status_label)
        bot_row.addStretch()
        self.start_btn = QPushButton("Start Bot")
        self.start_btn.clicked.connect(self._start_bot)
        self.stop_btn = QPushButton("Stop Bot")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_bot)
        bot_row.addWidget(self.start_btn)
        bot_row.addWidget(self.stop_btn)
        layout.addLayout(bot_row)

        # ---- People table ----
        hint = QLabel(
            "People who message your bot are automatically registered below. "
            "Select a person to set their priority, a custom busy message, "
            "or write a prompt explaining who they are to you."
        )
        hint.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Priority", "Notify"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._on_user_selected)
        layout.addWidget(self.table, stretch=1)

        # ---- Edit form ----
        self.selected_label = QLabel("Select a person above to edit their settings.")
        self.selected_label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        layout.addWidget(self.selected_label)

        edit_row = QHBoxLayout()
        edit_row.addWidget(QLabel("Priority:"))
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(PRIORITIES)
        edit_row.addWidget(self.priority_combo)
        self.notify_cb = QCheckBox("Notify on busy/free")
        edit_row.addWidget(self.notify_cb)

        edit_row.addWidget(QLabel("Message:"))
        self.msg_edit = QLineEdit()
        self.msg_edit.setPlaceholderText("e.g. 'Busy with work — will reply later'")
        edit_row.addWidget(self.msg_edit, stretch=1)
        layout.addLayout(edit_row)

        layout.addWidget(QLabel("Prompt — who this person is to you:"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "e.g. Alex is my boss at work, been my manager for 3 years. "
            "We have weekly 1:1s every Monday."
        )
        self.prompt_edit.setMaximumHeight(100)
        layout.addWidget(self.prompt_edit)

        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Changes")
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self.refresh_btn)
        layout.addLayout(btn_row)

        self._refresh()

    # ---------- data ----------
    def _refresh(self):
        self.client.call(lambda: self.client.get_status(), on_success=self._on_status, on_error=lambda e: None)
        self.client.call(lambda: self.client.bot_status(), on_success=self._on_bot_status, on_error=lambda e: None)
        self.client.call(lambda: self.client.list_telegram_users(), on_success=self._fill_users, on_error=lambda e: None)

    def _on_status(self, state: dict):
        if state.get("is_busy"):
            note = f" - {state['note']}" if state.get("note") else ""
            self.state_label.setText(f'<span style="color:{theme.WARN};">● Busy{note}</span>')
        else:
            self.state_label.setText(f'<span style="color:{theme.OK};">● Free</span>')

    def _on_bot_status(self, data: dict):
        running = data.get("running", False)
        has_token = data.get("has_token", False)
        if not has_token:
            self.status_label.setText("Bot: no token — set TELEGRAM_BOT_TOKEN in .env")
            self.status_label.setStyleSheet(f"color: {theme.ERR}; font-size: 13px;")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
        elif running:
            self.status_label.setText("Bot: ● running")
            self.status_label.setStyleSheet(f"color: {theme.OK}; font-size: 13px;")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        else:
            self.status_label.setText("Bot: ○ stopped")
            self.status_label.setStyleSheet(f"color: {theme.WARN}; font-size: 13px;")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

    def _fill_users(self, users: list):
        self.table.setRowCount(0)
        for row, u in enumerate(users):
            self.table.insertRow(row)

            item = QTableWidgetItem(u.get("name", ""))
            item.setData(Qt.UserRole, {
                "priority": u.get("priority", "normal"),
                "notify_on_busy": u.get("notify_on_busy", False),
                "busy_message": u.get("busy_message"),
                "prompt": u.get("prompt"),
                "telegram_id": u.get("telegram_id"),
            })
            self.table.setItem(row, 0, item)

            self.table.setItem(row, 1, QTableWidgetItem(u.get("priority", "normal")))

            notify_item = QTableWidgetItem("✓" if u.get("notify_on_busy") else "")
            notify_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, notify_item)

    # ---------- busy / free ----------
    def _go_busy(self):
        note = self.note_edit.text().strip() or None
        self.client.call(lambda: self.client.go_busy(note), on_success=lambda _: self._refresh(), on_error=lambda e: None)

    def _go_free(self):
        self.client.call(lambda: self.client.go_free(), on_success=lambda _: self._refresh(), on_error=lambda e: None)

    # ---------- bot ----------
    def _start_bot(self):
        self.client.call(lambda: self.client.start_bot(), on_success=lambda _: self._refresh(), on_error=lambda e: None)

    def _stop_bot(self):
        self.client.call(lambda: self.client.stop_bot(), on_success=lambda _: self._refresh(), on_error=lambda e: None)

    # ---------- edit ----------
    def _on_user_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        data = self.table.item(row, 0).data(Qt.UserRole) or {}
        name = self.table.item(row, 0).text()
        self.selected_label.setText(f"Editing: {name}")

        prio = data.get("priority", "normal")
        idx = self.priority_combo.findText(prio)
        if idx >= 0:
            self.priority_combo.setCurrentIndex(idx)

        self.notify_cb.setChecked(bool(data.get("notify_on_busy")))
        self.msg_edit.setText(data.get("busy_message") or "")
        self.prompt_edit.setPlainText(data.get("prompt") or "")

    def _save(self):
        row = self.table.currentRow()
        if row < 0:
            return
        data = self.table.item(row, 0).data(Qt.UserRole) or {}
        tid = data.get("telegram_id")
        if tid is None:
            return

        priority = self.priority_combo.currentText()
        notify = self.notify_cb.isChecked()
        busy_msg = self.msg_edit.text().strip() or None
        prompt = self.prompt_edit.toPlainText().strip() or None

        self.client.call(lambda: self.client.set_telegram_priority(tid, priority), on_success=lambda _: None, on_error=lambda e: None)
        self.client.call(lambda: self.client.set_telegram_notify_on_busy(tid, notify), on_success=lambda _: None, on_error=lambda e: None)
        self.client.call(lambda: self.client.set_telegram_messages(tid, busy_msg), on_success=lambda _: None, on_error=lambda e: None)
        self.client.call(lambda: self.client.set_telegram_prompt(tid, prompt), on_success=lambda _: self._refresh(), on_error=lambda e: None)
