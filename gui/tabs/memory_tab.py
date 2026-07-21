"""
Memory tab - simple freeform text editors for each memory tier.

No key/value nonsense. Just text boxes:
  - Long-term: Emma's identity / system prompt (also editable from Chat tab)
  - Project: notes per project
  - Daily: today's journal, rolls over automatically
"""
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.api_client import EmmaClient
from gui import theme


class LongTermMemoryPane(QWidget):
    def __init__(self, client: EmmaClient, status_label: QLabel):
        super().__init__()
        self.client = client
        self.status_label = status_label
        layout = QVBoxLayout(self)

        label = QLabel(
            "Who Emma is, who you are, her purpose. "
            "This is injected as her system prompt on every message."
        )
        label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        label.setWordWrap(True)
        layout.addWidget(label)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "e.g.\n"
            "You are Emma, a personal AI assistant. You are warm, efficient, "
            "and communicative. The user is Alex, your creator. Your purpose "
            "is to help manage projects, tasks, and daily life."
        )
        layout.addWidget(self.editor, stretch=1)

        row = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save)
        row.addWidget(self.save_btn)
        row.addStretch()
        layout.addLayout(row)

        self._load()

    def _show_error(self, msg):
        self.status_label.setStyleSheet(f"color: {theme.ERR}; font-size: 12px;")
        self.status_label.setText(f"⚠ {msg}")

    def _show_success(self, msg):
        self.status_label.setStyleSheet(f"color: {theme.OK}; font-size: 12px;")
        self.status_label.setText(msg)

    def _load(self):
        self.client.call(
            lambda: self.client.get_long_term_text(),
            on_success=lambda d: self.editor.setPlainText(d.get("text", "")),
            on_error=lambda e: self._show_error(str(e)),
        )

    def _save(self):
        text = self.editor.toPlainText()
        self.client.call(
            lambda: self.client.set_long_term_text(text),
            on_success=lambda _: self._show_success("Long-term memory saved."),
            on_error=lambda e: self._show_error(str(e)),
        )


class ProjectMemoryPane(QWidget):
    def __init__(self, client: EmmaClient, status_label: QLabel):
        super().__init__()
        self.client = client
        self.status_label = status_label
        self.current_project = ""
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Project:"))
        self.project_combo = QComboBox()
        self.project_combo.setEditable(True)
        row.addWidget(self.project_combo, stretch=1)
        self.load_btn = QPushButton("Load")
        self.load_btn.clicked.connect(self._load)
        row.addWidget(self.load_btn)
        layout.addLayout(row)

        label = QLabel("Freeform notes Emma will remember about this project.")
        label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        layout.addWidget(label)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "e.g.\n"
            "Project HyperClutch: a WebRTC-based motion capture system. "
            "Built with Python, C++, and TypeScript. The user wants to "
            "ship MVP by August 2026."
        )
        layout.addWidget(self.editor, stretch=1)

        row2 = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save)
        row2.addWidget(self.save_btn)
        row2.addStretch()
        layout.addLayout(row2)

        self._refresh_projects()

    def _show_error(self, msg: str):
        self.status_label.setStyleSheet(f"color: {theme.ERR}; font-size: 12px;")
        self.status_label.setText(f"⚠ {msg}")

    def _show_success(self, msg: str):
        self.status_label.setStyleSheet(f"color: {theme.OK}; font-size: 12px;")
        self.status_label.setText(msg)

    def _refresh_projects(self):
        self.client.call(
            lambda: self.client.list_projects(),
            on_success=self._on_projects,
            on_error=lambda e: self._show_error(str(e)),
        )

    def _on_projects(self, projects: list):
        current = self.project_combo.currentText()
        self.project_combo.clear()
        self.project_combo.addItems(projects or ["emma"])
        if current:
            self.project_combo.setCurrentText(current)

    def _load(self):
        project = self.project_combo.currentText().strip()
        if not project:
            return
        self.current_project = project
        self.client.call(
            lambda _p=project: self.client.get_project_text(_p),
            on_success=lambda d: self.editor.setPlainText(d.get("text", "")),
            on_error=lambda e: self._show_error(str(e)),
        )

    def _save(self):
        project = self.project_combo.currentText().strip()
        if not project:
            return
        text = self.editor.toPlainText()
        self.client.call(
            lambda _p=project, _t=text: self.client.set_project_text(_p, _t),
            on_success=lambda _: self._show_success(f"Project '{project}' saved."),
            on_error=lambda e: self._show_error(str(e)),
        )


class DailyMemoryPane(QWidget):
    def __init__(self, client: EmmaClient, status_label: QLabel):
        super().__init__()
        self.client = client
        self.status_label = status_label
        layout = QVBoxLayout(self)

        label = QLabel("Today's notes. Emma will read this as context for the day. Resets each morning.")
        label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        label.setWordWrap(True)
        layout.addWidget(label)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "e.g.\n"
            "Today I'm focused on the HyperClutch frontend. "
            "Feeling energetic. Need to finish the WebRTC sync module."
        )
        layout.addWidget(self.editor, stretch=1)

        row = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save)
        row.addWidget(self.save_btn)
        row.addStretch()
        layout.addLayout(row)

        self._load()

    def _show_error(self, msg: str):
        self.status_label.setStyleSheet(f"color: {theme.ERR}; font-size: 12px;")
        self.status_label.setText(f"⚠ {msg}")

    def _show_success(self, msg: str):
        self.status_label.setStyleSheet(f"color: {theme.OK}; font-size: 12px;")
        self.status_label.setText(msg)

    def _load(self):
        self.client.call(
            lambda: self.client.get_daily_text(),
            on_success=lambda d: self.editor.setPlainText(d.get("text", "")),
            on_error=lambda e: self._show_error(str(e)),
        )

    def _save(self):
        text = self.editor.toPlainText()
        self.client.call(
            lambda: self.client.set_daily_text(text),
            on_success=lambda _: self._show_success("Daily memory saved."),
            on_error=lambda e: self._show_error(str(e)),
        )


class MemoryTab(QWidget):
    def __init__(self, client: EmmaClient):
        super().__init__()
        layout = QVBoxLayout(self)
        title = QLabel("Memory")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        self.status_label = QLabel(" ")
        self.status_label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")

        tabs = QTabWidget()
        tabs.addTab(LongTermMemoryPane(client, self.status_label), "Long-term")
        tabs.addTab(ProjectMemoryPane(client, self.status_label), "Project")
        tabs.addTab(DailyMemoryPane(client, self.status_label), "Daily")
        layout.addWidget(tabs)
        layout.addWidget(self.status_label)