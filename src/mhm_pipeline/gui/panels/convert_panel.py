"""Stage 1 — MARC conversion panel."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.marc_field_visualizer import MarcFieldVisualizer
from mhm_pipeline.gui.widgets.stage_progress import StageProgressWidget

if TYPE_CHECKING:
    from converter.transformer.field_handlers import ExtractedData


class ConvertPanel(QWidget):
    """Panel for Stage 1: parsing MARC/TSV/CSV records into JSON."""

    run_requested = pyqtSignal(Path, Path, int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # input / output selectors
        self._input_selector = FileSelector(
            "MARC Input:", mode="open", filter="MARC / TSV files (*.mrc *.csv *.tsv)"
        )
        self._output_selector = FileSelector("Output Dir:", mode="directory")

        layout.addWidget(self._input_selector)
        layout.addWidget(self._output_selector)

        # record range
        range_group = QGroupBox("Record Range")
        range_layout = QHBoxLayout(range_group)

        range_layout.addWidget(QLabel("Start:"))
        self._start_spin = QSpinBox()
        self._start_spin.setRange(0, 999_999_999)
        self._start_spin.setSpecialValueText("Beginning")
        range_layout.addWidget(self._start_spin)

        range_layout.addWidget(QLabel("End:"))
        self._end_spin = QSpinBox()
        self._end_spin.setRange(0, 999_999_999)
        self._end_spin.setSpecialValueText("All")
        range_layout.addWidget(self._end_spin)

        range_layout.addStretch()
        layout.addWidget(range_group)

        # buttons row
        btn_layout = QHBoxLayout()
        self._run_btn = QPushButton("Parse MARC Records")
        self._run_btn.clicked.connect(self._on_run)
        btn_layout.addWidget(self._run_btn)

        self._fullscreen_btn = QPushButton("Open in Full Window")
        self._fullscreen_btn.clicked.connect(self._on_fullscreen)
        self._fullscreen_btn.setEnabled(False)
        btn_layout.addWidget(self._fullscreen_btn)
        layout.addLayout(btn_layout)

        # progress
        self._progress = StageProgressWidget()
        layout.addWidget(self._progress)

        # MARC field visualizer
        self._field_visualizer = MarcFieldVisualizer()
        layout.addWidget(self._field_visualizer, stretch=2)

        # log viewer
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        """Return the embedded log viewer."""
        return self._log_viewer

    @property
    def stage_progress(self) -> StageProgressWidget:
        """Return the embedded stage progress widget."""
        return self._progress

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        input_path = self._input_selector.path
        output_path = self._output_selector.path
        if input_path is None or output_path is None:
            return
        self.run_requested.emit(
            input_path,
            output_path,
            self._start_spin.value(),
            self._end_spin.value(),
        )

    def display_extracted_data(self, data: ExtractedData) -> None:
        """Display extracted MARC data in the visualizer."""
        self._field_visualizer.load_from_extracted_data(data)
        self._fullscreen_btn.setEnabled(True)

    def _on_fullscreen(self) -> None:
        """Open MARC field visualizer in a full-screen dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("MARC Field Visualizer")
        screen = self.screen()
        if screen:
            geom = screen.availableGeometry()
            dialog.resize(geom.width() * 9 // 10, geom.height() * 9 // 10)
        else:
            dialog.resize(1200, 800)
        dlg_layout = QVBoxLayout(dialog)
        full_viz = MarcFieldVisualizer()
        # Copy data from the panel's visualizer if possible
        dlg_layout.addWidget(full_viz, stretch=1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        dlg_layout.addWidget(close_btn)
        dialog.exec()
