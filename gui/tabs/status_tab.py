from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.api_client import EmmaClient
from gui import theme


class StatusTab(QWidget):
    def __init__(self, client: EmmaClient):
        super().__init__()
        self.client = client
        layout = QVBoxLayout(self)

        title = QLabel("Busy Mode")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        self.state_label = QLabel("Checking status...")
        self.state_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.state_label)

        row = QHBoxLayout()
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("optional note, e.g. 'coding session'")
        self.busy_btn = QPushButton("I'm Busy")
        self.busy_btn.setStyleSheet(f"border: 1px solid {theme.WARN}; color: {theme.WARN};")
        self.busy_btn.clicked.connect(self.go_busy)
        self.free_btn = QPushButton("I'm Free")
        self.free_btn.setStyleSheet(f"border: 1px solid {theme.OK}; color: {theme.OK};")
        self.free_btn.clicked.connect(self.go_free)
        row.addWidget(self.note_edit, stretch=1)
        row.addWidget(self.busy_btn)
        row.addWidget(self.free_btn)
        layout.addLayout(row)

        hint = QLabel(
            "When you go busy, Emma will notify everyone in Telegram Notifications "
            "who has 'Notify on busy/free' enabled. Set custom messages per user there."
        )
        hint.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()

        self.refresh()

    def refresh(self):
        self.client.call(lambda: self.client.get_status(), on_success=self._on_status, on_error=lambda e: None)

    def _on_status(self, state: dict):
        if state.get("is_busy"):
            note = f" - {state['note']}" if state.get("note") else ""
            self.state_label.setText(f'<span style="color:{theme.WARN};">● Busy{note}</span>')
        else:
            self.state_label.setText(f'<span style="color:{theme.OK};">● Free</span>')

    def go_busy(self):
        note = self.note_edit.text().strip() or None
        self.client.call(lambda: self.client.go_busy(note), on_success=lambda _: self.refresh(), on_error=lambda e: None)

    def go_free(self):
        self.client.call(lambda: self.client.go_free(), on_success=lambda _: self.refresh(), on_error=lambda e: None)
