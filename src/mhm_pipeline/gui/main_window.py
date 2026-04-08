"""Main application window for the MHM Pipeline desktop app."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.controller.pipeline_controller import PipelineController
from mhm_pipeline.gui.panels.authority_panel import AuthorityPanel
from mhm_pipeline.gui.panels.convert_panel import ConvertPanel
from mhm_pipeline.gui.panels.ner_panel import NerPanel
from mhm_pipeline.gui.panels.rdf_panel import RdfPanel
from mhm_pipeline.gui.panels.validate_panel import ValidatePanel
from mhm_pipeline.gui.panels.wikidata_panel import WikidataPanel
from mhm_pipeline.gui.widgets.entity_highlighter import Entity
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.pipeline_flow_widget import PipelineFlowWidget
from mhm_pipeline.platform_.gpu import get_device
from mhm_pipeline.settings.settings_manager import SettingsManager

_STAGE_LABELS: list[str] = [
    "MARC Parsing",
    "NER Extraction",
    "Authority Matching",
    "RDF Graph",
    "SHACL Validation",
    "Wikidata Upload",
]

_STATE_ICONS: dict[str, str] = {
    "pending": "\u25CB",   # ○
    "running": "\u25D4",   # ◔
    "done": "\u25CF",      # ●
    "error": "\u2716",     # ✖
}


class MainWindow(QMainWindow):
    """Root window of the MHM Pipeline application."""

    def __init__(
        self,
        settings: SettingsManager,
        controller: PipelineController,
        parent: QMainWindow | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._controller = controller
        self._stage_states: list[str] = ["pending"] * len(_STAGE_LABELS)

        self.setWindowTitle("MHM Pipeline")
        self.setMinimumSize(960, 640)

        self._build_menu_bar()
        self._build_central()
        self._build_status_bar()
        self._connect_controller()

    # ── Menu bar ──────────────────────────────────────────────────────

    def _build_menu_bar(self) -> None:
        """Create File, Pipeline, and Help menus."""
        menu_bar = self.menuBar()

        # File
        file_menu = menu_bar.addMenu("&File")
        open_action = QAction("&Open MARC…", self)
        open_action.triggered.connect(self._on_open_marc)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Pipeline
        pipeline_menu = menu_bar.addMenu("&Pipeline")
        run_all_action = QAction("&Run All", self)
        run_all_action.triggered.connect(self._on_run_all)
        pipeline_menu.addAction(run_all_action)
        cancel_action = QAction("&Cancel", self)
        cancel_action.triggered.connect(self._controller.cancel)
        pipeline_menu.addAction(cancel_action)

        # Help
        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    # ── Central widget ────────────────────────────────────────────────

    def _build_central(self) -> None:
        """Build the sidebar + stacked panels + bottom log layout."""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)

        # Pipeline flow widget (overview at top)
        self._flow_widget = PipelineFlowWidget()
        self._flow_widget.stage_clicked.connect(self._on_stage_clicked)
        main_layout.addWidget(self._flow_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # sidebar
        self._sidebar = QListWidget()
        self._sidebar.setMinimumWidth(120)
        self._sidebar.setMaximumWidth(200)
        for label in _STAGE_LABELS:
            item = QListWidgetItem(f"{_STATE_ICONS['pending']}  {label}")
            self._sidebar.addItem(item)
        self._sidebar.currentRowChanged.connect(self._on_sidebar_changed)
        splitter.addWidget(self._sidebar)

        # stacked panels
        self._stack = QStackedWidget()
        self._convert_panel = ConvertPanel()
        self._ner_panel = NerPanel()
        self._authority_panel = AuthorityPanel(
            default_mazal_db=self._settings.mazal_db_path,
            default_xml_dir=self._settings.mazal_xml_dir,
            default_kima_db=self._settings.kima_db_path,
            default_kima_tsv=self._settings.kima_tsv_dir,
        )
        self._rdf_panel = RdfPanel()
        self._validate_panel = ValidatePanel()
        self._wikidata_panel = WikidataPanel()

        self._panels: list[QWidget] = [
            self._convert_panel,
            self._ner_panel,
            self._authority_panel,
            self._rdf_panel,
            self._validate_panel,
            self._wikidata_panel,
        ]
        for panel in self._panels:
            self._stack.addWidget(panel)
        splitter.addWidget(self._stack)
        splitter.setCollapsible(0, True)  # Sidebar can collapse
        splitter.setStretchFactor(0, 0)   # Sidebar doesn't stretch
        splitter.setStretchFactor(1, 1)   # Panels get all extra space

        main_layout.addWidget(splitter, stretch=3)

        # shared bottom log viewer
        self._shared_log = LogViewer()
        self._shared_log.setMaximumBlockCount(5000)
        main_layout.addWidget(self._shared_log, stretch=1)

        self._sidebar.setCurrentRow(0)

    # ── Status bar ────────────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        """Add GPU and record-count labels to the status bar."""
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

        device = get_device()
        self._gpu_label = QLabel(f"GPU: {device}")
        self._record_label = QLabel("Records: 0")

        status_bar.addPermanentWidget(self._gpu_label)
        status_bar.addPermanentWidget(self._record_label)

    # ── Controller wiring ─────────────────────────────────────────────

    def _connect_controller(self) -> None:
        """Wire pipeline controller signals to UI updates."""
        self._controller.stage_started.connect(self._on_stage_started)
        self._controller.stage_finished.connect(self._on_stage_finished)
        self._controller.stage_error.connect(self._on_stage_error)
        self._controller.stage_progress.connect(self._on_stage_progress)
        self._controller.pipeline_finished.connect(self._on_pipeline_finished)
        self._controller.entity_status.connect(self._wikidata_panel.update_entity_status)

        # Stage panels → controller
        self._convert_panel.run_requested.connect(self._on_run_convert)
        self._ner_panel.run_requested.connect(self._on_run_ner)
        self._authority_panel.run_requested.connect(self._on_run_authority)
        self._rdf_panel.run_requested.connect(self._on_run_rdf)
        self._validate_panel.run_requested.connect(self._on_run_validate)
        self._wikidata_panel.run_requested.connect(self._on_run_wikidata)

    def _on_stage_started(self, index: int) -> None:
        self._update_stage_state(index, "running")
        self._shared_log.append_line(f"Stage {index + 1} started…")

    def _on_stage_progress(self, index: int, pct: int) -> None:
        """Update progress bar for the given stage."""
        panel = self._panels[index] if 0 <= index < len(self._panels) else None
        if panel and hasattr(panel, "stage_progress"):
            panel.stage_progress.set_progress(pct)

    def _on_stage_finished(self, index: int, output: Path) -> None:
        self._update_stage_state(index, "done")
        self._shared_log.append_line(
            f"Stage {index + 1} finished. Output: {output}"
        )
        self._load_stage_results(index, output)
        self._autofill_next_stage(index, output)

    def _load_stage_results(self, index: int, output: Path) -> None:
        """Load stage output into the appropriate panel visualization."""
        if index == 1:  # NER stage
            self._load_ner_results(output)
        elif index == 2:  # Authority stage
            self._load_authority_results(output)

    def _load_ner_results(self, output: Path) -> None:
        """Load NER results and display entities in the panel."""
        try:
            import json

            with open(output, encoding="utf-8") as f:
                results = json.load(f)

            # Collect all entities from all records
            all_entities = []
            texts = []

            # Handle both list and dict formats
            if isinstance(results, list):
                records = results
            elif isinstance(results, dict) and "records" in results:
                records = results["records"]
            else:
                records = [results]

            for record in records:
                if not isinstance(record, dict):
                    continue

                entities = record.get("entities", [])
                text = record.get("text", "")

                if text:
                    texts.append(text)

                for ent in entities:
                    if not isinstance(ent, dict):
                        continue

                    # Map the entity data
                    entity = Entity(
                        text=ent.get("person", ent.get("text", "")),
                        type="PERSON",  # The model extracts persons
                        start=ent.get("start", 0),
                        end=ent.get("end", 0),
                        role=ent.get("role"),
                        confidence=ent.get("confidence"),
                    )
                    all_entities.append(entity)

            # Display in the panel - use display_records for proper formatting
            display_text = texts[0] if texts else "NER results loaded"
            self._ner_panel.display_entities(display_text, all_entities, records)

        except Exception as e:
            self._shared_log.append_line(f"Failed to load NER results: {e}")

    def _load_authority_results(self, output: Path) -> None:
        """Load authority results and display matches in the panel."""
        try:
            import json

            from mhm_pipeline.gui.widgets.authority_matcher_view import (
                AuthorityMatch,
            )

            with open(output, encoding="utf-8") as f:
                results = json.load(f)

            matches: list[tuple[str, AuthorityMatch]] = []

            # Handle different result formats
            if isinstance(results, dict) and "matches" in results:
                records = results["matches"]
            elif isinstance(results, list):
                records = results
            else:
                records = [results]

            for record in records:
                if not isinstance(record, dict):
                    continue

                extracted = record.get("extracted_name", "")
                match_data = record.get("match", {})

                if extracted and match_data:
                    match = AuthorityMatch(
                        source=match_data.get("source", "unknown"),
                        id=match_data.get("id", ""),
                        preferred_name=match_data.get("preferred_name", ""),
                        confidence=match_data.get("confidence", 0.0),
                        found=match_data.get("found", False),
                    )
                    matches.append((extracted, match))

                # Also include MARC authority matches
                for marc_match in record.get("marc_authority_matches") or []:
                    if marc_match.get("mazal_id") or marc_match.get("viaf_uri"):
                        match = AuthorityMatch(
                            source="mazal" if marc_match.get("mazal_id") else "viaf",
                            id=marc_match.get("mazal_id") or marc_match.get("viaf_uri", ""),
                            preferred_name=marc_match.get("name", ""),
                            confidence=1.0 if marc_match.get("mazal_id") else 0.9,
                            found=True,
                        )
                        matches.append((marc_match.get("name", ""), match))

            self._authority_panel.display_matches(matches)
            self._shared_log.append_line(
                f"Loaded {len(matches)} authority matches from {output.name}"
            )

        except Exception as e:
            self._shared_log.append_line(f"Failed to load authority results: {e}")

    def _autofill_next_stage(self, completed: int, output: Path) -> None:
        """Pre-populate the next panel's input and output selectors."""
        out_dir = output.parent
        if completed == 0:
            # Stage 0 output (MARC extract) feeds NER and Authority
            self._ner_panel._input_selector.path = output
            self._ner_panel._output_selector.path = out_dir
            self._authority_panel._input_selector.path = output
            self._authority_panel._output_selector.path = out_dir
        elif completed == 1:
            # Stage 1 output (NER results) feeds Authority as optional enrichment
            self._authority_panel._ner_selector.path = output
        elif completed == 2:
            self._rdf_panel._input_selector.path = output
            self._rdf_panel._output_selector.path = out_dir
        elif completed == 3:
            self._validate_panel._ttl_selector.path = output
            self._wikidata_panel._ttl_selector.path = output

    def _on_stage_error(self, index: int, message: str) -> None:
        self._update_stage_state(index, "error")
        self._shared_log.append_line(f"Stage {index + 1} ERROR: {message}")

    def _on_pipeline_finished(self) -> None:
        self._shared_log.append_line("Pipeline complete.")

    def _update_stage_state(self, index: int, state: str) -> None:
        if index < 0 or index >= len(_STAGE_LABELS):
            return
        self._stage_states[index] = state
        icon = _STATE_ICONS.get(state, _STATE_ICONS["pending"])
        item = self._sidebar.item(index)
        if item is not None:
            item.setText(f"{icon}  {_STAGE_LABELS[index]}")

        # also update the convert panel's progress widget if present
        if hasattr(self._convert_panel, "stage_progress"):
            self._convert_panel.stage_progress.set_stage_state(index, state)

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_sidebar_changed(self, row: int) -> None:
        if 0 <= row < self._stack.count():
            self._stack.setCurrentIndex(row)
            self._flow_widget.set_active_stage(row)

    def _on_stage_clicked(self, index: int) -> None:
        """Handle stage clicked in flow widget.

        Args:
            index: The stage index (0-5).
        """
        self._sidebar.setCurrentRow(index)

    def _on_run_convert(
        self, input_path: Path, output_path: Path, start: int, end: int
    ) -> None:
        self._shared_log.append_line(f"Parsing {input_path.name}…")
        self._controller.start_stage(
            0, input_path=input_path, output_dir=output_path, start=start, end=end,
        )

    def _on_run_ner(
        self, input_path: Path, output_path: Path, model_path: str,
        batch_size: int, provenance_model_path: str, contents_model_path: str,
    ) -> None:
        self._shared_log.append_line(f"Running NER on {input_path.name}…")
        self._controller.start_stage(
            1, input_path=input_path, output_dir=output_path,
            model_path=model_path, batch_size=batch_size,
            provenance_model_path=provenance_model_path,
            contents_model_path=contents_model_path,
        )

    def _on_run_authority(
        self,
        input_path: Path,
        output_path: Path,
        ner_path: Path,
        viaf: bool,
        kima: bool,
        kima_db_path: str,
        mazal_db_path: str,
    ) -> None:
        self._shared_log.append_line(f"Authority resolution: {input_path.name}…")
        # ner_path is Path("") when no file was selected
        ner: Path | None = ner_path if ner_path.name else None
        self._controller.start_stage(
            2,
            input_path=input_path,
            output_dir=output_path,
            ner_path=ner,
            enable_viaf=viaf,
            enable_kima=kima,
            kima_db_path=kima_db_path,
            mazal_db_path=mazal_db_path,
        )

    def _on_run_rdf(self, input_path: Path, output_path: Path, fmt: str) -> None:
        self._shared_log.append_line(f"Building RDF from {input_path.name}…")
        self._controller.start_stage(
            3, input_path=input_path, output_dir=output_path, rdf_format=fmt,
        )

    def _on_run_validate(self, ttl_path: Path, shapes_path: Path) -> None:
        self._shared_log.append_line(f"Validating {ttl_path.name}…")
        self._controller.start_stage(4, input_path=ttl_path, shapes_path=shapes_path)

    def _on_run_wikidata(
        self, input_path: Path, output_dir: Path,
        token: str, dry_run: bool, batch_mode: bool,
    ) -> None:
        mode = "dry run" if dry_run else "live upload"
        self._shared_log.append_line(f"Wikidata {mode} from {input_path.name}…")
        self._controller.start_stage(
            5, input_path=input_path, output_dir=output_dir,
            token=token, dry_run=dry_run, batch_mode=batch_mode,
        )

    def _on_open_marc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Input File", "", "MARC / TSV files (*.mrc *.tsv *.csv)"
        )
        if path:
            self._sidebar.setCurrentRow(0)
            self._convert_panel._input_selector.path = Path(path)

    def _on_run_all(self) -> None:
        self._shared_log.append_line("Starting full pipeline run…")
        self._controller.start_stage(0)

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About MHM Pipeline",
            "MHM Pipeline\n\n"
            "A desktop application for processing MARC Hebrew Manuscript records\n"
            "through NER, authority reconciliation, RDF serialisation,\n"
            "SHACL validation, and Wikidata upload.\n\n"
            "Bar-Ilan University",
        )
