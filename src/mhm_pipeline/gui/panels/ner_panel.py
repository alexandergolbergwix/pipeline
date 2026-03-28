"""Stage 2 — Named Entity Recognition panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer

_DEFAULT_MODEL = "alexgoldberg/hebrew-manuscript-joint-ner-v2"


class NerPanel(QWidget):
    """Panel for Stage 2: NER extraction from parsed JSON."""

    # (input_path, output_dir, model_path, batch_size)
    run_requested = pyqtSignal(Path, Path, str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # file selectors
        self._input_selector = FileSelector(
            "Input JSON:", mode="open", filter="JSON files (*.json)"
        )
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        layout.addWidget(self._input_selector)
        layout.addWidget(self._output_selector)

        # model identifier (HuggingFace repo ID or local path)
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Model:"))
        self._model_edit = QLineEdit(_DEFAULT_MODEL)
        self._model_edit.setToolTip(
            "HuggingFace model ID (e.g. alexgoldberg/hebrew-manuscript-joint-ner-v2) "
            "or absolute path to a local model directory."
        )
        model_layout.addWidget(self._model_edit)
        layout.addLayout(model_layout)

        # batch size
        batch_layout = QHBoxLayout()
        batch_layout.addWidget(QLabel("Batch size:"))
        self._batch_spin = QSpinBox()
        self._batch_spin.setRange(1, 512)
        self._batch_spin.setValue(32)
        batch_layout.addWidget(self._batch_spin)
        batch_layout.addStretch()
        layout.addLayout(batch_layout)

        # run button
        self._run_btn = QPushButton("Run Stage 2")
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        # log viewer
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        """Return the embedded log viewer."""
        return self._log_viewer

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        input_path = self._input_selector.path
        output_path = self._output_selector.path
        model_path = self._model_edit.text().strip()

        if input_path is None:
            self._log_viewer.append_line("Error: select an input JSON file first.")
            return
        if not model_path:
            self._log_viewer.append_line("Error: enter a model ID or local path.")
            return
        if output_path is None:
            output_path = input_path.parent
            self._output_selector.path = output_path

        self.run_requested.emit(input_path, output_path, model_path, self._batch_spin.value())
