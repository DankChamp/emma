"""
Providers & API Keys tab.

One card per provider Emma knows about. Each card shows live status
(online/offline, configured or not), lets you paste/replace its API key,
pick its default model from a live or suggested list, and test it -
all without ever opening .env by hand.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.api_client import EmmaClient
from gui import theme

PROVIDER_LABELS = {
    "ollama": "Ollama (Local)",
    "local_generic": "Local Server (Any Model)",
    "groq": "Groq (Cloud - fast)",
    "nvidia_nim": "NVIDIA NIM (Cloud - coding/reasoning)",
}

PROVIDER_HINTS = {
    "ollama": "Runs on your ThinkPad. No key needed - just make sure `ollama serve` is running.",
    "local_generic": (
        "Point this at ANY OpenAI-compatible local server: LM Studio "
        "(http://localhost:1234), llama.cpp's llama-server (http://localhost:8080), "
        "text-generation-webui, vLLM, KoboldCpp, LocalAI. Leave the API key blank unless "
        "the server asks for one."
    ),
    "groq": "Get a free key at console.groq.com/keys. Used for fast conversation and general tasks.",
    "nvidia_nim": "Get a key at build.nvidia.com. Emma prefers this for coding and deep reasoning.",
}

# Providers whose base URL is user-editable (as opposed to a fixed cloud
# endpoint). "ollama" keeps using the legacy dedicated endpoint; anything
# else added here goes through the generic /settings/providers/{name}/base-url.
LOCAL_URL_DEFAULTS = {
    "ollama": "http://localhost:11434",
    "local_generic": "http://localhost:1234",
}


class ProviderCard(QFrame):
    def __init__(self, client: EmmaClient, name: str):
        super().__init__()
        self.client = client
        self.name = name
        self.setProperty("class", "Card")
        self.setStyleSheet("QFrame.Card {}")  # class selector picked up from theme.py

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel(PROVIDER_LABELS.get(name, name))
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        top.addWidget(title)
        top.addStretch()
        self.status_label = QLabel("checking...")
        self.status_label.setTextFormat(Qt.RichText)
        top.addWidget(self.status_label)
        layout.addLayout(top)

        hint = QLabel(PROVIDER_HINTS.get(name, ""))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        layout.addWidget(hint)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)

        row = 0
        is_local_url_provider = name in LOCAL_URL_DEFAULTS

        if is_local_url_provider:
            grid.addWidget(QLabel("Base URL:"), row, 0)
            self.base_url_edit = QLineEdit(LOCAL_URL_DEFAULTS[name])
            grid.addWidget(self.base_url_edit, row, 1)
            self.save_url_btn = QPushButton("Save URL")
            self.save_url_btn.clicked.connect(self._save_base_url)
            grid.addWidget(self.save_url_btn, row, 3)
            row += 1

        # Every provider except plain Ollama can take an API key. For
        # local_generic it's optional (most local servers don't check it).
        if name != "ollama":
            key_label = "API Key (optional):" if is_local_url_provider else "API Key:"
            grid.addWidget(QLabel(key_label), row, 0)
            self.key_edit = QLineEdit()
            self.key_edit.setEchoMode(QLineEdit.Password)
            self.key_edit.setPlaceholderText(
                "Only if your server requires one" if is_local_url_provider else "Paste your API key here"
            )
            grid.addWidget(self.key_edit, row, 1)

            self.show_btn = QPushButton("Show")
            self.show_btn.setFixedWidth(60)
            self.show_btn.clicked.connect(self._toggle_visibility)
            grid.addWidget(self.show_btn, row, 2)

            self.save_key_btn = QPushButton("Save Key")
            self.save_key_btn.clicked.connect(self._save_key)
            grid.addWidget(self.save_key_btn, row, 3)
            row += 1

        grid.addWidget(QLabel("Default model:"), row, 0)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        grid.addWidget(self.model_combo, row, 1)

        self.refresh_models_btn = QPushButton("Refresh Models")
        self.refresh_models_btn.clicked.connect(self.refresh_models)
        grid.addWidget(self.refresh_models_btn, row, 2)

        self.save_model_btn = QPushButton("Save Model")
        self.save_model_btn.clicked.connect(self._save_model)
        grid.addWidget(self.save_model_btn, row, 3)

        layout.addLayout(grid)

        bottom = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self.test_connection)
        bottom.addWidget(self.test_btn)
        bottom.addStretch()
        layout.addLayout(bottom)

    def _toggle_visibility(self):
        if self.key_edit.echoMode() == QLineEdit.Password:
            self.key_edit.setEchoMode(QLineEdit.Normal)
            self.show_btn.setText("Hide")
        else:
            self.key_edit.setEchoMode(QLineEdit.Password)
            self.show_btn.setText("Show")

    def apply_status(self, info: dict):
        available = info.get("available", False)
        self.status_label.setText(theme.status_dot(available))
        model = info.get("default_model")
        if model and not self.model_combo.currentText():
            self.model_combo.addItem(model)
            self.model_combo.setCurrentText(model)

    def refresh_models(self):
        self.refresh_models_btn.setEnabled(False)
        self.client.call(
            lambda: self.client.provider_models(self.name),
            on_success=self._on_models,
            on_error=lambda e: self.refresh_models_btn.setEnabled(True),
        )

    def _on_models(self, data: dict):
        self.refresh_models_btn.setEnabled(True)
        models = data.get("models", [])
        current = self.model_combo.currentText()
        self.model_combo.clear()
        if models:
            self.model_combo.addItems(models)
        if current:
            self.model_combo.setCurrentText(current)

    def _save_key(self):
        key = self.key_edit.text().strip()
        if not key:
            return
        self.save_key_btn.setEnabled(False)
        self.client.call(
            lambda: self.client.set_provider_key(self.name, key),
            on_success=lambda _: self._after_save("API key saved."),
            on_error=lambda e: self._after_save(f"Failed to save key: {e}"),
        )

    def _save_base_url(self):
        url = self.base_url_edit.text().strip()
        if not url:
            return
        if self.name == "ollama":
            call = lambda: self.client.set_ollama_base_url(url)
        else:
            call = lambda: self.client.set_provider_base_url(self.name, url)
        self.client.call(
            call,
            on_success=lambda _: self._after_save("Base URL saved."),
            on_error=lambda e: self._after_save(f"Failed to save URL: {e}"),
        )

    def _save_model(self):
        model = self.model_combo.currentText().strip()
        if not model:
            return
        self.client.call(
            lambda: self.client.set_provider_model(self.name, model),
            on_success=lambda _: self._after_save("Default model saved."),
            on_error=lambda e: self._after_save(f"Failed to save model: {e}"),
        )

    def _after_save(self, message: str):
        if hasattr(self, "save_key_btn"):
            self.save_key_btn.setEnabled(True)
        self.status_label.setText(f'<span style="color:{theme.TEXT_DIM};">{message}</span>')

    def test_connection(self):
        self.test_btn.setEnabled(False)
        self.status_label.setText("testing...")
        self.client.call(
            lambda: self.client.test_provider(self.name),
            on_success=self._on_test_result,
            on_error=lambda e: self._on_test_result({"available": False}),
        )

    def _on_test_result(self, result: dict):
        self.test_btn.setEnabled(True)
        self.status_label.setText(theme.status_dot(result.get("available", False)))


class ProvidersTab(QWidget):
    def __init__(self, client: EmmaClient):
        super().__init__()
        self.client = client

        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Providers & API Keys")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        self.refresh_all_btn = QPushButton("Refresh Status")
        self.refresh_all_btn.clicked.connect(self.refresh_status)
        header.addWidget(self.refresh_all_btn)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(12)

        self.cards: dict[str, ProviderCard] = {}
        for name in ("ollama", "local_generic", "groq", "nvidia_nim"):
            card = ProviderCard(client, name)
            self.cards[name] = card
            inner_layout.addWidget(card)
        inner_layout.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self.refresh_status()

    def refresh_status(self):
        self.client.call(
            lambda: self.client.list_providers(),
            on_success=self._apply_status,
            on_error=lambda e: None,
        )

    def _apply_status(self, statuses: list[dict]):
        for info in statuses:
            card = self.cards.get(info["name"])
            if card:
                card.apply_status(info)
