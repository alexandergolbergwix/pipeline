"""Stage 3 — Authority reconciliation panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer


class AuthorityPanel(QWidget):
    """Panel for Stage 3: authority record reconciliation.

    Signal args: (input_path, output_dir, marc_path, enable_viaf,
                  enable_kima, kima_db_path, mazal_db_path)
    marc_path is Path("") when no MARC extract is selected.
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
            "NER Results:", mode="open", filter="JSON files (*.json)"
        )
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        layout.addWidget(self._input_selector)
        layout.addWidget(self._output_selector)

        # ── MARC extract (optional, for place matching) ────────────────
        self._marc_selector = FileSelector(
            "MARC Extract (optional):", mode="open", filter="JSON files (*.json)"
        )
        self._marc_selector.setToolTip(
            "JSON output from Stage 1 (MARC parse). Used for KIMA place matching."
        )
        layout.addWidget(self._marc_selector)

        # ── Authority sources ──────────────────────────────────────────
        sources_group = QGroupBox("Authority Sources")
        sources_layout = QVBoxLayout(sources_group)

        self._viaf_cb = QCheckBox("Enable VIAF (person names)")
        self._viaf_cb.setChecked(True)

        self._kima_cb = QCheckBox("Enable KIMA (Hebrew historical place names)")
        self._kima_cb.setChecked(False)
        self._kima_cb.setToolTip(
            "KIMA — an open, attestation-based database of historical place names "
            "in the Hebrew script. Requires the KIMA SQLite index to be built first."
        )

        sources_layout.addWidget(self._viaf_cb)
        sources_layout.addWidget(self._kima_cb)
        layout.addWidget(sources_group)

        # ── Mazal index ────────────────────────────────────────────────
        mazal_group = QGroupBox("Mazal (NLI) Authority Index")
        mazal_layout = QVBoxLayout(mazal_group)

        self._mazal_db_selector = FileSelector(
            "Index DB:", mode="open", filter="SQLite DB (*.db)"
        )
        if default_mazal_db:
            self._mazal_db_selector.path = default_mazal_db

        self._xml_dir_selector = FileSelector("XML Dir:", mode="directory")
        if default_xml_dir:
            self._xml_dir_selector.path = default_xml_dir

        self._rebuild_mazal_btn = QPushButton("Rebuild Mazal Index…")
        self._rebuild_mazal_btn.setToolTip(
            "Re-parse all NLIAUT*.xml files in the XML Dir and write a fresh SQLite index."
        )
        self._rebuild_mazal_btn.clicked.connect(self._on_rebuild_mazal)

        mazal_layout.addWidget(self._mazal_db_selector)
        mazal_layout.addWidget(self._xml_dir_selector)
        mazal_layout.addWidget(self._rebuild_mazal_btn)
        layout.addWidget(mazal_group)

        # ── KIMA index ─────────────────────────────────────────────────
        kima_group = QGroupBox("KIMA Place Authority Index")
        kima_layout = QVBoxLayout(kima_group)

        self._kima_db_selector = FileSelector(
            "Index DB:", mode="open", filter="SQLite DB (*.db)"
        )
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
        self._rebuild_kima_btn.setToolTip(
            "Parse the KIMA TSV files and build a fresh SQLite place authority index."
        )
        self._rebuild_kima_btn.clicked.connect(self._on_rebuild_kima)

        kima_layout.addWidget(self._kima_db_selector)
        kima_layout.addWidget(self._kima_tsv_selector)
        kima_layout.addWidget(self._rebuild_kima_btn)
        layout.addWidget(kima_group)

        # ── Run button ─────────────────────────────────────────────────
        self._run_btn = QPushButton("Run Stage 3")
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        # ── Log viewer ─────────────────────────────────────────────────
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        return self._log_viewer

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        input_path = self._input_selector.path
        if input_path is None:
            self._log_viewer.append_line("Error: select a NER results JSON file first.")
            return

        output_path = self._output_selector.path
        if output_path is None:
            output_path = input_path.parent
            self._output_selector.path = output_path

        marc_path = self._marc_selector.path or Path("")
        kima_db_path = str(self._kima_db_selector.path or "")
        mazal_db_path = str(self._mazal_db_selector.path or "")

        self.run_requested.emit(
            input_path,
            output_path,
            marc_path,
            self._viaf_cb.isChecked(),
            self._kima_cb.isChecked(),
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
            lambda pct: self._log_viewer.append_line(f"  KIMA index: {pct}%")
            if pct in (40, 85, 100) else None
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
