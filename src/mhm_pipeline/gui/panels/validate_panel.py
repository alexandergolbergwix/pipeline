"""Stage 5 — SHACL validation panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.percent_progress import PercentProgressWidget
from mhm_pipeline.gui.widgets.validation_result_view import ValidationResultView


class ValidatePanel(QWidget):
    """Panel for Stage 5: SHACL shape validation."""

    run_requested = pyqtSignal(Path, Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # file selectors
        self._ttl_selector = FileSelector("TTL File:", mode="open", filter="Turtle files (*.ttl)")
        self._shapes_selector = FileSelector(
            "SHACL Shapes:", mode="open", filter="Turtle files (*.ttl)"
        )
        layout.addWidget(self._ttl_selector)
        layout.addWidget(self._shapes_selector)

        # severity filter
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Severity:"))
        self._severity_combo = QComboBox()
        self._severity_combo.addItems(["All", "Violation", "Warning"])
        filter_layout.addWidget(self._severity_combo)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        # buttons
        btn_layout = QHBoxLayout()
        self._run_btn = QPushButton("Validate SHACL")
        self._run_btn.clicked.connect(self._on_run)
        btn_layout.addWidget(self._run_btn)

        self._fullscreen_btn = QPushButton("Open in Full Window")
        self._fullscreen_btn.clicked.connect(self._on_fullscreen)
        self._fullscreen_btn.setEnabled(False)
        btn_layout.addWidget(self._fullscreen_btn)
        layout.addLayout(btn_layout)

        # Progress bar
        self._progress = PercentProgressWidget()
        layout.addWidget(self._progress)

        # Validation result view
        self._validation_view = ValidationResultView()
        layout.addWidget(self._validation_view, stretch=2)

        # violations table (legacy, keep for compatibility)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Severity", "Focus Node", "Message"])
        header = self._table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, stretch=1)

        # log viewer
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        """Return the embedded log viewer."""
        return self._log_viewer

    @property
    def stage_progress(self) -> PercentProgressWidget:
        """Return the embedded progress widget."""
        return self._progress

    # ── Public API ────────────────────────────────────────────────────

    def populate_report(self, report_text: str) -> None:
        """Parse a SHACL text report and populate the violations table.

        Expects lines of the form:
            Severity: <severity>
            Focus Node: <node>
            Message: <message>
        separated by blank lines.
        """
        self._table.setRowCount(0)

        severity_filter = self._severity_combo.currentText()
        entries: list[dict[str, str]] = []
        current: dict[str, str] = {}

        for line in report_text.splitlines():
            line = line.strip()
            if not line:
                if current:
                    entries.append(current)
                    current = {}
                continue
            if line.startswith("Severity:"):
                current["severity"] = line.split(":", 1)[1].strip()
            elif line.startswith("Focus Node:"):
                current["focus"] = line.split(":", 1)[1].strip()
            elif line.startswith("Message:"):
                current["message"] = line.split(":", 1)[1].strip()

        if current:
            entries.append(current)

        for entry in entries:
            sev = entry.get("severity", "")
            if severity_filter != "All" and severity_filter.lower() not in sev.lower():
                continue
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(sev))
            self._table.setItem(row, 1, QTableWidgetItem(entry.get("focus", "")))
            self._table.setItem(row, 2, QTableWidgetItem(entry.get("message", "")))

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        ttl_path = self._ttl_selector.path
        shapes_path = self._shapes_selector.path
        if ttl_path is None or shapes_path is None:
            return
        self.run_requested.emit(ttl_path, shapes_path)

    def load_validation_results(self, result: object) -> None:
        """Load validation results into the view."""
        self._validation_view.load_results(result)  # type: ignore[arg-type]
        self._fullscreen_btn.setEnabled(True)

    def _on_fullscreen(self) -> None:
        """Open validation results in a full-screen dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("SHACL Validation Results")
        screen = self.screen()
        if screen:
            geom = screen.availableGeometry()
            dialog.resize(geom.width() * 9 // 10, geom.height() * 9 // 10)
        else:
            dialog.resize(1200, 800)
        dlg_layout = QVBoxLayout(dialog)
        full_view = ValidationResultView()
        dlg_layout.addWidget(full_view, stretch=1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        dlg_layout.addWidget(close_btn)
        dialog.exec()
