"""
"Void" theme - dark, minimal, terminal-flavoured, a nod to the Void Linux
box this is meant to run on. Near-black background, a single violet
accent for anything interactive, a cold teal for status/success, and a
warm amber reserved for warnings/errors. One QSS string, applied once to
the whole QApplication so every widget picks it up consistently.
"""

BG = "#0b0c10"
BG_ELEVATED = "#131620"
BG_INPUT = "#1a1e2b"
BORDER = "#262b3d"
TEXT = "#e6e8f0"
TEXT_DIM = "#8a8fa3"
ACCENT = "#8b5cf6"       # violet - primary actions, focus rings
ACCENT_HOVER = "#a78bfa"
ACCENT_DIM = "#4c3a80"
OK = "#2dd4bf"           # teal - "online" / success
WARN = "#f59e0b"         # amber - warnings
ERR = "#f43f5e"          # rose - errors / offline

FONT_UI = "Inter, 'Segoe UI', 'Ubuntu', sans-serif"
FONT_MONO = "'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace"

VOID_QSS = f"""
* {{
    font-family: {FONT_UI};
    color: {TEXT};
    outline: none;
}}

QMainWindow, QWidget {{
    background-color: {BG};
}}

QWidget#Sidebar {{
    background-color: {BG_ELEVATED};
    border-right: 1px solid {BORDER};
}}

QLabel#Brand {{
    font-family: {FONT_MONO};
    font-size: 20px;
    font-weight: 600;
    color: {TEXT};
    padding: 18px 16px 4px 16px;
}}

QLabel#BrandSub {{
    font-family: {FONT_MONO};
    font-size: 11px;
    color: {ACCENT};
    padding: 0px 16px 16px 16px;
    letter-spacing: 1px;
}}

QLabel#SectionTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {TEXT_DIM};
    letter-spacing: 1px;
    text-transform: uppercase;
    padding: 4px 2px;
}}

QLabel.Hint {{
    color: {TEXT_DIM};
    font-size: 12px;
}}

QListWidget#NavList {{
    background-color: transparent;
    border: none;
    padding: 8px;
    font-size: 14px;
}}

QListWidget#NavList::item {{
    padding: 10px 12px;
    border-radius: 8px;
    margin-bottom: 2px;
    color: {TEXT_DIM};
}}

QListWidget#NavList::item:selected {{
    background-color: {ACCENT_DIM};
    color: {TEXT};
}}

QListWidget#NavList::item:hover:!selected {{
    background-color: {BG_INPUT};
    color: {TEXT};
}}

QStackedWidget {{
    background-color: {BG};
}}

QFrame.Card {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateTimeEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {ACCENT_DIM};
    font-size: 13px;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DIM};
    outline: none;
}}

QPushButton {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
}}

QPushButton:hover {{
    border: 1px solid {ACCENT};
    color: {ACCENT_HOVER};
}}

QPushButton:pressed {{
    background-color: {ACCENT_DIM};
}}

QPushButton:disabled {{
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
}}

QPushButton.Primary {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    color: #ffffff;
}}

QPushButton.Primary:hover {{
    background-color: {ACCENT_HOVER};
    border: 1px solid {ACCENT_HOVER};
    color: #ffffff;
}}

QPushButton.Danger {{
    border: 1px solid {ERR};
    color: {ERR};
}}

QPushButton.Danger:hover {{
    background-color: {ERR};
    color: #ffffff;
}}

QTextBrowser#ChatLog {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 10px;
    font-size: 14px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: {ACCENT_DIM};
}}

QScrollBar:horizontal {{
    height: 0px;
}}

QTableWidget {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: {BORDER};
    font-size: 13px;
}}

QHeaderView::section {{
    background-color: {BG_INPUT};
    color: {TEXT_DIM};
    padding: 8px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}

QTableWidget::item {{
    padding: 4px;
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    top: -1px;
}}

QTabBar::tab {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    padding: 8px 14px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    color: {TEXT_DIM};
}}

QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}

QCheckBox, QRadioButton {{
    font-size: 13px;
    spacing: 8px;
}}

QStatusBar {{
    background-color: {BG_ELEVATED};
    border-top: 1px solid {BORDER};
    color: {TEXT_DIM};
    font-family: {FONT_MONO};
    font-size: 11px;
}}

QSplitter::handle {{
    background: {BORDER};
}}

QToolTip {{
    background-color: {BG_ELEVATED};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
}}
"""


def status_dot(available: bool) -> str:
    """Small colored-dot glyph for inline provider status."""
    color = OK if available else ERR
    label = "online" if available else "offline"
    return f'<span style="color:{color};">\u25cf</span> {label}'
