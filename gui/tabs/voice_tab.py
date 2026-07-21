"""
Voice tab - start/stop Emma's wake-word voice assistant and watch its log.

The voice assistant does blocking, C-level audio I/O (sounddevice/Vosk),
which doesn't mix safely with Qt's event loop in-process. So, matching the
rest of Emma's "everything is just a client" architecture, the GUI simply
launches `emma_voice.py` as a subprocess - the exact same thing the user
would type in a terminal - and streams its stdout into a log box. Stopping
the voice assistant just terminates that subprocess.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import theme
from gui.api_client import EmmaClient

EMMA_ROOT = Path(__file__).resolve().parent.parent.parent


class VoiceTab(QWidget):
    def __init__(self, client: EmmaClient):
        super().__init__()
        self.client = client
        self.process: QProcess | None = None

        layout = QVBoxLayout(self)

        title = QLabel("Voice")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        hint = QLabel(
            "Runs emma_voice.py as a background process. Say the wake word, then your "
            "command - Emma answers out loud in a natural feminine voice. Fully offline: "
            "wake-word and speech recognition run locally (Vosk), and replies are spoken "
            "locally with a neural voice (Piper). For the best voice, run once: "
            "python voice/download_voice.py"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addWidget(QLabel("Wake word:"))
        self.wake_word_edit = QLineEdit("hey emma")
        row.addWidget(self.wake_word_edit, stretch=1)
        self.start_btn = QPushButton("Start Listening")
        self.start_btn.setStyleSheet(f"border: 1px solid {theme.OK}; color: {theme.OK};")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setStyleSheet(f"border: 1px solid {theme.ERR}; color: {theme.ERR};")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        layout.addLayout(row)

        self.status_label = QLabel("Not running.")
        self.status_label.setStyleSheet(f"color: {theme.TEXT_DIM};")
        layout.addWidget(self.status_label)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(f"font-family: {theme.FONT_MONO}; font-size: 12px;")
        layout.addWidget(self.log, stretch=1)

    def start(self):
        if self.process is not None:
            return

        wake_word = self.wake_word_edit.text().strip() or "hey emma"

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(EMMA_ROOT))
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._on_output)
        self.process.finished.connect(self._on_finished)

        python = str(EMMA_ROOT / ".venv" / "bin" / "python")
        args = [
            str(EMMA_ROOT / "emma_voice.py"),
            "--wake-word",
            wake_word,
            "--backend-url",
            self.client.base_url,
        ]
        self.log.clear()
        self.process.start(python, args)

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText(f'Listening for "{wake_word}"...')
        self.status_label.setStyleSheet(f"color: {theme.OK};")

    def stop(self):
        if self.process is None:
            return
        self.process.terminate()
        if not self.process.waitForFinished(2000):
            self.process.kill()

    def _on_output(self):
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if data:
            self.log.appendPlainText(data.rstrip("\n"))

    def _on_finished(self, exit_code, exit_status):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Not running.")
        self.status_label.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self.process = None
