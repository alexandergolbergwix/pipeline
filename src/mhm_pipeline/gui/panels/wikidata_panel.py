"""Stage 6 — Wikidata upload panel.

Features:
- Input: authority_enriched.json (Stage 2 output), NOT TTL
- Dry run: exports QuickStatements V2 format
- Live upload: via WikibaseIntegrator with bot password
- Per-entity progress tracking
- Batch mode with configurable pauses
- Load and review results
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.percent_progress import PercentProgressWidget
from mhm_pipeline.gui.widgets.upload_progress_view import UploadProgressView


class WikidataPanel(QWidget):
    """Panel for Stage 6: Wikidata upload with dry-run and live modes."""

    # (input_path, output_dir, token, dry_run, batch_mode)
    run_requested = pyqtSignal(Path, Path, str, bool, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Outer layout with scroll area so nothing is ever cut off
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)

        # Input file selector (authority_enriched.json)
        self._input_selector = FileSelector(
            "Enriched JSON:", mode="open", filter="JSON files (*.json)",
        )
        layout.addWidget(self._input_selector)

        # Output directory
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        layout.addWidget(self._output_selector)

        # Configuration row
        config_layout = QHBoxLayout()

        self._dry_run_cb = QCheckBox("Dry run (QuickStatements export)")
        self._dry_run_cb.setChecked(True)
        self._dry_run_cb.setToolTip(
            "When checked, exports QuickStatements V2 format instead of uploading."
        )
        config_layout.addWidget(self._dry_run_cb)

        self._batch_cb = QCheckBox("Batch mode (45 items + 30s pause)")
        self._batch_cb.setChecked(True)
        self._batch_cb.setToolTip("Pause every 45 items to avoid Wikidata rate limiting (recommended)")
        config_layout.addWidget(self._batch_cb)

        config_layout.addStretch()

        self._configure_btn = QPushButton("Configure...")
        self._configure_btn.clicked.connect(self._on_configure)
        config_layout.addWidget(self._configure_btn)

        layout.addLayout(config_layout)

        # Token (hidden by default, shown when not dry run)
        self._token_row = QWidget()
        token_layout = QHBoxLayout(self._token_row)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.addWidget(QLabel("Bot Password:"))
        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_edit.setPlaceholderText("consumer_key|consumer_secret (OAuth 2.0)")
        token_layout.addWidget(self._token_edit)
        layout.addWidget(self._token_row)
        self._token_row.setVisible(False)
        self._dry_run_cb.toggled.connect(lambda checked: self._token_row.setVisible(not checked))

        # Warning
        warning = QLabel("Note: Live upload requires bot approval for >50 items")
        warning.setStyleSheet("color: #b45309; font-style: italic; font-size: 11px;")
        layout.addWidget(warning)

        # Buttons
        btn_layout = QHBoxLayout()
        self._run_btn = QPushButton("Upload to Wikidata")
        self._run_btn.clicked.connect(self._on_run)
        btn_layout.addWidget(self._run_btn)

        self._load_btn = QPushButton("Load Results")
        self._load_btn.clicked.connect(self._on_load_results)
        btn_layout.addWidget(self._load_btn)

        self._fullscreen_btn = QPushButton("Open in Full Window")
        self._fullscreen_btn.clicked.connect(self._on_fullscreen)
        btn_layout.addWidget(self._fullscreen_btn)

        layout.addLayout(btn_layout)

        # Progress bar
        self._progress = PercentProgressWidget()
        layout.addWidget(self._progress)

        # Stats preview (compact)
        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(
            "background-color: #f0f9ff; border: 1px solid #bae6fd; "
            "border-radius: 6px; padding: 4px 8px; font-size: 11px;"
        )
        self._stats_label.setWordWrap(True)
        self._stats_label.setMaximumHeight(60)
        self._stats_label.hide()
        layout.addWidget(self._stats_label)

        # Upload progress view — gets most of the space
        self._upload_view = UploadProgressView()
        layout.addWidget(self._upload_view, stretch=4)

        # log viewer — compact
        self._log_viewer = LogViewer()
        self._log_viewer.setMaximumHeight(100)
        layout.addWidget(self._log_viewer)

        # Close scroll area
        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        return self._log_viewer

    @property
    def upload_view(self) -> UploadProgressView:
        return self._upload_view

    @property
    def stage_progress(self) -> PercentProgressWidget:
        return self._progress

    def set_total_items(self, total: int) -> None:
        """Set the total number of items to upload (for overall progress bar)."""
        self._total_items = total
        self._completed_items = 0
        self._upload_view.update_overall_progress(0, total)

    def update_entity_status(
        self, local_id: str, status: str, qid: str, message: str,
    ) -> None:
        """Update per-entity progress from the upload worker."""
        try:
            from mhm_pipeline.gui.widgets.upload_progress_view import WikidataEntity  # noqa: PLC0415

            # Handle special "__total__" signal to set overall progress bar
            if local_id == "__total__" and status == "total":
                self.set_total_items(int(qid))
                return

            widget = self._upload_view.get_entity_widget(local_id)
            if widget is None:
                entity = WikidataEntity(
                    local_id=local_id,
                    label=local_id[:40],
                    entity_type="item",
                )
                widget = self._upload_view.add_entity(entity)

            widget.set_status(status=status, qid=qid, message=message)

            # Update overall progress for non-"uploading" statuses (actual completions)
            if status in ("success", "exists", "failed", "skipped"):
                self._completed_items = getattr(self, "_completed_items", 0) + 1
                total = getattr(self, "_total_items", self._completed_items)
                self._upload_view.update_overall_progress(self._completed_items, total)
        except Exception as e:
            import logging  # noqa: PLC0415
            logging.getLogger(__name__).warning("Entity status update error: %s", e)

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_configure(self) -> None:
        """Open configuration dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Upload Configuration")
        dialog.setMinimumWidth(400)
        form = QFormLayout(dialog)

        dry_run = QCheckBox("Dry run (QuickStatements export)")
        dry_run.setChecked(self._dry_run_cb.isChecked())
        form.addRow(dry_run)

        batch = QCheckBox("Batch mode (45 items + 60s pause)")
        batch.setChecked(self._batch_cb.isChecked())
        form.addRow(batch)

        token = QLineEdit(self._token_edit.text())
        token.setEchoMode(QLineEdit.EchoMode.Password)
        token.setPlaceholderText("consumer_key|consumer_secret")
        form.addRow("Auth Token:", token)

        auth_help = QLabel(
            "<small>Formats: <b>OAuth 2.0</b>: consumer_key|consumer_secret<br>"
            "<b>Bot password</b>: Username@BotName:password</small>"
        )
        auth_help.setStyleSheet("color: #6b7280;")
        form.addRow(auth_help)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        form.addRow(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._dry_run_cb.setChecked(dry_run.isChecked())
            self._batch_cb.setChecked(batch.isChecked())
            self._token_edit.setText(token.text())

    def _on_run(self) -> None:
        input_path = self._input_selector.path
        output_path = self._output_selector.path

        if input_path is None:
            self._log_viewer.append_line("Error: select authority_enriched.json first.")
            return
        if output_path is None:
            output_path = input_path.parent
            self._output_selector.path = output_path

        dry_run = self._dry_run_cb.isChecked()
        if not dry_run and not self._token_edit.text().strip():
            QMessageBox.warning(
                self, "Missing Token",
                "Bot password is required for live upload. "
                "Use dry run for QuickStatements export.",
            )
            return

        self.run_requested.emit(
            input_path, output_path,
            self._token_edit.text().strip(),
            dry_run, self._batch_cb.isChecked(),
        )

    def _on_load_results(self) -> None:
        """Load and display previously generated upload results."""
        from PyQt6.QtWidgets import QFileDialog  # noqa: PLC0415

        path_str, _ = QFileDialog.getOpenFileName(
            self, "Load Upload Results", "",
            "JSON files (*.json);;QuickStatements (*.txt);;All (*)",
        )
        if not path_str:
            return

        path = Path(path_str)
        try:
            if path.suffix == ".txt":
                # QuickStatements file
                text = path.read_text(encoding="utf-8")
                lines = text.strip().split("\n")
                self._stats_label.setText(
                    f"<b>QuickStatements Export</b><br>"
                    f"{len(lines)} statements from {path.name}"
                )
                self._stats_label.show()
                self._log_viewer.append_line(f"Loaded {len(lines)} QuickStatements from {path}")
            else:
                # JSON results (upload_results.json or wikidata_items.json)
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, list) and data and "status" in data[0]:
                    # upload_results.json
                    success = sum(1 for r in data if r.get("status") in ("success", "exists"))
                    failed = sum(1 for r in data if r.get("status") == "failed")
                    self._stats_label.setText(
                        f"<b>Upload Results</b><br>"
                        f"{len(data)} items: {success} succeeded, {failed} failed"
                    )
                    for r in data:
                        self._upload_view.update_entity(
                            r.get("local_id", ""),
                            r.get("status", ""),
                            r.get("qid", ""),
                            r.get("message", ""),
                        )
                elif isinstance(data, list) and data and "entity_type" in data[0]:
                    # wikidata_items.json (dry run summary)
                    ms_count = sum(1 for d in data if d.get("entity_type") == "manuscript")
                    person_count = sum(1 for d in data if d.get("entity_type") == "person")
                    total_stmts = sum(d.get("statements_count", 0) for d in data)
                    reconciled = sum(1 for d in data if d.get("existing_qid"))
                    self._stats_label.setText(
                        f"<b>Wikidata Items Preview</b><br>"
                        f"{len(data)} items: {ms_count} manuscripts, {person_count} persons<br>"
                        f"{total_stmts} total statements, {reconciled} already on Wikidata"
                    )
                else:
                    self._stats_label.setText(f"Loaded {len(data)} items from {path.name}")

                self._stats_label.show()
                self._log_viewer.append_line(f"Loaded results from {path}")

        except Exception as e:
            self._log_viewer.append_line(f"Error loading results: {e}")
            QMessageBox.critical(self, "Load Error", str(e))

    def _on_fullscreen(self) -> None:
        """Open upload progress in a full-screen dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Wikidata Upload Progress")
        screen = self.screen()
        if screen:
            geom = screen.availableGeometry()
            dialog.resize(geom.width() * 9 // 10, geom.height() * 9 // 10)
        else:
            dialog.resize(1200, 800)
        dlg_layout = QVBoxLayout(dialog)
        full_view = UploadProgressView()
        dlg_layout.addWidget(full_view, stretch=1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        dlg_layout.addWidget(close_btn)
        dialog.exec()
