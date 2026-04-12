"""Stage 2 — Named Entity Recognition panel.

Runs up to 3 NER models: Person (notes/colophon), Provenance (MARC 561),
Contents (MARC 505). Results displayed in View + Edit tabs.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.entity_highlighter import (
    Entity,
    EntityHighlighter,
    build_entity_display_text,
    get_entity_colors,
    get_entity_icon,
)
from mhm_pipeline.gui.widgets.extraction_editor import ExtractionEditor
from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.percent_progress import PercentProgressWidget

_DEFAULT_MODEL = "alexgoldberg/hebrew-manuscript-joint-ner-v2"
_PREVIEW_MAX_ENTITIES = 8


class NerPanel(QWidget):
    """Panel for Stage 2: NER extraction from parsed JSON."""

    # (input_path, output_dir, model_path, batch_size, prov_model_path, cont_model_path)
    run_requested = pyqtSignal(Path, Path, str, int, str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        import os  # noqa: PLC0415

        layout = QVBoxLayout(self)

        # file selectors
        self._input_selector = FileSelector(
            "Input JSON:", mode="open", filter="JSON files (*.json)"
        )
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        layout.addWidget(self._input_selector)
        layout.addWidget(self._output_selector)

        # ── NER Models section ──────────────────────────────────────
        models_frame = QFrame()
        models_frame.setFrameShape(QFrame.Shape.StyledPanel)
        models_frame.setStyleSheet("QFrame { border: 1px solid #d1d5db; border-radius: 6px; }")
        models_layout = QVBoxLayout(models_frame)
        models_layout.setContentsMargins(10, 8, 10, 8)
        models_layout.setSpacing(4)

        models_header = QLabel("NER Models")
        models_header.setStyleSheet("font-weight: bold; font-size: 13px; border: none;")
        models_layout.addWidget(models_header)

        # Model 1: Person NER
        person_row = QHBoxLayout()
        self._person_check = QCheckBox("Person NER")
        self._person_check.setChecked(True)
        self._person_check.setToolTip("Extract persons + roles from notes and colophon")
        self._person_check.setStyleSheet("border: none;")
        person_row.addWidget(self._person_check)
        person_default = os.environ.get("MHM_BUNDLED_NER_MODEL", "")
        self._person_model_edit = QLineEdit(person_default if person_default else _DEFAULT_MODEL)
        self._person_model_edit.setStyleSheet(
            "border: 1px solid #9ca3af; border-radius: 3px; padding: 2px 4px;"
        )
        person_row.addWidget(self._person_model_edit)
        models_layout.addLayout(person_row)

        # Model 2: Provenance NER
        prov_row = QHBoxLayout()
        self._prov_check = QCheckBox("Provenance NER")
        self._prov_check.setChecked(True)
        self._prov_check.setToolTip("Extract OWNER, DATE, COLLECTION from MARC 561 (93.96% F1)")
        self._prov_check.setStyleSheet("border: none;")
        prov_row.addWidget(self._prov_check)
        prov_default = os.environ.get("MHM_BUNDLED_PROVENANCE_MODEL", "")
        self._prov_model_edit = QLineEdit(prov_default if prov_default else "(auto-detect)")
        self._prov_model_edit.setStyleSheet(
            "border: 1px solid #9ca3af; border-radius: 3px; padding: 2px 4px;"
        )
        prov_row.addWidget(self._prov_model_edit)
        models_layout.addLayout(prov_row)

        # Model 3: Contents NER
        cont_row = QHBoxLayout()
        self._cont_check = QCheckBox("Contents NER")
        self._cont_check.setChecked(True)
        self._cont_check.setToolTip("Extract WORK, FOLIO, WORK_AUTHOR from MARC 505 (99.99% F1)")
        self._cont_check.setStyleSheet("border: none;")
        cont_row.addWidget(self._cont_check)
        cont_default = os.environ.get("MHM_BUNDLED_CONTENTS_MODEL", "")
        self._cont_model_edit = QLineEdit(cont_default if cont_default else "(auto-detect)")
        self._cont_model_edit.setStyleSheet(
            "border: 1px solid #9ca3af; border-radius: 3px; padding: 2px 4px;"
        )
        cont_row.addWidget(self._cont_model_edit)
        models_layout.addLayout(cont_row)

        layout.addWidget(models_frame)

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
        self._run_btn = QPushButton("Extract Named Entities")
        self._run_btn.clicked.connect(self._on_run)

        # load results button
        self._load_btn = QPushButton("Load Results")
        self._load_btn.setToolTip("Load previously generated NER results JSON")
        self._load_btn.clicked.connect(self._on_load_results)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self._run_btn)
        btn_layout.addWidget(self._load_btn)
        layout.addLayout(btn_layout)

        # Progress bar
        self._progress = PercentProgressWidget()
        layout.addWidget(self._progress)

        # ── Results area: View tab + Edit tab ─────────────────────────
        self._results_tabs = QTabWidget()
        self._results_tabs.setDocumentMode(True)

        view_tab = QWidget()
        view_layout = QVBoxLayout(view_tab)
        view_layout.setContentsMargins(0, 4, 0, 0)
        self._build_results_preview(view_layout)
        self._results_tabs.addTab(view_tab, "View")

        self._extraction_editor = ExtractionEditor()
        self._results_tabs.addTab(self._extraction_editor, "Edit Entities")

        layout.addWidget(self._results_tabs, stretch=2)

        # log viewer
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

    def _build_results_preview(self, parent_layout: QVBoxLayout) -> None:
        """Build the compact entity results preview section."""
        preview_frame = QFrame()
        preview_frame.setFrameShape(QFrame.Shape.StyledPanel)
        preview_frame.setStyleSheet("QFrame { border: 1px solid #d1d5db; border-radius: 6px; }")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(10, 8, 10, 8)
        preview_layout.setSpacing(6)

        # Header row: title + "View All" button
        header = QHBoxLayout()
        self._preview_header = QLabel("No NER results loaded")
        self._preview_header.setStyleSheet("font-weight: bold; font-size: 13px; border: none;")
        header.addWidget(self._preview_header)
        header.addStretch()

        self._view_full_btn = QPushButton("View All Results")
        self._view_full_btn.setStyleSheet(
            "QPushButton { background-color: #3b82f6; color: white; "
            "padding: 5px 16px; border-radius: 4px; font-weight: bold; border: none; }"
            "QPushButton:hover { background-color: #2563eb; }"
            "QPushButton:disabled { background-color: #9ca3af; }"
        )
        self._view_full_btn.setEnabled(False)
        self._view_full_btn.clicked.connect(self._on_view_full_results)
        header.addWidget(self._view_full_btn)
        preview_layout.addLayout(header)

        # Small entity list preview (capped height)
        self._preview_list = QListWidget()
        self._preview_list.setMaximumHeight(200)
        self._preview_list.setAlternatingRowColors(True)
        self._preview_list.setStyleSheet("border: none;")
        preview_layout.addWidget(self._preview_list)

        # "...and N more" label
        self._more_label = QLabel("")
        self._more_label.setStyleSheet(
            "color: #6b7280; font-style: italic; font-size: 11px; border: none;"
        )
        self._more_label.hide()
        preview_layout.addWidget(self._more_label)

        # Role filter section (inside the preview frame)
        self._setup_role_filter()
        preview_layout.addLayout(self._role_filter_layout)

        parent_layout.addWidget(preview_frame)

        # Hidden full highlighter — only used inside the popup
        self._entity_highlighter = EntityHighlighter()
        self._entity_highlighter.hide()

    def _update_preview(self) -> None:
        """Refresh the compact preview list with current entities."""
        entities = getattr(self, "_current_entities", [])
        records = getattr(self, "_current_records", [])

        self._preview_list.clear()

        if not entities:
            self._preview_header.setText("No NER results loaded")
            self._view_full_btn.setEnabled(False)
            self._more_label.hide()
            return

        n_records = len(records) if records else 0
        n_entities = len(entities)
        header = (
            f"Entities Found ({n_records} records, {n_entities} entities)"
            if n_records
            else f"Entities Found ({n_entities} entities)"
        )
        self._preview_header.setText(header)
        self._view_full_btn.setEnabled(True)

        # Show first N entities as preview
        from PyQt6.QtGui import QColor

        shown = entities[:_PREVIEW_MAX_ENTITIES]
        for entity in shown:
            icon = get_entity_icon(
                entity,
                EntityHighlighter.ROLE_ICONS,
                EntityHighlighter.DEFAULT_ICON,
            )
            display_text = f"{icon} {build_entity_display_text(entity)}"
            bg_color, _ = get_entity_colors(
                entity,
                EntityHighlighter.ROLE_COLORS,
                EntityHighlighter.ENTITY_COLORS,
            )
            item = QListWidgetItem(display_text)
            item.setBackground(QColor(bg_color))
            self._preview_list.addItem(item)

        remaining = n_entities - len(shown)
        if remaining > 0:
            self._more_label.setText(f"  ...and {remaining} more entities")
            self._more_label.show()
        else:
            self._more_label.hide()

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        """Return the embedded log viewer."""
        return self._log_viewer

    @property
    def stage_progress(self) -> PercentProgressWidget:
        """Return the embedded progress widget."""
        return self._progress

    # ── Role Filter ───────────────────────────────────────────────────

    def _setup_role_filter(self) -> None:
        """Set up the role filter UI above the entity list."""
        self._role_filter_layout = QHBoxLayout()
        self._role_filter_layout.setSpacing(8)

        # Label
        filter_label = QLabel("Filter by role:")
        filter_label.setStyleSheet("font-weight: bold;")
        self._role_filter_layout.addWidget(filter_label)

        # Checkboxes for each role (will be populated dynamically)
        self._role_checkboxes: dict[str, QCheckBox] = {}
        self._role_filter_layout.addStretch()

        # Select All / Clear All buttons
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setToolTip("Show all roles")
        self._select_all_btn.clicked.connect(self._on_select_all_roles)
        self._role_filter_layout.addWidget(self._select_all_btn)

        self._clear_all_btn = QPushButton("Clear All")
        self._clear_all_btn.setToolTip("Hide all roles")
        self._clear_all_btn.clicked.connect(self._on_clear_all_roles)
        self._role_filter_layout.addWidget(self._clear_all_btn)

        # Initially disable until data is loaded
        self._set_role_filter_enabled(False)

    def _update_role_filter_checkboxes(self) -> None:
        """Update role filter checkboxes based on current entities."""
        # Clear existing checkboxes
        for checkbox in self._role_checkboxes.values():
            self._role_filter_layout.removeWidget(checkbox)
            checkbox.deleteLater()
        self._role_checkboxes.clear()

        # Get unique roles from current entities
        roles = self._entity_highlighter.get_all_roles()
        if not roles:
            self._set_role_filter_enabled(False)
            return

        # Add checkbox for each role
        for i, role in enumerate(roles):
            checkbox = QCheckBox(role.title())
            checkbox.setChecked(True)  # Default to checked
            checkbox.stateChanged.connect(self._on_role_filter_changed)
            self._role_checkboxes[role] = checkbox
            # Insert before the stretch and buttons
            self._role_filter_layout.insertWidget(i + 1, checkbox)

        self._set_role_filter_enabled(True)

    def _set_role_filter_enabled(self, enabled: bool) -> None:
        """Enable or disable role filter controls."""
        self._select_all_btn.setEnabled(enabled)
        self._clear_all_btn.setEnabled(enabled)
        for checkbox in self._role_checkboxes.values():
            checkbox.setEnabled(enabled)

    def _on_role_filter_changed(self) -> None:
        """Handle role filter checkbox state changes."""
        # Get selected roles
        selected_roles = {
            role for role, checkbox in self._role_checkboxes.items() if checkbox.isChecked()
        }
        # Apply filter
        self._entity_highlighter.filter_by_roles(selected_roles)

    def _on_select_all_roles(self) -> None:
        """Select all role checkboxes."""
        for checkbox in self._role_checkboxes.values():
            checkbox.setChecked(True)
        self._entity_highlighter.filter_by_roles(set())

    def _on_clear_all_roles(self) -> None:
        """Clear all role checkboxes."""
        for checkbox in self._role_checkboxes.values():
            checkbox.setChecked(False)
        # Get all roles to filter out
        all_roles = set(self._role_checkboxes.keys())
        self._entity_highlighter.filter_by_roles(all_roles)

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        input_path = self._input_selector.path
        output_path = self._output_selector.path

        if input_path is None:
            self._log_viewer.append_line("Error: select an input JSON file first.")
            return
        if output_path is None:
            output_path = input_path.parent
            self._output_selector.path = output_path

        person_model = (
            self._person_model_edit.text().strip() if self._person_check.isChecked() else ""
        )
        if self._person_check.isChecked() and not person_model:
            self._log_viewer.append_line("Error: Person NER model path is empty.")
            return

        prov_model = ""
        if self._prov_check.isChecked():
            prov_text = self._prov_model_edit.text().strip()
            prov_model = "" if prov_text == "(auto-detect)" else prov_text

        cont_model = ""
        if self._cont_check.isChecked():
            cont_text = self._cont_model_edit.text().strip()
            cont_model = "" if cont_text == "(auto-detect)" else cont_text

        enabled = []
        if self._person_check.isChecked():
            enabled.append("Person")
        if self._prov_check.isChecked():
            enabled.append("Provenance")
        if self._cont_check.isChecked():
            enabled.append("Contents")
        self._log_viewer.append_line(f"Running NER models: {', '.join(enabled)}")

        self.run_requested.emit(
            input_path,
            output_path,
            person_model,
            self._batch_spin.value(),
            prov_model,
            cont_model,
        )

    def _on_load_results(self) -> None:
        """Load and display previously generated NER results."""
        from PyQt6.QtWidgets import QFileDialog

        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Load NER Results",
            "",
            "JSON files (*.json)",
        )
        if not path_str:
            return

        path = Path(path_str)
        try:
            with open(path, encoding="utf-8") as f:
                results = json.load(f)

            self._store_results(results, output_path=path)
            total_entities = len(self._current_entities)
            n_records = len(self._current_records)
            self._log_viewer.append_line(
                f"Loaded {n_records} records with {total_entities} entities from {path}"
            )

        except Exception as e:
            self._log_viewer.append_line(f"Error loading results: {e}")
            QMessageBox.critical(self, "Load Error", str(e))

    def _store_results(self, results: object, output_path: Path | None = None) -> None:
        """Parse raw JSON results, store them, and refresh both tabs."""
        if isinstance(results, list):
            records = results
        elif isinstance(results, dict) and "records" in results:
            records = results["records"]
        else:
            records = [results]

        self._current_records = records

        # View tab
        self._entity_highlighter.display_records(records)
        self._current_entities = self._entity_highlighter.get_entities()

        # Edit tab
        self._extraction_editor.load_records(records, output_path)

        self._update_preview()
        self._update_role_filter_checkboxes()

    def display_entities(
        self,
        text: str,
        entities: list[Entity],
        records: list[dict] | None = None,
    ) -> None:
        """Display extracted entities in the preview.

        Args:
            text: The original note text.
            entities: List of extracted Entity objects.
            records: Optional list of full record dicts for display_records mode.
        """
        if records:
            self._entity_highlighter.display_records(records)
            self._current_entities = self._entity_highlighter.get_entities()
        else:
            self._entity_highlighter.load_entities(text, entities)
            self._current_entities = entities
        self._current_text = text
        self._current_records = records if records else []
        self._update_preview()
        self._update_role_filter_checkboxes()

    def _on_view_full_results(self) -> None:
        """Open a full-screen dialog showing all NER results."""
        if not getattr(self, "_current_entities", None):
            QMessageBox.information(
                self,
                "No Results",
                "No NER results to display. run NER Extraction or load results first.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"NER Results — {len(self._current_records)} records, "
            f"{len(self._current_entities)} entities"
        )

        # Full-screen dialog
        screen = self.screen()
        if screen:
            geom = screen.availableGeometry()
            dialog.resize(geom.width() * 9 // 10, geom.height() * 9 // 10)
        else:
            dialog.resize(1200, 800)

        dlg_layout = QVBoxLayout(dialog)

        # Full entity highlighter
        full_view = EntityHighlighter()
        if self._current_records:
            full_view.display_records(self._current_records)
        else:
            full_view.load_entities(
                getattr(self, "_current_text", ""),
                self._current_entities,
            )
        dlg_layout.addWidget(full_view, stretch=1)

        # Role filter inside popup
        filter_layout = QHBoxLayout()
        filter_label = QLabel("Filter by role:")
        filter_label.setStyleSheet("font-weight: bold;")
        filter_layout.addWidget(filter_label)
        popup_checkboxes: dict[str, QCheckBox] = {}
        for role in full_view.get_all_roles():
            cb = QCheckBox(role.title())
            cb.setChecked(True)
            popup_checkboxes[role] = cb
            filter_layout.addWidget(cb)
        filter_layout.addStretch()

        def _apply_popup_filter() -> None:
            selected = {r for r, cb in popup_checkboxes.items() if cb.isChecked()}
            full_view.filter_by_roles(selected)

        for cb in popup_checkboxes.values():
            cb.stateChanged.connect(_apply_popup_filter)
        dlg_layout.addLayout(filter_layout)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("QPushButton { padding: 6px 24px; font-size: 13px; }")
        close_btn.clicked.connect(dialog.accept)
        dlg_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

        dialog.exec()
