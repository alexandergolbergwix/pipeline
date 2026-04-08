"""Stage 6 — Wikidata upload panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.percent_progress import PercentProgressWidget
from mhm_pipeline.gui.widgets.upload_progress_view import UploadProgressView


class WikidataPanel(QWidget):
    """Panel for Stage 6: Wikidata upload."""

    run_requested = pyqtSignal(Path, str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # TTL file selector
        self._ttl_selector = FileSelector(
            "TTL File:", mode="open", filter="Turtle files (*.ttl)"
        )
        layout.addWidget(self._ttl_selector)

        # OAuth token
        token_layout = QHBoxLayout()
        token_layout.addWidget(QLabel("OAuth Token:"))
        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_edit.setPlaceholderText("Enter Wikidata OAuth token")
        token_layout.addWidget(self._token_edit)
        layout.addLayout(token_layout)

        # dry run
        self._dry_run_cb = QCheckBox("Dry run")
        self._dry_run_cb.setChecked(True)
        layout.addWidget(self._dry_run_cb)

        # warning
        warning = QLabel("Note: Wikidata upload requires bot approval for >50 items")
        warning.setStyleSheet("color: #996600; font-style: italic;")
        layout.addWidget(warning)

        # run button
        self._run_btn = QPushButton("Upload to Wikidata")
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        # Progress bar
        self._progress = PercentProgressWidget()
        layout.addWidget(self._progress)

        # Upload progress view
        self._upload_view = UploadProgressView()
        layout.addWidget(self._upload_view, stretch=2)

        # log viewer
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        """Return the embedded log viewer."""
        return self._log_viewer

    @property
    def upload_view(self) -> UploadProgressView:
        """Return the upload progress view."""
        return self._upload_view

    @property
    def stage_progress(self) -> PercentProgressWidget:
        """Return the embedded progress widget."""
        return self._progress

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        ttl_path = self._ttl_selector.path
        if ttl_path is None:
            return
        self.run_requested.emit(
            ttl_path,
            self._token_edit.text(),
            self._dry_run_cb.isChecked(),
        )
