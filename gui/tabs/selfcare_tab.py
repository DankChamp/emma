"""
SelfCareTab - GUI for diagnostics, auto-repair, updates, changelog, and system health checks.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.api_client import EmmaClient
from gui import theme


class SelfCareTab(QWidget):
    def __init__(self, client: EmmaClient):
        super().__init__()
        self.client = client

        outer = QVBoxLayout(self)
        outer.setSpacing(12)

        # Title
        title = QLabel("Self-Care & Diagnostics")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        outer.addWidget(title)

        # Version & Info Card
        self.info_card = QFrame()
        self.info_card.setProperty("class", "Card")
        self.info_card.setStyleSheet("QFrame.Card {}")
        info_layout = QHBoxLayout(self.info_card)
        
        self.version_label = QLabel("Loading version info...")
        self.version_label.setStyleSheet("font-family: " + theme.FONT_MONO + "; font-size: 13px;")
        info_layout.addWidget(self.version_label)
        
        self.refresh_version_btn = QPushButton("Refresh Info")
        self.refresh_version_btn.setFixedWidth(120)
        self.refresh_version_btn.clicked.connect(self.load_version_info)
        info_layout.addWidget(self.refresh_version_btn)
        
        outer.addWidget(self.info_card)

        # Actions Row
        actions_row = QHBoxLayout()
        
        self.diag_btn = QPushButton("Run Diagnostics")
        self.diag_btn.setStyleSheet(f"border: 1px solid {theme.ACCENT}; color: {theme.ACCENT_HOVER};")
        self.diag_btn.clicked.connect(self.run_diagnostics)
        actions_row.addWidget(self.diag_btn)

        self.repair_btn = QPushButton("Auto-Repair")
        self.repair_btn.setStyleSheet(f"border: 1px solid {theme.OK}; color: {theme.OK};")
        self.repair_btn.clicked.connect(self.run_repair)
        actions_row.addWidget(self.repair_btn)

        self.check_update_btn = QPushButton("Check for Updates")
        self.check_update_btn.clicked.connect(self.check_updates)
        actions_row.addWidget(self.check_update_btn)

        self.apply_update_btn = QPushButton("Apply Updates")
        self.apply_update_btn.setEnabled(False)
        self.apply_update_btn.clicked.connect(self.apply_updates)
        actions_row.addWidget(self.apply_update_btn)

        self.deps_btn = QPushButton("Update Dependencies")
        self.deps_btn.clicked.connect(self.update_dependencies)
        actions_row.addWidget(self.deps_btn)

        outer.addLayout(actions_row)

        # Diagnostics Output Area
        output_label = QLabel("Diagnostics & Execution Log:")
        output_label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px; font-weight: 600;")
        outer.addWidget(output_label)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet(f"font-family: {theme.FONT_MONO}; font-size: 12px; background-color: {theme.BG_ELEVATED}; border: 1px solid {theme.BORDER}; border-radius: 8px; padding: 8px;")
        outer.addWidget(self.log_output, stretch=2)

        # Changelog Area
        changelog_label = QLabel("Recent Changelog (last 10 updates):")
        changelog_label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px; font-weight: 600;")
        outer.addWidget(changelog_label)

        self.changelog_table = QTableWidget(0, 4)
        self.changelog_table.setHorizontalHeaderLabels(["Hash", "Date", "Author", "Commit Message"])
        self.changelog_table.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.changelog_table, stretch=1)

        self.load_version_info()
        self.load_changelog()

    def load_version_info(self):
        self.version_label.setText("Checking repository version...")
        self.client.call(
            lambda: self.client.get_version_info(),
            on_success=self._on_version_success,
            on_error=lambda e: self.version_label.setText(f"Error fetching version: {e}"),
        )

    def _on_version_success(self, info: dict):
        branch = info.get("branch", "unknown")
        commit = info.get("commit", "unknown")
        date_str = info.get("date", "unknown")
        self.version_label.setText(f"Active Branch: {branch}  //  Commit: {commit}  //  Released: {date_str}")

    def run_diagnostics(self):
        self.log_output.setPlainText("Running comprehensive system diagnostics...")
        self.client.call(
            lambda: self.client.run_diagnostics(),
            on_success=self._on_diagnostics_success,
            on_error=lambda e: self.log_output.appendPlainText(f"\nDiagnostics execution failed: {e}"),
        )

    def _on_diagnostics_success(self, report: dict):
        import json
        self.log_output.clear()
        self.log_output.appendPlainText("=== DIAGNOSTICS REPORT ===")
        
        self.log_output.appendPlainText("\n[DATABASES]")
        for db, status in report.get("databases", {}).items():
            symbol = "✔" if status == "ok" else "❌"
            self.log_output.appendPlainText(f"  {symbol} {db}: {status}")
            
        self.log_output.appendPlainText("\n[CONFIGURATION]")
        for key, status in report.get("config", {}).items():
            symbol = "✔" if status in ["present", "configured"] else "❌"
            self.log_output.appendPlainText(f"  {symbol} {key}: {status}")

        self.log_output.appendPlainText("\n[AI PROVIDERS STATUS]")
        for prov in report.get("providers", []):
            symbol = "✔" if prov.get("available") else "❌"
            config_status = "Configured" if prov.get("configured") else "Not configured"
            avail_status = "Available" if prov.get("available") else "Offline"
            self.log_output.appendPlainText(f"  {symbol} {prov.get('name')}: {config_status} ({avail_status}, model: {prov.get('default_model')})")

        self.log_output.appendPlainText("\n[DEPENDENCIES]")
        missing_count = 0
        for dep in report.get("dependencies", []):
            symbol = "✔" if dep.get("installed") else "❌"
            ver_info = f" (version: {dep.get('version')})" if dep.get("installed") else " (NOT INSTALLED)"
            if not dep.get("installed"):
                missing_count += 1
            self.log_output.appendPlainText(f"  {symbol} {dep.get('name')}{ver_info}")

        self.log_output.appendPlainText("\n[SYSTEM DISK]")
        disk = report.get("disk", {})
        total_gb = disk.get("total", 0) / (1024**3)
        free_gb = disk.get("free", 0) / (1024**3)
        self.log_output.appendPlainText(f"  Total Space: {total_gb:.2f} GB")
        self.log_output.appendPlainText(f"  Free Space:  {free_gb:.2f} GB")

        self.log_output.appendPlainText("\n=== END REPORT ===")
        if missing_count > 0:
            self.log_output.appendPlainText(f"\nWARNING: {missing_count} required python dependencies are missing. Run Update Dependencies.")

    def run_repair(self):
        self.log_output.setPlainText("Initiating system auto-repair sequence...")
        self.client.call(
            lambda: self.client.auto_repair(),
            on_success=self._on_repair_success,
            on_error=lambda e: self.log_output.appendPlainText(f"\nAuto-repair execution failed: {e}"),
        )

    def _on_repair_success(self, report: dict):
        self.log_output.clear()
        self.log_output.appendPlainText("=== AUTO-REPAIR RESULTS ===")
        
        self.log_output.appendPlainText("\n[CONFIG REPAIRS]")
        for key, result in report.get("config", {}).items():
            self.log_output.appendPlainText(f"  {key}: {result}")
            
        self.log_output.appendPlainText("\n[DATABASE REPAIRS]")
        for db, result in report.get("databases", {}).items():
            self.log_output.appendPlainText(f"  {db}: {result}")

        self.log_output.appendPlainText("\n===========================\n")
        self.log_output.appendPlainText("Re-running diagnostics to verify status...\n")
        self._on_diagnostics_success(report.get("final_status", {}))

    def check_updates(self):
        self.log_output.setPlainText("Checking git repository for updates...")
        self.client.call(
            lambda: self.client.check_updates(),
            on_success=self._on_check_updates_success,
            on_error=lambda e: self.log_output.appendPlainText(f"\nUpdate check failed: {e}"),
        )

    def _on_check_updates_success(self, info: dict):
        if info.get("error"):
            self.log_output.appendPlainText(f"\nGit status error: {info['error']}")
            return
        
        has_updates = info.get("has_updates", False)
        behind_by = info.get("behind_by", 0)
        
        self.log_output.appendPlainText(f"\nLocal Commit: {info.get('current_commit')}")
        if has_updates:
            self.log_output.appendPlainText(f"Status: Behind upstream by {behind_by} commit(s).")
            self.log_output.appendPlainText(f"Latest Upstream Message: \"{info.get('latest_message')}\"")
            self.log_output.appendPlainText("\nUpdates are available! Click 'Apply Updates' to pull them.")
            self.apply_update_btn.setEnabled(True)
        else:
            self.log_output.appendPlainText("Status: Up to date with upstream repository.")
            self.apply_update_btn.setEnabled(False)

    def apply_updates(self):
        self.log_output.appendPlainText("\nApplying updates from repository (running git pull)...")
        self.apply_update_btn.setEnabled(False)
        self.client.call(
            lambda: self.client.apply_updates(),
            on_success=self._on_apply_updates_success,
            on_error=lambda e: self.log_output.appendPlainText(f"\nUpdate failed: {e}"),
        )

    def _on_apply_updates_success(self, info: dict):
        if info.get("success"):
            self.log_output.appendPlainText(f"\n{info.get('message')}")
            self.log_output.appendPlainText(f"New Local Commit: {info.get('new_commit')}")
            if info.get("changes_summary"):
                self.log_output.appendPlainText(f"\nChanges Summary:\n{info.get('changes_summary')}")
            self.load_version_info()
            self.load_changelog()
        else:
            self.log_output.appendPlainText(f"\nFailed to apply updates: {info.get('message')}")
            self.apply_update_btn.setEnabled(True)

    def update_dependencies(self):
        self.log_output.appendPlainText("\nUpdating dependencies in virtual environment (pip install)...")
        self.deps_btn.setEnabled(False)
        self.client.call(
            lambda: self.client.update_dependencies(),
            on_success=self._on_deps_success,
            on_error=lambda e: (self.log_output.appendPlainText(f"\nDependency update failed: {e}"), self.deps_btn.setEnabled(True)),
        )

    def _on_deps_success(self, info: dict):
        self.deps_btn.setEnabled(True)
        if info.get("success"):
            self.log_output.appendPlainText(f"\n{info.get('message')}")
            if info.get("output"):
                self.log_output.appendPlainText(f"\nPip Output:\n{info.get('output')}")
        else:
            self.log_output.appendPlainText(f"\nDependency update failed: {info.get('message')}")

    def load_changelog(self):
        self.client.call(
            lambda: self.client.get_changelog(),
            on_success=self._on_changelog_success,
            on_error=lambda _: None,
        )

    def _on_changelog_success(self, commits: list):
        self.changelog_table.setRowCount(0)
        for row, commit in enumerate(commits):
            self.changelog_table.insertRow(row)
            self.changelog_table.setItem(row, 0, QTableWidgetItem(commit.get("hash", "")))
            self.changelog_table.setItem(row, 1, QTableWidgetItem(commit.get("date", "")))
            self.changelog_table.setItem(row, 2, QTableWidgetItem(commit.get("author", "")))
            self.changelog_table.setItem(row, 3, QTableWidgetItem(commit.get("message", "")))
