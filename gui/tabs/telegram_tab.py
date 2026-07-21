"""
Telegram tab — manage Emma's notification bot.

People who message the bot are automatically registered below. You can set
who they are to you (label/role), their priority (high breaks through busy
mode), custom busy/free messages, and flag one as the owner for alerts.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.api_client import EmmaClient
from gui import theme

ROLES = ["friend", "family", "work", "other"]
PRIORITIES = ["normal", "high", "low"]


class TelegramTab(QWidget):
    def __init__(self, client: EmmaClient):
        super().__init__()
        self.client = client
        layout = QVBoxLayout(self)

        title = QLabel("Telegram Notifications")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        bot_row = QHBoxLayout()
        self.status_label = QLabel("Bot: not running")
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

        hint = QLabel(
            "People who message your bot are automatically registered below. "
            "Select a user to edit their settings — assign a label so Emma knows "
            "who they are, set priority, custom messages, or make them the owner."
        )
        hint.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Telegram ID", "Name", "Label", "Role", "Priority", "Notify", "Chat ID"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._on_user_selected)
        layout.addWidget(self.table, stretch=1)

        fields = QVBoxLayout()

        row1 = QHBoxLayout()
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Label")
        row1.addWidget(self.label_edit)

        self.role_combo = QComboBox()
        self.role_combo.addItems(ROLES)
        row1.addWidget(self.role_combo)

        self.set_label_btn = QPushButton("Set Label & Role")
        self.set_label_btn.clicked.connect(self._set_label_role)
        row1.addWidget(self.set_label_btn)

        self.priority_combo = QComboBox()
        self.priority_combo.addItems(PRIORITIES)
        row1.addWidget(QLabel("Priority:"))
        row1.addWidget(self.priority_combo)

        self.set_priority_btn = QPushButton("Set Priority")
        self.set_priority_btn.clicked.connect(self._set_priority)
        row1.addWidget(self.set_priority_btn)

        row1.addStretch()
        fields.addLayout(row1)

        row2 = QHBoxLayout()
        self.notify_cb = QCheckBox("Notify on busy/free")
        row2.addWidget(self.notify_cb)

        self.set_notify_btn = QPushButton("Apply")
        self.set_notify_btn.clicked.connect(self._set_notify)
        row2.addWidget(self.set_notify_btn)

        self.chat_id_edit = QLineEdit()
        self.chat_id_edit.setPlaceholderText("Chat ID (manual)")
        row2.addWidget(self.chat_id_edit)

        self.set_chat_btn = QPushButton("Set Chat ID")
        self.set_chat_btn.clicked.connect(self._set_chat_id)
        row2.addWidget(self.set_chat_btn)

        self.owner_btn = QPushButton("Set as Owner")
        self.owner_btn.clicked.connect(self._set_owner)
        row2.addWidget(self.owner_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh)
        row2.addWidget(self.refresh_btn)

        fields.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Busy message:"))
        self.busy_msg_edit = QLineEdit()
        self.busy_msg_edit.setPlaceholderText("e.g. 'Busy with work — will reply later'")
        row3.addWidget(self.busy_msg_edit, stretch=1)
        row3.addWidget(QLabel("Free message:"))
        self.free_msg_edit = QLineEdit()
        self.free_msg_edit.setPlaceholderText("e.g. 'I'm free now!'")
        row3.addWidget(self.free_msg_edit, stretch=1)
        self.set_msgs_btn = QPushButton("Set Messages")
        self.set_msgs_btn.clicked.connect(self._set_messages)
        row3.addWidget(self.set_msgs_btn)
        fields.addLayout(row3)

        layout.addLayout(fields)

        msg_label = QLabel("Incoming messages:")
        msg_label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px; margin-top: 8px;")
        layout.addWidget(msg_label)

        self.msg_log = QTableWidget(0, 4)
        self.msg_log.setHorizontalHeaderLabels(["Time", "Name", "Telegram ID", "Message"])
        self.msg_log.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.msg_log.setMaximumHeight(160)
        layout.addWidget(self.msg_log)

        self._refresh()

    def _refresh(self):
        self.client.call(lambda: self.client.bot_status(), on_success=self._on_status, on_error=lambda e: None)
        self.client.call(lambda: self.client.list_telegram_users(), on_success=self._fill_users, on_error=lambda e: None)
        self.client.call(lambda: self.client.telegram_messages(), on_success=self._fill_messages, on_error=lambda e: None)

    def _on_status(self, data: dict):
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
            self.table.setItem(row, 0, QTableWidgetItem(str(u.get("telegram_id", ""))))
            self.table.setItem(row, 1, QTableWidgetItem(u.get("name", "")))
            self.table.setItem(row, 2, QTableWidgetItem(u.get("label", "")))
            self.table.setItem(row, 3, QTableWidgetItem(u.get("role", "")))
            self.table.setItem(row, 4, QTableWidgetItem(u.get("priority", "normal")))
            notify_item = QTableWidgetItem("✓" if u.get("notify_on_busy") else "")
            notify_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 5, notify_item)
            self.table.setItem(row, 6, QTableWidgetItem(str(u.get("chat_id", "") or "")))

    def _fill_messages(self, messages: list):
        self.msg_log.setRowCount(0)
        for row, m in enumerate(reversed(messages[-50:])):
            self.msg_log.insertRow(row)
            self.msg_log.setItem(row, 0, QTableWidgetItem(m.get("timestamp", "")))
            self.msg_log.setItem(row, 1, QTableWidgetItem(m.get("name", "")))
            self.msg_log.setItem(row, 2, QTableWidgetItem(str(m.get("user_id", ""))))
            self.msg_log.setItem(row, 3, QTableWidgetItem(m.get("text", "")))

    def _on_user_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.label_edit.setText(self.table.item(row, 2).text() or self.table.item(row, 1).text())
        role_text = self.table.item(row, 3).text()
        idx = self.role_combo.findText(role_text)
        if idx >= 0:
            self.role_combo.setCurrentIndex(idx)
        prio_text = self.table.item(row, 4).text()
        idx = self.priority_combo.findText(prio_text)
        if idx >= 0:
            self.priority_combo.setCurrentIndex(idx)
        self.notify_cb.setChecked(self.table.item(row, 5).text() == "✓")
        self.chat_id_edit.setText(self.table.item(row, 6).text())

    def _start_bot(self):
        self.client.call(lambda: self.client.start_bot(), on_success=lambda _: self._refresh(), on_error=lambda e: None)

    def _stop_bot(self):
        self.client.call(lambda: self.client.stop_bot(), on_success=lambda _: self._refresh(), on_error=lambda e: None)

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return int(self.table.item(row, 0).text())

    def _set_label_role(self):
        tid = self._selected_id()
        if tid is None:
            return
        label = self.label_edit.text().strip()
        role = self.role_combo.currentText()
        if not label:
            return
        self.client.call(
            lambda: self.client.label_telegram_user(tid, label, role),
            on_success=lambda _: self._refresh(),
            on_error=lambda e: None,
        )

    def _set_priority(self):
        tid = self._selected_id()
        if tid is None:
            return
        priority = self.priority_combo.currentText()
        self.client.call(
            lambda: self.client.set_telegram_priority(tid, priority),
            on_success=lambda _: self._refresh(),
            on_error=lambda e: None,
        )

    def _set_notify(self):
        tid = self._selected_id()
        if tid is None:
            return
        enabled = self.notify_cb.isChecked()
        self.client.call(
            lambda: self.client.set_telegram_notify_on_busy(tid, enabled),
            on_success=lambda _: self._refresh(),
            on_error=lambda e: None,
        )

    def _set_messages(self):
        tid = self._selected_id()
        if tid is None:
            return
        busy = self.busy_msg_edit.text().strip() or None
        free = self.free_msg_edit.text().strip() or None
        self.client.call(
            lambda: self.client.set_telegram_messages(tid, busy, free),
            on_success=lambda _: self._refresh(),
            on_error=lambda e: None,
        )

    def _set_chat_id(self):
        tid = self._selected_id()
        if tid is None:
            return
        raw = self.chat_id_edit.text().strip()
        if not raw:
            return
        try:
            chat_id = int(raw)
        except ValueError:
            return
        self.client.call(
            lambda: self.client.set_telegram_chat_id(tid, chat_id),
            on_success=lambda _: self._refresh(),
            on_error=lambda e: None,
        )

    def _set_owner(self):
        tid = self._selected_id()
        if tid is None:
            return
        self.client.call(
            lambda: self.client.set_telegram_owner(tid),
            on_success=lambda _: self._refresh(),
            on_error=lambda e: None,
        )
