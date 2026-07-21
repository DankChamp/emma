"""
Emma Desktop entry point.

Run with:
    python -m gui.app

Make sure the backend is running first (in another terminal):
    uvicorn main:app --reload

The GUI talks to it over plain HTTP, same as any other client would.
"""
import sys

from PySide6.QtWidgets import QApplication

from gui.api_client import DEFAULT_BASE_URL, EmmaClient
from gui.main_window import EmmaMainWindow
from gui.theme import VOID_QSS


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(VOID_QSS)
    app.setApplicationName("Emma")

    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    client = EmmaClient(base_url)

    window = EmmaMainWindow(client)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
