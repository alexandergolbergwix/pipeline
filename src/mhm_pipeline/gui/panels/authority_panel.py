"""Stage 3 — Authority reconciliation panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.widgets.authority_matcher_view import (
    AuthorityMatch,
    AuthorityMatcherView,
)
from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.percent_progress import PercentProgressWidget


class AuthorityPanel(QWidget):
    """Panel for Stage 3: authority record reconciliation.

    Signal args: (input_path, output_dir, ner_path, enable_viaf,
                  enable_kima, kima_db_path, mazal_db_path)
    input_path is the MARC extract (stage 0 output).
    ner_path is Path("") when no NER results are selected.
    """

    run_requested = pyqtSignal(Path, Path, Path, bool, bool, str, str)

    def __init__(
        self,
        default_mazal_db: Path | None = None,
        default_xml_dir: Path | None = None,
        default_kima_db: Path | None = None,
        default_kima_tsv: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # ── I/O selectors ─────────────────────────────────────────────
        self._input_selector = FileSelector(
            "MARC Extract:", mode="open", filter="JSON files (*.json)"
        )
        self._input_selector.setToolTip(
            "JSON output from Stage 1 (MARC parse). Contains original name "
            "fields (100/110/111/700/710/711) and place data."
        )
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        layout.addWidget(self._input_selector)
        layout.addWidget(self._output_selector)

        # ── NER results (optional, for NER entity matching) ──────────
        self._ner_selector = FileSelector(
            "NER Results (optional):", mode="open", filter="JSON files (*.json)"
        )
        self._ner_selector.setToolTip(
            "JSON output from Stage 2 (NER). Entities are merged into the "
            "MARC records before authority matching."
        )
        layout.addWidget(self._ner_selector)

        # ── Authority sources button ─────────────────────────────────
        sources_btn_layout = QHBoxLayout()
        self._sources_btn = QPushButton("⚙️ Authority Sources")
        self._sources_btn.setStyleSheet(theme.button_style("config"))
        self._sources_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sources_btn.setToolTip("Click to configure authority matching sources")
        self._sources_btn.clicked.connect(self._on_sources_clicked)
        sources_btn_layout.addWidget(self._sources_btn)
        sources_btn_layout.addStretch()
        layout.addLayout(sources_btn_layout)

        # Store checkbox states (default values)
        self._viaf_enabled = True
        self._kima_enabled = False
        self._mazal_enabled = True

        # ── Mazal index ────────────────────────────────────────────────
        self._mazal_group = QGroupBox("Mazal (NLI) Authority Index ▼")
        self._mazal_group.setCheckable(True)
        self._mazal_group.setChecked(True)
        self._mazal_group.toggled.connect(self._on_mazal_group_toggled)
        mazal_layout = QVBoxLayout(self._mazal_group)

        self._mazal_db_selector = FileSelector("Index DB:", mode="open", filter="SQLite DB (*.db)")
        if default_mazal_db:
            self._mazal_db_selector.path = default_mazal_db

        self._xml_dir_selector = FileSelector("XML Dir:", mode="directory")
        if default_xml_dir:
            self._xml_dir_selector.path = default_xml_dir

        self._rebuild_mazal_btn = QPushButton("Rebuild Mazal Index…")
        self._rebuild_mazal_btn.setStyleSheet(theme.button_style("warning"))
        self._rebuild_mazal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rebuild_mazal_btn.setToolTip(
            "Re-parse all NLIAUT*.xml files in the XML Dir and write a fresh SQLite index."
        )
        self._rebuild_mazal_btn.clicked.connect(self._on_rebuild_mazal)

        mazal_layout.addWidget(self._mazal_db_selector)
        mazal_layout.addWidget(self._xml_dir_selector)
        mazal_layout.addWidget(self._rebuild_mazal_btn)
        layout.addWidget(self._mazal_group)

        # ── KIMA index ─────────────────────────────────────────────────
        self._kima_group = QGroupBox("KIMA Place Authority Index ▼")
        self._kima_group.setCheckable(True)
        self._kima_group.setChecked(False)
        self._kima_group.toggled.connect(self._on_kima_group_toggled)
        kima_layout = QVBoxLayout(self._kima_group)

        self._kima_db_selector = FileSelector("Index DB:", mode="open", filter="SQLite DB (*.db)")
        if default_kima_db:
            self._kima_db_selector.path = default_kima_db

        self._kima_tsv_selector = FileSelector("TSV Dir:", mode="directory")
        self._kima_tsv_selector.setToolTip(
            "Directory containing the three KIMA TSV files "
            "('20251015 Kima places.tsv', 'Kima-Hebrew-Variants-*.tsv', "
            "'Maagarim-Zurot-&-Arachim.tsv')."
        )
        if default_kima_tsv:
            self._kima_tsv_selector.path = default_kima_tsv

        self._rebuild_kima_btn = QPushButton("Rebuild KIMA Index…")
        self._rebuild_kima_btn.setStyleSheet(theme.button_style("warning"))
        self._rebuild_kima_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rebuild_kima_btn.setToolTip(
            "Parse the KIMA TSV files and build a fresh SQLite place authority index."
        )
        self._rebuild_kima_btn.clicked.connect(self._on_rebuild_kima)

        kima_layout.addWidget(self._kima_db_selector)
        kima_layout.addWidget(self._kima_tsv_selector)
        kima_layout.addWidget(self._rebuild_kima_btn)
        layout.addWidget(self._kima_group)

        # ── Run button ─────────────────────────────────────────────────
        self._run_btn = QPushButton("Match Authorities")
        self._run_btn.clicked.connect(self._on_run)
        self._run_btn.setStyleSheet(theme.button_style())
        self._run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._run_btn)

        # Progress bar
        self._progress = PercentProgressWidget()
        layout.addWidget(self._progress)

        # ── Review banner (hidden until results load) ──────────────────
        self._review_banner = self._build_review_banner(
            "⚠  Authority matches are AI-assisted — please verify each match "
            "before building RDF. Incorrect matches will produce wrong Wikidata links."
        )
        layout.addWidget(self._review_banner)

        # ── Authority matcher view + Review & Edit launcher ─────────────
        # The read-only matcher view stays for the compact in-panel preview,
        # while the new AuthorityEditor is opened in a full-screen dialog
        # via "Review & Edit Matches" — same interaction model as NER.
        results_header = QHBoxLayout()
        results_header.addStretch()
        self._review_edit_btn = QPushButton("Review & Edit Matches")
        self._review_edit_btn.setStyleSheet(theme.button_style("primary"))
        self._review_edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._review_edit_btn.setEnabled(False)
        self._review_edit_btn.clicked.connect(self._on_review_matches)
        results_header.addWidget(self._review_edit_btn)
        layout.addLayout(results_header)

        self._matcher_view = AuthorityMatcherView()
        layout.addWidget(self._matcher_view, stretch=2)

        # Off-screen editor that the review dialog re-parents temporarily
        from mhm_pipeline.gui.widgets.authority_editor import AuthorityEditor  # noqa: PLC0415
        self._authority_editor = AuthorityEditor()
        self._authority_editor.hide()

        # Remember the output path so the editor can persist approved matches
        self._last_output_path: Path | None = None

        # ── Log viewer ─────────────────────────────────────────────────
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

    # ── Review banner helper ───────────────────────────────────────────

    def _build_review_banner(self, message: str) -> QFrame:
        """Return an amber warning banner (hidden by default)."""
        banner = QFrame()
        banner.setFrameShape(QFrame.Shape.StyledPanel)
        banner.setStyleSheet(
            "QFrame { background: #fffbeb; border: 1px solid #f59e0b; border-radius: 6px; }"
        )
        row = QHBoxLayout(banner)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)

        lbl = QLabel(message)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("border: none; color: #92400e; font-size: 12px;")
        row.addWidget(lbl, stretch=1)

        dismiss = QPushButton("✕")
        dismiss.setFixedSize(22, 22)
        dismiss.setToolTip("Dismiss")
        dismiss.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #92400e; font-size: 13px; }"
            "QPushButton:hover { color: #78350f; }"
        )
        dismiss.clicked.connect(banner.hide)
        row.addWidget(dismiss)

        banner.hide()
        return banner

    def show_review_banner(self) -> None:
        """Show the review banner (called by main window after results load)."""
        self._review_banner.show()

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        return self._log_viewer

    @property
    def stage_progress(self) -> PercentProgressWidget:
        """Return the embedded progress widget."""
        return self._progress

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_sources_clicked(self) -> None:
        """Open a dialog to configure authority sources.

        Uses the shared ``ToggleSwitch`` widget instead of ``QCheckBox``:
        switches paint a pill-shaped track with an animated knob and
        reliably reflect their state (the old checkboxes were rendered
        invisible by a QSS parsing issue with the SVG check glyph). This
        matches the Configure Models dialog pattern for consistency.
        """
        from mhm_pipeline.gui import theme  # noqa: PLC0415
        from mhm_pipeline.gui.widgets.toggle_switch import ToggleSwitch  # noqa: PLC0415

        dialog = QDialog(self)
        dialog.setWindowTitle("Authority Sources")
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(
            theme.SPACE_XL, theme.SPACE_LG, theme.SPACE_XL, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        def _make_row(
            title: str, desc: str, checked: bool, tooltip: str,
        ) -> tuple[QHBoxLayout, "ToggleSwitch"]:
            row = QHBoxLayout()
            row.setSpacing(theme.SPACE_MD)

            sw = ToggleSwitch()
            sw.setChecked(checked)
            sw.setToolTip(tooltip)
            row.addWidget(sw)

            label_col = QVBoxLayout()
            label_col.setSpacing(2)
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(
                f"color: {theme.ui('text')};"
                f" font-size: {theme.FONT_BASE}px;"
                f" font-weight: {theme.WEIGHT_SEMIBOLD};"
            )
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(
                f"color: {theme.ui('subtext')};"
                f" font-size: {theme.FONT_SM}px;"
            )
            desc_lbl.setWordWrap(True)
            label_col.addWidget(title_lbl)
            label_col.addWidget(desc_lbl)
            row.addLayout(label_col, stretch=1)
            return row, sw

        viaf_row, viaf_sw = _make_row(
            "VIAF",
            "Virtual International Authority File — person names",
            self._viaf_enabled,
            "Virtual International Authority File - person names",
        )
        layout.addLayout(viaf_row)

        mazal_row, mazal_sw = _make_row(
            "Mazal (NLI)",
            "National Library of Israel authority records — person names",
            self._mazal_enabled,
            "Mazal — National Library of Israel authority records",
        )
        layout.addLayout(mazal_row)

        kima_row, kima_sw = _make_row(
            "KIMA",
            "Open, attestation-based database of historical Hebrew-script place names",
            self._kima_enabled,
            "KIMA — an open, attestation-based database of historical place "
            "names in the Hebrew script.",
        )
        layout.addLayout(kima_row)

        layout.addSpacing(theme.SPACE_SM)
        info_label = QLabel("Select which authority sources to use for matching.")
        info_label.setStyleSheet(
            f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;"
        )
        layout.addWidget(info_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(theme.ghost_button_style())
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet(theme.button_style())
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._viaf_enabled = viaf_sw.isChecked()
            self._mazal_enabled = mazal_sw.isChecked()
            self._kima_enabled = kima_sw.isChecked()
            self._sync_group_boxes_with_sources()

    def _sync_group_boxes_with_sources(self) -> None:
        """Update group box expansion based on source selection."""
        # Update Mazal group
        self._mazal_group.blockSignals(True)
        self._mazal_group.setChecked(self._mazal_enabled)
        arrow = "▼" if self._mazal_enabled else "▶"
        self._mazal_group.setTitle(f"Mazal (NLI) Authority Index {arrow}")
        self._mazal_group.blockSignals(False)

        # Update KIMA group
        self._kima_group.blockSignals(True)
        self._kima_group.setChecked(self._kima_enabled)
        arrow = "▼" if self._kima_enabled else "▶"
        self._kima_group.setTitle(f"KIMA Place Authority Index {arrow}")
        self._kima_group.blockSignals(False)

    def _on_run(self) -> None:
        input_path = self._input_selector.path
        if input_path is None:
            self._log_viewer.append_line("Error: select a MARC extract JSON file first.")
            return

        output_path = self._output_selector.path
        if output_path is None:
            output_path = input_path.parent
            self._output_selector.path = output_path

        ner_path = self._ner_selector.path or Path("")
        kima_db_path = str(self._kima_db_selector.path or "") if self._kima_enabled else ""
        mazal_db_path = str(self._mazal_db_selector.path or "") if self._mazal_enabled else ""

        self.run_requested.emit(
            input_path,
            output_path,
            ner_path,
            self._viaf_enabled,
            self._kima_enabled,
            kima_db_path,
            mazal_db_path,
        )

    def _on_rebuild_mazal(self) -> None:
        xml_dir = self._xml_dir_selector.path
        db_path = self._mazal_db_selector.path
        if xml_dir is None:
            self._log_viewer.append_line("Error: select the Mazal XML directory first.")
            return
        if db_path is None:
            self._log_viewer.append_line("Error: select the Mazal output DB path first.")
            return

        from mhm_pipeline.controller.workers import MazalIndexWorker

        self._rebuild_mazal_btn.setEnabled(False)
        self._log_viewer.append_line(f"Rebuilding Mazal index from {xml_dir} …")
        worker = MazalIndexWorker(xml_dir=xml_dir, db_path=db_path)
        worker.log_line.connect(self._log_viewer.append_line)
        worker.finished.connect(lambda p: self._on_rebuild_mazal_done(p))
        worker.error.connect(lambda msg: self._on_rebuild_mazal_error(msg))
        worker.start()
        self._mazal_rebuild_worker = worker

    def _on_rebuild_mazal_done(self, db_path: Path) -> None:
        self._log_viewer.append_line(f"Mazal index built: {db_path}")
        self._rebuild_mazal_btn.setEnabled(True)

    def _on_rebuild_mazal_error(self, msg: str) -> None:
        self._log_viewer.append_line(f"Mazal rebuild error: {msg}")
        self._rebuild_mazal_btn.setEnabled(True)

    def _on_rebuild_kima(self) -> None:
        tsv_dir = self._kima_tsv_selector.path
        db_path = self._kima_db_selector.path
        if tsv_dir is None:
            self._log_viewer.append_line("Error: select the KIMA TSV directory first.")
            return
        if db_path is None:
            self._log_viewer.append_line("Error: select the KIMA output DB path first.")
            return

        from mhm_pipeline.controller.workers import KimaIndexWorker

        self._rebuild_kima_btn.setEnabled(False)
        self._log_viewer.append_line(f"Rebuilding KIMA index from {tsv_dir} …")
        worker = KimaIndexWorker(tsv_dir=tsv_dir, db_path=db_path)
        worker.log_line.connect(self._log_viewer.append_line)
        worker.progress.connect(
            lambda pct: (
                self._log_viewer.append_line(f"  KIMA index: {pct}%")
                if pct in (40, 85, 100)
                else None
            )
        )
        worker.finished.connect(lambda p: self._on_rebuild_kima_done(p))
        worker.error.connect(lambda msg: self._on_rebuild_kima_error(msg))
        worker.start()
        self._kima_rebuild_worker = worker

    def _on_rebuild_kima_done(self, db_path: Path) -> None:
        self._log_viewer.append_line(f"KIMA index built: {db_path}")
        self._rebuild_kima_btn.setEnabled(True)

    def _on_rebuild_kima_error(self, msg: str) -> None:
        self._log_viewer.append_line(f"KIMA rebuild error: {msg}")
        self._rebuild_kima_btn.setEnabled(True)

    def display_matches(self, matches: list[tuple[str, AuthorityMatch]]) -> None:
        """Display authority matches in the matcher view.

        Args:
            matches: List of (extracted_name, authority_match) tuples.
        """
        self._matcher_view.set_match_data(matches)
        self._current_matches = matches
        self._review_edit_btn.setEnabled(bool(matches))

    def load_authority_output(
        self,
        records: list[dict],
        output_path: Path | None = None,
        *,
        auto_review: bool = False,
    ) -> None:
        """Hydrate the AuthorityEditor from a full authority_enriched.json.

        Called by MainWindow after the AuthorityWorker finishes and
        whenever the user clicks Load Results. ``auto_review=True`` opens
        the Review & Edit Matches dialog automatically — matching NER.
        """
        self._last_output_path = output_path
        self._authority_editor.load_records(records, output_path)
        self._review_edit_btn.setEnabled(bool(records))
        if auto_review and records:
            from PyQt6.QtCore import QTimer  # noqa: PLC0415
            QTimer.singleShot(150, self._on_review_matches)

    def _on_review_matches(self) -> None:
        """Open the AuthorityEditor in a full-screen modal dialog."""
        if (
            not self._authority_editor._model._rows
            and not getattr(self, "_current_matches", None)
        ):
            QMessageBox.information(
                self, "No matches",
                "No authority matches to review. Run matching or load results first.",
            )
            return

        from mhm_pipeline.gui import theme  # noqa: PLC0415

        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"Review & Edit Authority Matches"
            f" — {self._authority_editor._model.rowCount()} matches"
        )
        screen = self.screen()
        if screen:
            geom = screen.availableGeometry()
            dialog.resize(geom.width() * 9 // 10, geom.height() * 9 // 10)
        else:
            dialog.resize(1200, 800)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(0, 0, 0, 0)
        dlg_layout.setSpacing(0)

        self._authority_editor.setParent(dialog)
        self._authority_editor.show()
        dlg_layout.addWidget(self._authority_editor, stretch=1)

        close_bar = QHBoxLayout()
        close_bar.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM,
        )
        close_bar.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(theme.button_style())
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dialog.accept)
        close_bar.addWidget(close_btn)
        dlg_layout.addLayout(close_bar)

        dialog.exec()

        # Re-parent editor back so next open works
        self._authority_editor.setParent(self)
        self._authority_editor.hide()

    def _on_mazal_group_toggled(self, checked: bool) -> None:
        """Handle Mazal group box toggle - update arrow and stored value."""
        arrow = "▼" if checked else "▶"
        self._mazal_group.setTitle(f"Mazal (NLI) Authority Index {arrow}")
        self._mazal_enabled = checked

    def _on_kima_group_toggled(self, checked: bool) -> None:
        """Handle KIMA group box toggle - update arrow and stored value."""
        arrow = "▼" if checked else "▶"
        self._kima_group.setTitle(f"KIMA Place Authority Index {arrow}")
        self._kima_enabled = checked

    def _on_view_full_results(self) -> None:
        """Open a dialog with the full results table."""
        if not hasattr(self, "_current_matches") or not self._current_matches:
            QMessageBox.information(
                self,
                "No Results",
                "No authority matches to display. Match Authorities first.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Authority Match Results")
        screen = self.screen()
        if screen:
            geom = screen.availableGeometry()
            dialog.resize(geom.width() * 9 // 10, geom.height() * 9 // 10)
        else:
            dialog.resize(1200, 800)

        layout = QVBoxLayout(dialog)

        # Create a larger matcher view
        full_view = AuthorityMatcherView()
        full_view.set_match_data(self._current_matches)
        layout.addWidget(full_view)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(theme.button_style('ghost'))
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec()
