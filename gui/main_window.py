"""
EmmaMainWindow - the shell: a sidebar of sections and a stacked area on
the right. Deliberately simple navigation (QListWidget + QStackedWidget)
so adding a future section is a two-line change, matching the same
"modular, easy to extend" principle the backend follows.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from gui.api_client import EmmaClient
from gui.tabs.chat_tab import ChatTab
from gui.tabs.providers_tab import ProvidersTab
from gui.tabs.memory_tab import MemoryTab
from gui.tabs.schedule_tab import ScheduleTab
from gui.tabs.voice_tab import VoiceTab
from gui.tabs.selfcare_tab import SelfCareTab
from gui.tabs.notifications_busy_tab import NotificationsBusyTab

SECTIONS = [
    ("Talk", "chat"),
    ("Voice", "voice"),
    ("Providers & Keys", "providers"),
    ("Memory", "memory"),
    ("Schedule", "schedule"),
    ("Notifications & Busy Mode", "notifications_busy"),
    ("Self-Care", "selfcare"),
]


class EmmaMainWindow(QMainWindow):
    def __init__(self, client: EmmaClient):
        super().__init__()
        self.client = client
        self.setWindowTitle("Emma")
        self.resize(1180, 760)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # ---- sidebar ----
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        side_layout = QHBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)

        from PySide6.QtWidgets import QVBoxLayout

        side_col = QVBoxLayout()
        side_col.setContentsMargins(0, 0, 0, 0)
        side_col.setSpacing(0)

        brand = QLabel("EMMA")
        brand.setObjectName("Brand")
        side_col.addWidget(brand)

        subtitle = QLabel("PERSONAL OS // VOID")
        subtitle.setObjectName("BrandSub")
        side_col.addWidget(subtitle)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        for label, _key in SECTIONS:
            QListWidgetItem(label, self.nav)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        side_col.addWidget(self.nav, stretch=1)

        side_layout.addLayout(side_col)
        root.addWidget(sidebar)

        # ---- stacked content ----
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(16, 16, 16, 16)

        self.chat_tab = ChatTab(client)
        self.voice_tab = VoiceTab(client)
        self.providers_tab = ProvidersTab(client)
        self.memory_tab = MemoryTab(client)
        self.schedule_tab = ScheduleTab(client)
        self.selfcare_tab = SelfCareTab(client)
        self.notifications_busy_tab = NotificationsBusyTab(client)

        for widget in (
            self.chat_tab,
            self.voice_tab,
            self.providers_tab,
            self.memory_tab,
            self.schedule_tab,
            self.notifications_busy_tab,
            self.selfcare_tab,
        ):
            wrapper = self._padded(widget)
            self.stack.addWidget(wrapper)

        root.addWidget(self.stack, stretch=1)

        self.nav.setCurrentRow(0)

        # ---- status bar: backend connection indicator ----
        self.setStatusBar(QStatusBar())
        self.connection_label = QLabel(f"backend: {client.base_url}")
        self.statusBar().addPermanentWidget(self.connection_label)
        self._check_connection()

    @staticmethod
    def _padded(widget: QWidget) -> QWidget:
        from PySide6.QtWidgets import QVBoxLayout

        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(widget)
        return wrapper

    def _on_nav_changed(self, row: int):
        if row >= 0:
            self.stack.setCurrentIndex(row)

    def _check_connection(self):
        self.client.call(
            lambda: self.client.ping(),
            on_success=lambda _: self.connection_label.setText(f"● connected - {self.client.base_url}"),
            on_error=lambda e: self.connection_label.setText(f"● not connected - {self.client.base_url}"),
        )
