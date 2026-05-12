"""Main application window for the MHM Pipeline desktop app."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (
    QApplication,
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
from mhm_pipeline.gui.panels.wikidata_studio_panel import WikidataStudioPanel
from mhm_pipeline.gui.widgets.entity_highlighter import Entity
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
# PipelineFlowWidget retired — the left sidebar is the single source of
# truth for stage state, and the top bar duplicated that navigation.
from mhm_pipeline.platform_.gpu import get_device
from mhm_pipeline.settings.settings_manager import SettingsManager

# Wikidata Preview + Wikidata Upload have been merged into "Wikidata Studio"
_STAGE_LABELS: list[str] = [
    "MARC Parsing",
    "NER Extraction",
    "Authority Matching",
    "RDF Graph",
    "SHACL Validation",
    "Wikidata Studio",
]

_STATE_ICONS: dict[str, str] = {
    "pending": "\u25cb",  # ○
    "running": "\u25d4",  # ◔
    "done": "\u25cf",  # ●
    "error": "\u2716",  # ✖
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
        assert menu_bar is not None

        # File
        file_menu = menu_bar.addMenu("&File")
        assert file_menu is not None
        open_action = QAction("&Open MARC…", self)
        open_action.triggered.connect(self._on_open_marc)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Pipeline
        pipeline_menu = menu_bar.addMenu("&Pipeline")
        assert pipeline_menu is not None
        run_all_action = QAction("&Run All", self)
        run_all_action.triggered.connect(self._on_run_all)
        pipeline_menu.addAction(run_all_action)
        cancel_action = QAction("&Cancel", self)
        cancel_action.triggered.connect(self._controller.cancel)
        pipeline_menu.addAction(cancel_action)

        # Settings
        self._build_settings_menu(menu_bar)

        # Help
        help_menu = menu_bar.addMenu("&Help")
        assert help_menu is not None
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _build_settings_menu(self, menu_bar: object) -> None:
        """Settings menu — Appearance / Log level / GPU device / utilities.

        Keep it a top-level menu (not under File or Help) per the user's
        explicit request that it sits next to "File" on both macOS and
        Windows. macOS will still let users press the standard Cmd+, but
        the menu remains visible in the global menu bar.
        """
        settings_menu = menu_bar.addMenu("&Settings")  # type: ignore[attr-defined]
        assert settings_menu is not None

        # ── Appearance ──────────────────────────────────────────────────
        appearance_menu = settings_menu.addMenu("&Appearance")
        assert appearance_menu is not None
        self._appearance_group = QActionGroup(self)
        self._appearance_group.setExclusive(True)
        current_theme = self._settings.theme
        for label, value in (
            ("&System Default", "system"),
            ("&Dark", "dark"),
            ("&Light", "light"),
        ):
            action = QAction(label, self, checkable=True)
            action.setData(value)
            action.setChecked(current_theme == value)
            action.triggered.connect(
                lambda _checked=False, v=value: self._on_theme_change(v)
            )
            self._appearance_group.addAction(action)
            appearance_menu.addAction(action)

        # ── Log level ───────────────────────────────────────────────────
        log_menu = settings_menu.addMenu("&Log Level")
        assert log_menu is not None
        self._log_level_group = QActionGroup(self)
        self._log_level_group.setExclusive(True)
        current_log = self._settings.log_level
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            action = QAction(level, self, checkable=True)
            action.setData(level)
            action.setChecked(current_log == level)
            action.triggered.connect(
                lambda _checked=False, lv=level: self._on_log_level_change(lv)
            )
            self._log_level_group.addAction(action)
            log_menu.addAction(action)

        # ── GPU device ──────────────────────────────────────────────────
        gpu_menu = settings_menu.addMenu("&GPU Device")
        assert gpu_menu is not None
        self._gpu_device_group = QActionGroup(self)
        self._gpu_device_group.setExclusive(True)
        current_gpu = self._settings.gpu_device
        for label, value in (
            ("&Auto-detect", "auto"),
            ("&MPS (Apple Silicon)", "mps"),
            ("&CUDA (NVIDIA)", "cuda"),
            ("&CPU only", "cpu"),
        ):
            action = QAction(label, self, checkable=True)
            action.setData(value)
            action.setChecked(current_gpu == value)
            action.triggered.connect(
                lambda _checked=False, v=value: self._on_gpu_device_change(v)
            )
            self._gpu_device_group.addAction(action)
            gpu_menu.addAction(action)

        settings_menu.addSeparator()

        # ── Utilities ───────────────────────────────────────────────────
        open_settings_action = QAction("&Open Settings File…", self)
        open_settings_action.triggered.connect(self._on_open_settings_file)
        settings_menu.addAction(open_settings_action)

        open_log_dir_action = QAction("Open &Log Folder…", self)
        open_log_dir_action.triggered.connect(self._on_open_log_dir)
        settings_menu.addAction(open_log_dir_action)

        reset_wizard_action = QAction("&Reset First-Run Wizard", self)
        reset_wizard_action.triggered.connect(self._on_reset_first_run)
        settings_menu.addAction(reset_wizard_action)

    # ── Settings handlers ─────────────────────────────────────────────

    def _on_theme_change(self, value: str) -> None:
        """Persist the theme choice and re-apply the global stylesheet."""
        self._settings.theme = value
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        theme.invalidate_cache()
        app = QApplication.instance()
        if app is not None:
            theme.apply_stylesheet(app)
        # Force every widget tree to repaint with new colours
        self.update()
        for child in self.findChildren(QWidget):
            child.update()

    def _on_log_level_change(self, level: str) -> None:
        """Persist log level and apply it to the root logger."""
        import logging  # noqa: PLC0415

        self._settings.log_level = level
        logging.getLogger().setLevel(getattr(logging, level, logging.INFO))

    def _on_gpu_device_change(self, device: str) -> None:
        """Persist the GPU device choice. Takes effect on the next Stage 2 run."""
        self._settings.gpu_device = device
        QMessageBox.information(
            self,
            "GPU Device Changed",
            f"GPU device set to '{device}'. The change applies to the next NER run.",
        )

    def _on_open_settings_file(self) -> None:
        """Open the QSettings INI file in the platform's default editor."""
        import os  # noqa: PLC0415
        import sys  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        path = self._settings._qs.fileName()  # noqa: SLF001
        if not path:
            QMessageBox.warning(self, "Settings", "Could not locate settings file.")
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            elif sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Settings", f"Could not open file:\n{exc}")

    def _on_open_log_dir(self) -> None:
        """Reveal the platform log directory in the file manager."""
        import os  # noqa: PLC0415
        import sys  # noqa: PLC0415
        import subprocess  # noqa: PLC0415
        from mhm_pipeline.platform_.paths import app_log_dir  # noqa: PLC0415

        log_dir = app_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(log_dir)], check=False)
            elif sys.platform == "win32":
                os.startfile(str(log_dir))  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", str(log_dir)], check=False)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Logs", f"Could not open folder:\n{exc}")

    def _on_reset_first_run(self) -> None:
        """Clear first_run_done so the wizard reappears on next launch."""
        reply = QMessageBox.question(
            self,
            "Reset First-Run Wizard",
            "Reset the first-run wizard? It will run again the next time "
            "you start MHM Pipeline.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._settings.first_run_done = False
            QMessageBox.information(
                self,
                "Reset",
                "First-run wizard reset. Restart the app to see it.",
            )

    # ── Central widget ────────────────────────────────────────────────

    def _build_central(self) -> None:
        """Build the sidebar + stacked panels + bottom log layout."""
        from mhm_pipeline.gui import theme  # noqa: PLC0415
        from mhm_pipeline.gui.widgets.graph_backdrop import GraphBackdrop  # noqa: PLC0415

        # The central widget IS the graph backdrop — its paintEvent draws the
        # node/edge wallpaper. Every child (flow widget, sidebar, panels, log)
        # composites on top and their translucent surfaces "read through" it.
        central = GraphBackdrop()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD)
        main_layout.setSpacing(theme.SPACE_MD)

        # (Top pipeline flow bar removed — the left sidebar is the single
        # source of truth for stage navigation + state; the duplicate
        # top bar only added vertical clutter.)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(theme.SPACE_SM)

        # sidebar
        self._sidebar = QListWidget()
        self._sidebar.setMinimumWidth(140)
        self._sidebar.setMaximumWidth(220)
        for label in _STAGE_LABELS:
            item = QListWidgetItem(f"{_STATE_ICONS['pending']}  {label}")
            self._sidebar.addItem(item)
        self._sidebar.currentRowChanged.connect(self._on_sidebar_changed)
        theme.apply_drop_shadow(self._sidebar, blur=22, offset_y=4)
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
        # Unified tab: replaces the old WikidataPreview + WikidataUpload
        self._wikidata_studio_panel = WikidataStudioPanel()

        self._panels: list[QWidget] = [
            self._convert_panel,
            self._ner_panel,
            self._authority_panel,
            self._rdf_panel,                # stage 3
            self._validate_panel,           # stage 4
            self._wikidata_studio_panel,    # stage 5 (merged)
        ]
        # Wrap each panel in a scroll area so content is reachable even when
        # the window is resized below the panel's natural size. Native
        # QTableView / QPlainTextEdit scrolling still works inside.
        from mhm_pipeline.gui.widgets.flow_layout import make_scrollable  # noqa: PLC0415
        for panel in self._panels:
            self._stack.addWidget(make_scrollable(panel, horizontal=True, vertical=True))
        splitter.addWidget(self._stack)
        splitter.setCollapsible(0, True)  # Sidebar can collapse
        splitter.setStretchFactor(0, 0)  # Sidebar doesn't stretch
        splitter.setStretchFactor(1, 1)  # Panels get all extra space

        # Lift the stacked-panels area with a subtle drop shadow
        theme.apply_drop_shadow(self._stack, blur=30, offset_y=8)
        main_layout.addWidget(splitter, stretch=3)

        # shared bottom log viewer
        self._shared_log = LogViewer()
        self._shared_log.setMaximumBlockCount(5000)
        theme.apply_drop_shadow(self._shared_log, blur=22, offset_y=4)
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
        # Substep label forwarded into the active panel's DynamicProgressBar
        self._controller.stage_substep.connect(self._on_stage_substep)
        self._controller.pipeline_finished.connect(self._on_pipeline_finished)
        # entity_status feeds per-item upload progress in the studio panel.
        # The studio panel doesn't currently surface this (it owns its own
        # dry-run / live progress internally). Keep the signal but route to
        # a no-op so the controller doesn't error on emit.
        self._controller.entity_status.connect(
            lambda *_args: None  # TODO: route to the studio panel's upload view
        )

        # Stage panels → controller
        self._convert_panel.run_requested.connect(self._on_run_convert)
        self._ner_panel.run_requested.connect(self._on_run_ner)
        self._authority_panel.run_requested.connect(self._on_run_authority)
        self._rdf_panel.run_requested.connect(self._on_run_rdf)
        self._validate_panel.run_requested.connect(self._on_run_validate)
        # The merged Wikidata Studio panel emits its own upload_requested
        # signal that carries the list of already-approved WikidataItems.
        self._wikidata_studio_panel.upload_requested.connect(
            self._on_run_wikidata_studio,
        )

    # Per-stage success / failure labels for the DynamicProgressBar.
    # Mirrors Agent C's spec — each stage's bar finishes with the right
    # text when stage_finished / stage_error fires.
    _STAGE_PROGRESS_LABELS: tuple[tuple[str, str], ...] = (
        ("Stage 1 — MARC parsed",            "Stage 1 failed"),
        ("Stage 2 — NER complete",           "Stage 2 failed"),
        ("Stage 3 — authority enriched",     "Stage 3 failed"),
        ("Stage 4 — RDF built",              "Stage 4 failed"),
        ("Stage 5 — SHACL OK",               "Stage 5 — SHACL failed"),
        ("Stage 6 — Wikidata upload complete", "Stage 6 — upload failed"),
    )

    def _on_stage_started(self, index: int) -> None:
        self._update_stage_state(index, "running")
        self._shared_log.append_line(f"Stage {index + 1} started…")
        # Reset the panel's DynamicProgressBar so a fresh ETA history begins.
        bar = self._panel_progress_bar(index)
        if bar is not None and hasattr(bar, "reset"):
            bar.reset()
            # Percent semantics — total is 100 so ``set_progress(pct)``
            # works with no extra arg from the controller's existing
            # ``stage_progress(int, int)`` signal.
            if hasattr(bar, "set_total"):
                bar.set_total(100)

    def _on_stage_progress(self, index: int, pct: int) -> None:
        """Update progress bar for the given stage."""
        bar = self._panel_progress_bar(index)
        if bar is None:
            return
        if hasattr(bar, "set_total"):
            # DynamicProgressBar — percent of 100 with running ETA.
            bar.set_progress(pct, 100)
        else:
            bar.set_progress(pct)

    def _on_stage_substep(self, index: int, label: str) -> None:
        """Forward substep label from controller to the panel's dynamic bar."""
        bar = self._panel_progress_bar(index)
        if bar is not None and hasattr(bar, "set_substep"):
            bar.set_substep(label)

    def _panel_progress_bar(self, index: int) -> object | None:
        """Return the panel's progress widget at *index*, or None."""
        panel = self._panels[index] if 0 <= index < len(self._panels) else None
        return getattr(panel, "stage_progress", None)

    def _on_stage_finished(self, index: int, output: Path) -> None:
        self._update_stage_state(index, "done")
        self._shared_log.append_line(f"Stage {index + 1} finished. Output: {output}")
        bar = self._panel_progress_bar(index)
        if bar is not None and hasattr(bar, "finish") and 0 <= index < len(self._STAGE_PROGRESS_LABELS):
            bar.finish(self._STAGE_PROGRESS_LABELS[index][0], success=True)
        self._load_stage_results(index, output)
        self._autofill_next_stage(index, output)
        if index == 1:
            self._ner_panel.show_review_banner()
        elif index == 2:
            self._authority_panel.show_review_banner()
            # Pre-fill the studio's input selector with the authority output
            # so the user can immediately click "Load & Build Items".
            self._wikidata_studio_panel._input_selector.path = output

    def _load_stage_results(self, index: int, output: Path) -> None:
        """Load stage output into the appropriate panel visualization."""
        if index == 1:
            self._load_ner_results(output)
        elif index == 2:
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
                        text=str(ent.get("person", ent.get("text", "")) or ""),
                        type="PERSON",  # The model extracts persons
                        start=ent.get("start", 0),
                        end=ent.get("end", 0),
                        role=ent.get("role"),
                        confidence=ent.get("confidence"),
                    )
                    all_entities.append(entity)

            # Display in the panel - use display_records for proper formatting
            display_text = texts[0] if texts else "NER results loaded"
            # Let the panel remember where to save approved results later
            self._ner_panel._last_output_path = output  # type: ignore[attr-defined]
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
            # Also hydrate the full-featured AuthorityEditor and auto-open
            # the Review & Edit Matches dialog — mirrors the NER flow.
            self._authority_panel.load_authority_output(
                records if isinstance(records, list) else [],
                output_path=output,
                auto_review=True,
            )
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
            # Stage 2 (Authority enriched) output feeds the RDF panel AND
            # the merged Wikidata Studio (which reads authority_enriched.json
            # directly and builds Wikidata items offline).
            self._rdf_panel._input_selector.path = output
            self._rdf_panel._output_selector.path = out_dir
            self._wikidata_studio_panel._input_selector.path = output
        elif completed == 3:
            # Stage 3 (RDF) output feeds SHACL
            self._validate_panel._ttl_selector.path = output

    def _on_stage_error(self, index: int, message: str) -> None:
        self._update_stage_state(index, "error")
        self._shared_log.append_line(f"Stage {index + 1} ERROR: {message}")
        bar = self._panel_progress_bar(index)
        if bar is not None and hasattr(bar, "finish") and 0 <= index < len(self._STAGE_PROGRESS_LABELS):
            bar.finish(self._STAGE_PROGRESS_LABELS[index][1], success=False)

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

        # also update the convert panel's six-pill stage overview if present
        overview = getattr(self._convert_panel, "stage_overview", None)
        if overview is not None and hasattr(overview, "set_stage_state"):
            overview.set_stage_state(index, state)

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_sidebar_changed(self, row: int) -> None:
        if 0 <= row < self._stack.count():
            self._stack.setCurrentIndex(row)

    def _on_run_convert(self, input_path: Path, output_path: Path, start: int, end: int) -> None:
        self._shared_log.append_line(f"Parsing {input_path.name}…")
        self._controller.start_stage(
            0,
            input_path=input_path,
            output_dir=output_path,
            start=start,
            end=end,
        )

    def _on_run_ner(
        self,
        input_path: Path,
        output_path: Path,
        model_path: str,
        batch_size: int,
        provenance_model_path: str,
        contents_model_path: str,
    ) -> None:
        self._shared_log.append_line(f"Running NER on {input_path.name}…")
        self._controller.start_stage(
            1,
            input_path=input_path,
            output_dir=output_path,
            model_path=model_path,
            batch_size=batch_size,
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
            3,
            input_path=input_path,
            output_dir=output_path,
            rdf_format=fmt,
        )

    def _on_run_validate(self, ttl_path: Path, shapes_path: Path) -> None:
        self._shared_log.append_line(f"Validating {ttl_path.name}…")
        self._controller.start_stage(4, input_path=ttl_path, shapes_path=shapes_path)

    def _on_run_wikidata_studio(
        self,
        input_path: Path,
        output_dir: Path,
        token: str,
        dry_run: bool,
        batch_mode: bool,
        approved_items: list,
    ) -> None:
        """Handle upload requested from the unified Wikidata Studio.

        The studio panel has already pre-built the list of approved
        WikidataItem objects. We hand them to the controller as the
        merged Stage 5 (was Stage 6).
        """
        mode = "dry run" if dry_run else "live upload"
        self._shared_log.append_line(
            f"Wikidata {mode}: {len(approved_items)} approved item(s) → {output_dir}"
        )
        self._controller.start_stage(
            5,
            input_path=input_path,
            output_dir=output_dir,
            token=token,
            dry_run=dry_run,
            batch_mode=batch_mode,
            approved_items=approved_items,
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
