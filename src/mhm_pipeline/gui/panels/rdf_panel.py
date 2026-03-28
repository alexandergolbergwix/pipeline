"""Stage 4 — RDF serialisation panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.ttl_preview import TtlPreview


class RdfPanel(QWidget):
    """Panel for Stage 4: RDF graph serialisation."""

    run_requested = pyqtSignal(Path, Path, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # file selectors
        self._input_selector = FileSelector(
            "Enriched JSON:", mode="open", filter="JSON files (*.json)"
        )
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        layout.addWidget(self._input_selector)
        layout.addWidget(self._output_selector)

        # serialisation format
        fmt_layout = QHBoxLayout()
        fmt_layout.addWidget(QLabel("Format:"))
        self._format_combo = QComboBox()
        self._format_combo.addItems(["Turtle", "JSON-LD", "N-Triples"])
        fmt_layout.addWidget(self._format_combo)
        fmt_layout.addStretch()
        layout.addLayout(fmt_layout)

        # run button
        self._run_btn = QPushButton("Run Stage 4")
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        # log viewer
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

        # TTL preview
        self._preview = TtlPreview()
        layout.addWidget(self._preview, stretch=1)

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        """Return the embedded log viewer."""
        return self._log_viewer

    @property
    def preview(self) -> TtlPreview:
        """Return the TTL preview widget."""
        return self._preview

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        input_path = self._input_selector.path
        output_path = self._output_selector.path
        if input_path is None:
            self._log_viewer.append_line("Error: select an enriched JSON file first.")
            return
        if output_path is None:
            output_path = input_path.parent
            self._output_selector.path = output_path
        self.run_requested.emit(
            input_path, output_path, self._format_combo.currentText()
        )
