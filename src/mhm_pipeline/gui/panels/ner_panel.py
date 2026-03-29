"""Stage 2 — Named Entity Recognition panel."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.entity_highlighter import Entity, EntityHighlighter
from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.percent_progress import PercentProgressWidget

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

        # Entity highlighter
        self._entity_highlighter = EntityHighlighter()
        layout.addWidget(self._entity_highlighter, stretch=2)

        # Role filter section
        self._setup_role_filter()
        layout.addLayout(self._role_filter_layout)

        # Results header with expand button
        results_header = QHBoxLayout()
        results_header.addWidget(QWidget())  # Spacer
        self._view_full_btn = QPushButton("View Full Results →")
        self._view_full_btn.clicked.connect(self._on_view_full_results)
        results_header.addWidget(self._view_full_btn)
        layout.addLayout(results_header)

        # log viewer
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

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
            role for role, checkbox in self._role_checkboxes.items()
            if checkbox.isChecked()
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

            # Handle both list and dict formats
            if isinstance(results, list):
                records = results
            elif isinstance(results, dict) and "records" in results:
                records = results["records"]
            else:
                records = [results]

            # Store the full records list
            self._current_records = records

            # Use display_records for all cases (handles single and multiple records)
            self._entity_highlighter.display_records(records)
            # Get entities from highlighter for View Full Results
            self._current_entities = self._entity_highlighter.get_entities()
            # Update role filter checkboxes based on loaded entities
            self._update_role_filter_checkboxes()
            total_entities = sum(len(r.get("entities", [])) for r in records if isinstance(r, dict))
            self._log_viewer.append_line(f"Loaded {len(records)} records with {total_entities} entities from {path}")

        except Exception as e:
            self._log_viewer.append_line(f"Error loading results: {e}")
            QMessageBox.critical(self, "Load Error", str(e))

    def display_entities(self, text: str, entities: list[Entity], records: list[dict] | None = None) -> None:
        """Display extracted entities in the highlighter.

        Args:
            text: The original note text.
            entities: List of extracted Entity objects.
            records: Optional list of full record dicts for display_records mode.
        """
        if records:
            self._entity_highlighter.display_records(records)
            # Get entities from highlighter after display_records
            self._current_entities = self._entity_highlighter.get_entities()
        else:
            self._entity_highlighter.load_entities(text, entities)
            self._current_entities = entities
        self._current_text = text
        self._current_records = records if records else []
        # Update role filter checkboxes
        self._update_role_filter_checkboxes()

    def _on_view_full_results(self) -> None:
        if not hasattr(self, "_current_entities") or not self._current_entities:
            QMessageBox.information(self, "No Results", "No NER results to display. Run Stage 2 or load results first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("NER Results")
        dialog.setMinimumSize(1000, 700)

        layout = QVBoxLayout(dialog)

        # Create larger entity highlighter
        from mhm_pipeline.gui.widgets.entity_highlighter import EntityHighlighter
        full_view = EntityHighlighter()

        # Use display_records if we have records, otherwise fall back to load_entities
        if hasattr(self, "_current_records") and self._current_records:
            full_view.display_records(self._current_records)
        else:
            full_view.load_entities(self._current_text, self._current_entities)
        layout.addWidget(full_view)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec()
