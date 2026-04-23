"""Stage 2 — Named Entity Recognition panel.

Runs up to 4 NER models: Person (notes/colophon), Provenance (MARC 561),
Contents (MARC 505), Colophon ML (MARC 500). The Genre classifier runs later
in Stage 3 (RDF building) and is shown here as a status indicator only.
Results open in full-screen popups via View / Edit buttons.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
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

        # ── NER Models — stored as instance fields, shown in popup ────
        person_default = os.environ.get("MHM_BUNDLED_NER_MODEL", "")
        self._person_check = QCheckBox()
        self._person_check.setChecked(True)
        self._person_model_edit = QLineEdit(person_default if person_default else _DEFAULT_MODEL)

        prov_default = os.environ.get("MHM_BUNDLED_PROVENANCE_MODEL", "")
        self._prov_check = QCheckBox()
        self._prov_check.setChecked(True)
        self._prov_model_edit = QLineEdit(prov_default if prov_default else "(auto-detect)")

        cont_default = os.environ.get("MHM_BUNDLED_CONTENTS_MODEL", "")
        self._cont_check = QCheckBox()
        self._cont_check.setChecked(True)
        self._cont_model_edit = QLineEdit(cont_default if cont_default else "(auto-detect)")

        _marc500_path = Path(__file__).resolve().parents[4] / "ner" / "marc500_classifier_model.pt"
        self._marc500_check = QCheckBox()
        self._marc500_check.setChecked(_marc500_path.exists())
        self._marc500_model_edit = QLineEdit(
            str(_marc500_path) if _marc500_path.exists() else "(not found — keyword fallback)"
        )

        _genre_path = Path(__file__).resolve().parents[4] / "ner" / "genre_classifier_model.pt"
        self._genre_check = QCheckBox()
        self._genre_check.setChecked(_genre_path.exists())
        self._genre_model_edit = QLineEdit(
            str(_genre_path) if _genre_path.exists() else "(not found — MARC 655 only)"
        )

        # Compact summary row + configure button
        models_row = QHBoxLayout()
        self._models_summary = QLabel(self._build_models_summary())
        self._models_summary.setStyleSheet(f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;")
        models_row.addWidget(self._models_summary)
        models_row.addStretch()
        cfg_btn = QPushButton("Configure Models…")
        cfg_btn.setFixedWidth(150)
        cfg_btn.clicked.connect(self._open_models_dialog)
        models_row.addWidget(cfg_btn)
        layout.addLayout(models_row)

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

        # ── Review banner (hidden until results load) ──────────────────
        self._review_banner = self._build_review_banner(
            "⚠  AI extraction may contain errors — please review and correct "
            "entities before continuing to the next stage.",
            edit_btn_text="Edit Entities →",
            on_edit=self._on_edit_entities_popup,
        )
        layout.addWidget(self._review_banner)

        # ── Compact results preview (entity list + action buttons) ────
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)
        self._build_results_preview(preview_layout)
        layout.addWidget(preview_container, stretch=2)

        # ExtractionEditor kept off-screen; opened in popup on demand
        self._extraction_editor = ExtractionEditor()
        self._extraction_editor.hide()

        # log viewer
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

    # ── Models popup ──────────────────────────────────────────────────

    def _build_models_summary(self) -> str:
        active = []
        if self._person_check.isChecked():
            active.append("Person NER")
        if self._prov_check.isChecked():
            active.append("Provenance NER")
        if self._cont_check.isChecked():
            active.append("Contents NER")
        if self._marc500_check.isChecked():
            active.append("Colophon ML")
        if self._genre_check.isChecked():
            active.append("Genre ML")
        return "Models: " + "  ·  ".join(active) if active else "No models selected"

    def _open_models_dialog(self) -> None:
        # Use dialog-local checkboxes to avoid Qt re-parenting the instance
        # attributes to the dialog (which would delete them when the dialog closes).
        dlg = QDialog(self)
        dlg.setWindowTitle("NER Models")
        dlg.setMinimumWidth(360)
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.setSpacing(12)
        dlg_layout.setContentsMargins(20, 16, 20, 16)

        info = QLabel("All models are bundled with the application.")
        info.setStyleSheet(f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;")
        dlg_layout.addWidget(info)

        # Create dialog-local copies — state is synced back on close
        person_cb = QCheckBox("Person NER")
        person_cb.setChecked(self._person_check.isChecked())
        prov_cb = QCheckBox("Provenance NER")
        prov_cb.setChecked(self._prov_check.isChecked())
        cont_cb = QCheckBox("Contents NER")
        cont_cb.setChecked(self._cont_check.isChecked())
        marc500_cb = QCheckBox("Colophon ML ⚡")
        marc500_cb.setChecked(self._marc500_check.isChecked())
        genre_cb = QCheckBox("Genre ML ⚡")
        genre_cb.setChecked(self._genre_check.isChecked())

        def _row(check: QCheckBox, desc: str, tooltip: str) -> QHBoxLayout:
            check.setToolTip(tooltip)
            h = QHBoxLayout()
            h.addWidget(check)
            lbl = QLabel(desc)
            lbl.setStyleSheet(f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;")
            h.addWidget(lbl)
            h.addStretch()
            return h

        dlg_layout.addLayout(_row(person_cb, "persons & roles",
                                  "Extract persons + roles from notes and colophon"))
        dlg_layout.addLayout(_row(prov_cb, "owners, dates, collections  (F1=95.9%)",
                                  "Extract OWNER, DATE, COLLECTION from MARC 561"))
        dlg_layout.addLayout(_row(cont_cb, "works, folios  (F1=99.99%)",
                                  "Extract WORK, FOLIO, WORK_AUTHOR from MARC 505"))
        dlg_layout.addLayout(_row(
            marc500_cb,
            "colophon detection  (F1=96.4%)" if self._marc500_check.isChecked() else "not found — keyword fallback",
            "Detect colophon sentences in MARC 500 notes → P1684",
        ))

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {theme.ui('border')};")
        dlg_layout.addWidget(sep)

        stage3_lbl = QLabel("Stage 3 — RDF Building")
        stage3_lbl.setStyleSheet(
            f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px; font-weight: bold;"
        )
        dlg_layout.addWidget(stage3_lbl)
        dlg_layout.addLayout(_row(
            genre_cb,
            "genre classification  (F1=88%)  — used for P136" if self._genre_check.isChecked() else "not found — MARC 655 only",
            "Predict manuscript genre (P136) from title + MARC 500 notes",
        ))

        close_btn = QPushButton("Done")
        close_btn.clicked.connect(dlg.accept)
        dlg_layout.addWidget(close_btn)

        dlg.exec()

        # Sync dialog state back to instance attributes
        self._person_check.setChecked(person_cb.isChecked())
        self._prov_check.setChecked(prov_cb.isChecked())
        self._cont_check.setChecked(cont_cb.isChecked())
        self._marc500_check.setChecked(marc500_cb.isChecked())
        self._genre_check.setChecked(genre_cb.isChecked())
        self._models_summary.setText(self._build_models_summary())

    # ── Review banner helper ───────────────────────────────────────────

    def _build_review_banner(
        self,
        message: str,
        *,
        edit_btn_text: str | None = None,
        on_edit: object = None,
    ) -> QFrame:
        """Return an amber warning banner (hidden by default)."""
        banner = QFrame()
        banner.setFrameShape(QFrame.Shape.StyledPanel)
        banner.setStyleSheet(theme.warning_banner_style())
        row = QHBoxLayout(banner)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)

        lbl = QLabel(message)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"border: none; color: {theme.warning_text_color()}; font-size: {theme.FONT_MD}px;")
        row.addWidget(lbl, stretch=1)

        if edit_btn_text and on_edit:
            edit_btn = QPushButton(edit_btn_text)
            edit_btn.setStyleSheet(theme.warning_btn_style())
            edit_btn.clicked.connect(on_edit)
            row.addWidget(edit_btn)

        dismiss = QPushButton("✕")
        dismiss.setFixedSize(22, 22)
        dismiss.setToolTip("Dismiss")
        dismiss.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {theme.warning_text_color()}; font-size: {theme.FONT_BASE}px; }}"
            f"QPushButton:hover {{ color: {theme.ui('no_match')}; }}"
        )
        dismiss.clicked.connect(banner.hide)
        row.addWidget(dismiss)

        banner.hide()
        return banner

    def show_review_banner(self) -> None:
        """Show the review banner (called by main window after results load)."""
        self._review_banner.show()

    def _build_results_preview(self, parent_layout: QVBoxLayout) -> None:
        """Build the compact entity results preview section."""
        preview_frame = QFrame()
        preview_frame.setFrameShape(QFrame.Shape.StyledPanel)
        preview_frame.setStyleSheet(theme.frame_style())
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(10, 8, 10, 8)
        preview_layout.setSpacing(6)

        # Header row: title + "View All" button
        header = QHBoxLayout()
        self._preview_header = QLabel("No NER results loaded")
        self._preview_header.setStyleSheet(f"font-weight: bold; font-size: {theme.FONT_BASE}px; border: none;")
        header.addWidget(self._preview_header)
        header.addStretch()

        self._view_full_btn = QPushButton("View Results")
        self._view_full_btn.setStyleSheet(theme.button_style())
        self._view_full_btn.setEnabled(False)
        self._view_full_btn.clicked.connect(self._on_view_full_results)
        header.addWidget(self._view_full_btn)

        self._edit_entities_btn = QPushButton("Edit Entities")
        self._edit_entities_btn.setStyleSheet(theme.warning_btn_style())
        self._edit_entities_btn.setEnabled(False)
        self._edit_entities_btn.clicked.connect(self._on_edit_entities_popup)
        header.addWidget(self._edit_entities_btn)
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
            f"color: {theme.ui('subtext')}; font-style: italic; font-size: {theme.FONT_SM}px; border: none;"
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
            self._edit_entities_btn.setEnabled(False)
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
        self._edit_entities_btn.setEnabled(True)

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

    # ── Filter panel (Sources · Types · Roles) ──────────────────────

    _SOURCE_LABELS: dict[str, str] = {
        "person_ner": "Person NER",
        "provenance_ner": "Provenance NER",
        "contents_ner": "Contents NER",
    }

    def _setup_role_filter(self) -> None:
        """Build a three-row filter panel: Sources, Entity Types, Person Roles."""
        # Free-standing QVBoxLayout — attached to a parent layout by caller
        self._role_filter_layout = QVBoxLayout()
        self._role_filter_layout.setContentsMargins(0, 0, 0, 0)
        self._role_filter_layout.setSpacing(4)

        self._source_checkboxes: dict[str, QCheckBox] = {}
        self._type_checkboxes: dict[str, QCheckBox] = {}
        self._role_checkboxes: dict[str, QCheckBox] = {}

        self._source_row = self._build_filter_row("Sources:", self._source_checkboxes,
                                                  on_all=self._on_all_sources,
                                                  on_none=self._on_none_sources)
        self._type_row = self._build_filter_row("Types:", self._type_checkboxes,
                                                on_all=self._on_all_types,
                                                on_none=self._on_none_types)
        self._role_row = self._build_filter_row("Roles:", self._role_checkboxes,
                                                on_all=self._on_all_roles,
                                                on_none=self._on_none_roles)
        self._role_filter_layout.addLayout(self._source_row)
        self._role_filter_layout.addLayout(self._type_row)
        self._role_filter_layout.addLayout(self._role_row)

        # Keep the _role_filter_layout reference as the container layout
        # (name preserved for compatibility with _build_results_preview which
        # calls preview_layout.addLayout(self._role_filter_layout))

    def _build_filter_row(
        self,
        label_text: str,
        boxes_dict: dict[str, QCheckBox],
        *,
        on_all: object,
        on_none: object,
    ) -> QHBoxLayout:
        """Build a single filter row (label + checkboxes area + All/None buttons)."""
        row = QHBoxLayout()
        row.setSpacing(6)

        lbl = QLabel(label_text)
        lbl.setFixedWidth(65)
        lbl.setStyleSheet(f"font-weight: bold; font-size: {theme.FONT_SM}px;")
        row.addWidget(lbl)

        # Dedicated stretch holder for the dynamic checkboxes
        boxes_layout = QHBoxLayout()
        boxes_layout.setSpacing(6)
        row.addLayout(boxes_layout, stretch=1)

        # Store the layout so we can add checkboxes later
        boxes_dict["__layout__"] = boxes_layout  # type: ignore[assignment]

        all_btn = QPushButton("All")
        all_btn.setFixedWidth(44)
        all_btn.setToolTip(f"Select all {label_text.lower().rstrip(':')}")
        all_btn.clicked.connect(on_all)  # type: ignore[arg-type]
        row.addWidget(all_btn)

        none_btn = QPushButton("None")
        none_btn.setFixedWidth(54)
        none_btn.setToolTip(f"Clear all {label_text.lower().rstrip(':')}")
        none_btn.clicked.connect(on_none)  # type: ignore[arg-type]
        row.addWidget(none_btn)
        return row

    def _refresh_filter_row(
        self,
        boxes_dict: dict[str, QCheckBox],
        values: list[str],
        label_for: dict[str, str] | None = None,
    ) -> None:
        """Rebuild checkboxes in-place for a filter row based on *values*."""
        layout = boxes_dict.get("__layout__")  # type: ignore[assignment]
        # Remove old (non-layout) entries
        for key in list(boxes_dict.keys()):
            if key == "__layout__":
                continue
            cb = boxes_dict.pop(key)
            if layout is not None:
                layout.removeWidget(cb)
            cb.deleteLater()

        if layout is None:
            return
        for v in values:
            label = (label_for or {}).get(v, v.replace("_", " ").title())
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.stateChanged.connect(self._on_filter_changed)
            boxes_dict[v] = cb
            layout.addWidget(cb)

    def _update_role_filter_checkboxes(self) -> None:
        """Populate all three filter rows from the current entity set."""
        sources = self._entity_highlighter.get_all_sources()
        types_ = self._entity_highlighter.get_all_types()
        roles = self._entity_highlighter.get_all_roles()

        self._refresh_filter_row(self._source_checkboxes, sources,
                                 label_for=self._SOURCE_LABELS)
        self._refresh_filter_row(self._type_checkboxes, types_)
        self._refresh_filter_row(self._role_checkboxes, roles)

    def _collect_selected(self, boxes_dict: dict[str, QCheckBox]) -> set[str]:
        return {
            k for k, cb in boxes_dict.items()
            if k != "__layout__" and cb.isChecked()
        }

    def _on_filter_changed(self) -> None:
        """Re-apply Source/Type/Role filters after any checkbox toggle."""
        sources = self._collect_selected(self._source_checkboxes)
        types_ = self._collect_selected(self._type_checkboxes)
        roles = self._collect_selected(self._role_checkboxes)
        self._entity_highlighter.apply_filters(sources, types_, roles)
        # Also mirror into the edit table
        self._extraction_editor.apply_filters(sources, types_, roles)

    def _build_popup_filter_panel(self, target: Any) -> QVBoxLayout:
        """Build a self-contained 3-row filter panel bound to *target* widget.

        ``target`` must implement ``get_all_sources()``, ``get_all_types()``,
        ``get_all_roles()`` and ``apply_filters(sources, types, roles)``.
        """
        container_layout = QVBoxLayout()
        container_layout.setSpacing(4)

        boxes = {
            "source": {},  # type: ignore[var-annotated]
            "type": {},
            "role": {},
        }

        def apply() -> None:
            sel = {dim: {k for k, cb in boxes[dim].items() if cb.isChecked()} for dim in boxes}
            target.apply_filters(sel["source"], sel["type"], sel["role"])

        def build_row(label_text: str, values: list[str], dim: str,
                      label_for: dict[str, str] | None = None) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(label_text)
            lbl.setFixedWidth(65)
            lbl.setStyleSheet(f"font-weight: bold; font-size: {theme.FONT_SM}px;")
            row.addWidget(lbl)
            for v in values:
                display = (label_for or {}).get(v, v.replace("_", " ").title())
                cb = QCheckBox(display)
                cb.setChecked(True)
                cb.stateChanged.connect(apply)
                boxes[dim][v] = cb
                row.addWidget(cb)
            row.addStretch()

            all_btn = QPushButton("All")
            all_btn.setFixedWidth(44)
            all_btn.clicked.connect(lambda: [c.setChecked(True) for c in boxes[dim].values()])
            row.addWidget(all_btn)
            none_btn = QPushButton("None")
            none_btn.setFixedWidth(54)
            none_btn.clicked.connect(lambda: [c.setChecked(False) for c in boxes[dim].values()])
            row.addWidget(none_btn)
            return row

        container_layout.addLayout(build_row("Sources:", target.get_all_sources(),
                                             "source", self._SOURCE_LABELS))
        container_layout.addLayout(build_row("Types:", target.get_all_types(), "type"))
        container_layout.addLayout(build_row("Roles:", target.get_all_roles(), "role"))
        return container_layout

    def _on_all_sources(self) -> None:
        for k, cb in self._source_checkboxes.items():
            if k != "__layout__":
                cb.setChecked(True)

    def _on_none_sources(self) -> None:
        for k, cb in self._source_checkboxes.items():
            if k != "__layout__":
                cb.setChecked(False)

    def _on_all_types(self) -> None:
        for k, cb in self._type_checkboxes.items():
            if k != "__layout__":
                cb.setChecked(True)

    def _on_none_types(self) -> None:
        for k, cb in self._type_checkboxes.items():
            if k != "__layout__":
                cb.setChecked(False)

    def _on_all_roles(self) -> None:
        for k, cb in self._role_checkboxes.items():
            if k != "__layout__":
                cb.setChecked(True)

    def _on_none_roles(self) -> None:
        for k, cb in self._role_checkboxes.items():
            if k != "__layout__":
                cb.setChecked(False)

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

        # 3-row filter inside popup (Sources · Types · Roles)
        filter_panel = self._build_popup_filter_panel(full_view)
        dlg_layout.addLayout(filter_panel)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("QPushButton { padding: 6px 24px; font-size: 13px; }")
        close_btn.clicked.connect(dialog.accept)
        dlg_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

        dialog.exec()

    def _on_edit_entities_popup(self) -> None:
        """Open the ExtractionEditor in a full-screen dialog."""
        if not getattr(self, "_current_records", None) and not getattr(self, "_current_entities", None):
            QMessageBox.information(
                self,
                "No Results",
                "No NER results to edit. Run extraction or load results first.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"Edit Entities — {len(self._current_records)} records, "
            f"{len(self._current_entities)} entities"
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

        # Re-use the extraction editor in the dialog temporarily
        # (safe: dialog is modal, editor is re-parented back after close)
        self._extraction_editor.setParent(dialog)  # type: ignore[arg-type]
        self._extraction_editor.show()
        dlg_layout.addWidget(self._extraction_editor, stretch=1)

        # Filter panel bound to the editor
        filter_panel = self._build_popup_filter_panel(self._extraction_editor)
        filter_container = QFrame()
        filter_container.setFrameShape(QFrame.Shape.NoFrame)
        filter_container.setLayout(filter_panel)
        filter_container.setContentsMargins(12, 8, 12, 8)
        dlg_layout.addWidget(filter_container)

        close_bar = QHBoxLayout()
        close_bar.setContentsMargins(8, 6, 8, 6)
        close_bar.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(f"QPushButton {{ padding: 6px 24px; font-size: {theme.FONT_BASE}px; }}")
        close_btn.clicked.connect(dialog.accept)
        close_bar.addWidget(close_btn)
        dlg_layout.addLayout(close_bar)

        dialog.exec()

        # Re-parent editor back so it remains usable for the next popup
        self._extraction_editor.setParent(self)  # type: ignore[arg-type]
        self._extraction_editor.hide()
