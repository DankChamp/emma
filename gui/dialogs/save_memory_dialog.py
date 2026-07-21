"""
SaveMemoryDialog - the popup behind the chat tab's "Save to memory" button.

Lets the user pick one or both memory tiers, name the entry, and edit the
value before it's written - Emma never silently decides what "important"
means, the user does.
"""
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
)


class SaveMemoryDialog(QDialog):
    def __init__(self, parent, prefill_value: str, categories: list[str], projects: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Save to Memory")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)

        hint = QLabel(
            "Choose where Emma should remember this. Long-term memory is\n"
            "forever (people, preferences, habits). Project memory is scoped\n"
            "to one project and stays out of everything else's way."
        )
        hint.setProperty("class", "Hint")
        hint.setStyleSheet("color: #8a8fa3; font-size: 12px;")
        layout.addWidget(hint)

        self.long_term_check = QCheckBox("Save to Long-term memory")
        self.project_check = QCheckBox("Save to Project memory")
        layout.addWidget(self.long_term_check)

        form = QFormLayout()

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems(categories or ["person", "preference", "habit", "fact"])
        form.addRow("Category:", self.category_combo)

        layout.addLayout(form)
        layout.addWidget(self.project_check)

        form2 = QFormLayout()
        self.project_combo = QComboBox()
        self.project_combo.setEditable(True)
        self.project_combo.addItems(projects or ["emma"])
        form2.addRow("Project:", self.project_combo)
        layout.addLayout(form2)

        form3 = QFormLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("e.g. coding_partner_name, deadline_hyperclutch_mvp")
        form3.addRow("Key:", self.key_edit)
        layout.addLayout(form3)

        layout.addWidget(QLabel("Value:"))
        self.value_edit = QTextEdit()
        self.value_edit.setPlainText(prefill_value)
        self.value_edit.setMinimumHeight(100)
        layout.addWidget(self.value_edit)

        self.long_term_check.setChecked(True)
        self._toggle_project(False)
        self.project_check.toggled.connect(self._toggle_project)
        self._toggle_long_term(True)
        self.long_term_check.toggled.connect(self._toggle_long_term)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _toggle_project(self, checked: bool):
        self.project_combo.setEnabled(checked)

    def _toggle_long_term(self, checked: bool):
        self.category_combo.setEnabled(checked)

    def result_payload(self) -> dict:
        targets = []
        if self.long_term_check.isChecked():
            targets.append("long_term")
        if self.project_check.isChecked():
            targets.append("project")
        return {
            "targets": targets,
            "category": self.category_combo.currentText().strip() or None,
            "project": self.project_combo.currentText().strip() or None,
            "key": self.key_edit.text().strip(),
            "value": self.value_edit.toPlainText().strip(),
        }
