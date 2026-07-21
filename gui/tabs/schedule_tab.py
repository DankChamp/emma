from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QMessageBox,
)


class ScheduleTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()
        self._connect_signals()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Date + status row
        top = QHBoxLayout()
        top.addWidget(QLabel("Date:"))
        self.date_picker = QDateEdit(QDate.currentDate())
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDisplayFormat("yyyy-MM-dd")
        top.addWidget(self.date_picker)
        self.status_label = QLabel("Free")
        self.status_label.setStyleSheet("font-weight:600;color:#2dd4bf")
        top.addWidget(self.status_label)
        top.addStretch()
        layout.addLayout(top)

        # AI generate section
        layout.addWidget(QLabel("Describe your day (AI generates the timetable):"))
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("e.g. Meeting at 10am, lunch at noon, gym at 3pm, work on project until 5pm")
        self.text_input.setMaximumHeight(80)
        layout.addWidget(self.text_input)
        self.generate_btn = QPushButton("Generate Schedule")
        self.generate_btn.setObjectName("PrimaryButton")
        layout.addWidget(self.generate_btn)

        # Table
        layout.addWidget(QLabel("Schedule:"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Start", "End", "Title", "Busy", "Notify"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        layout.addWidget(self.table)

        # Action buttons
        actions = QHBoxLayout()
        self.add_btn = QPushButton("Add Block")
        self.delete_btn = QPushButton("Delete Block")
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("PrimaryButton")
        self.refresh_btn = QPushButton("Refresh")
        actions.addWidget(self.add_btn)
        actions.addWidget(self.delete_btn)
        actions.addStretch()
        actions.addWidget(self.save_btn)
        actions.addWidget(self.refresh_btn)
        layout.addLayout(actions)

        # Owner section
        layout.addSpacing(16)
        owner_row = QHBoxLayout()
        owner_row.addWidget(QLabel("Set yourself as owner (for notifications):"))
        self.owner_combo = QComboBox()
        self.owner_combo.setMinimumWidth(200)
        owner_row.addWidget(self.owner_combo)
        self.set_owner_btn = QPushButton("Set as Owner")
        owner_row.addWidget(self.set_owner_btn)
        owner_row.addStretch()
        layout.addLayout(owner_row)

        layout.addStretch()

    def _connect_signals(self):
        self.date_picker.dateChanged.connect(self.refresh)
        self.generate_btn.clicked.connect(self._generate)
        self.add_btn.clicked.connect(self._add_block)
        self.delete_btn.clicked.connect(self._delete_block)
        self.save_btn.clicked.connect(self._save)
        self.refresh_btn.clicked.connect(self.refresh)
        self.set_owner_btn.clicked.connect(self._set_owner)

    def refresh(self):
        self._load_schedule()
        self._load_status()
        self._load_users()

    def _load_status(self):
        self.client.call(
            lambda: self.client.get_status(),
            on_success=self._on_status,
        )

    def _on_status(self, data: Any):
        if isinstance(data, dict) and data.get("is_busy"):
            note = data.get("note", "")
            txt = f"● Busy" + (f" - {note}" if note else "")
            self.status_label.setText(txt)
            self.status_label.setStyleSheet("font-weight:600;color:#f59e0b")
        else:
            self.status_label.setText("● Free")
            self.status_label.setStyleSheet("font-weight:600;color:#2dd4bf")

    def _load_schedule(self):
        day = self.date_picker.date().toString("yyyy-MM-dd")
        self.client.call(
            lambda: self.client.list_schedule(day),
            on_success=self._on_schedule,
        )

    def _on_schedule(self, data: Any):
        blocks = data if isinstance(data, list) else []
        self.table.setRowCount(len(blocks))
        for i, b in enumerate(blocks):
            start = b.get("start", "")
            end = b.get("end", "")
            self._set_time_item(i, 0, start)
            self._set_time_item(i, 1, end)
            title = b.get("title", "")
            item = QTableWidgetItem(title)
            self.table.setItem(i, 2, item)

            busy_cb = QCheckBox()
            busy_cb.setChecked(bool(b.get("busy", True)))
            self.table.setCellWidget(i, 3, busy_cb)

            notify_cb = QCheckBox()
            notify_cb.setChecked(True)
            self.table.setCellWidget(i, 4, notify_cb)

        if not blocks:
            self.table.setRowCount(0)

    def _set_time_item(self, row: int, col: int, iso_str: str):
        try:
            dt = datetime.fromisoformat(iso_str)
            widget = QTimeEdit(dt.time())
        except (ValueError, TypeError):
            h, m = 9, 0
            if ":" in str(iso_str):
                parts = str(iso_str).split(":")
                h = int(parts[0]) if parts[0].isdigit() else 9
                m = int(parts[1][:2]) if len(parts) > 1 and parts[1][:2].isdigit() else 0
            widget = QTimeEdit(time(h, m))
        widget.setDisplayFormat("HH:mm")
        self.table.setCellWidget(row, col, widget)

    def _load_users(self):
        self.client.call(
            lambda: self.client.list_telegram_users(),
            on_success=self._on_users,
        )

    def _on_users(self, data: Any):
        users = data if isinstance(data, list) else []
        self.owner_combo.clear()
        self._user_map = {}
        for u in users:
            tid = u.get("telegram_id", 0)
            name = u.get("name", str(tid))
            label = f"{name} (tid: {tid})"
            self.owner_combo.addItem(label, tid)
            self._user_map[tid] = u

    def _generate(self):
        text = self.text_input.toPlainText().strip()
        if not text:
            return
        day = self.date_picker.date().toString("yyyy-MM-dd")
        self.generate_btn.setEnabled(False)
        self.generate_btn.setText("Generating...")
        self.client.call(
            lambda: self.client.build_schedule(day, text),
            on_success=self._on_generated,
            on_error=self._on_gen_error,
        )

    def _on_generated(self, data: Any):
        self.generate_btn.setEnabled(True)
        self.generate_btn.setText("Generate Schedule")
        self._load_schedule()

    def _on_gen_error(self, msg: str):
        self.generate_btn.setEnabled(True)
        self.generate_btn.setText("Generate Schedule")
        QMessageBox.warning(self, "Error", f"Failed to generate schedule:\n{msg}")

    def _add_block(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_time_item(row, 0, "09:00")
        self._set_time_item(row, 1, "10:00")
        self.table.setItem(row, 2, QTableWidgetItem(""))
        cb = QCheckBox()
        cb.setChecked(True)
        self.table.setCellWidget(row, 3, cb)
        cb2 = QCheckBox()
        cb2.setChecked(True)
        self.table.setCellWidget(row, 4, cb2)

    def _delete_block(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _save(self):
        day = self.date_picker.date().toString("yyyy-MM-dd")
        blocks = []
        for i in range(self.table.rowCount()):
            start_w = self.table.cellWidget(i, 0)
            end_w = self.table.cellWidget(i, 1)
            title_item = self.table.item(i, 2)
            busy_w = self.table.cellWidget(i, 3)
            notify_w = self.table.cellWidget(i, 4)

            start_t = start_w.time() if isinstance(start_w, QTimeEdit) else time(9, 0)
            end_t = end_w.time() if isinstance(end_w, QTimeEdit) else time(10, 0)
            title = title_item.text().strip() if title_item else ""
            busy = busy_w.isChecked() if isinstance(busy_w, QCheckBox) else True

            blocks.append({
                "start": start_t.toString("HH:mm"),
                "end": end_t.toString("HH:mm"),
                "title": title or "Busy",
                "busy": busy,
            })

        self.save_btn.setEnabled(False)
        self.save_btn.setText("Saving...")
        self.client.call(
            lambda: self.client.save_schedule(day, blocks),
            on_success=self._on_saved,
            on_error=self._on_save_error,
        )

    def _on_saved(self, data: Any):
        self.save_btn.setEnabled(True)
        self.save_btn.setText("Save")
        self._load_schedule()

    def _on_save_error(self, msg: str):
        self.save_btn.setEnabled(True)
        self.save_btn.setText("Save")
        QMessageBox.warning(self, "Error", f"Failed to save schedule:\n{msg}")

    def _set_owner(self):
        tid = self.owner_combo.currentData()
        if not tid:
            return
        self.client.call(
            lambda: self.client.set_telegram_owner(tid),
            on_success=lambda _: QMessageBox.information(self, "Done", "Owner updated."),
            on_error=lambda msg: QMessageBox.warning(self, "Error", msg),
        )
