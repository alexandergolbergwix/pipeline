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
from mhm_pipeline.gui.widgets.dynamic_progress_bar import DynamicProgressBar
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

        # Where to write approved NER results — set by MainWindow when the
        # NER worker finishes, used by display_entities + Save.
        self._last_output_path: Path | None = None

        layout = QVBoxLayout(self)

        # file selectors
        self._input_selector = FileSelector(
            "Input JSON:", mode="open", filter="JSON files (*.json)"
        )
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        layout.addWidget(self._input_selector)
        layout.addWidget(self._output_selector)

        # ── NER Models — stored as instance fields, shown in popup ────
        # All four .pt classifiers are resolved through find_model_weights()
        # which checks PyInstaller-frozen + macOS .app bundle + dev-tree layouts.
        # The checkbox state must agree with the worker's resolver, otherwise
        # an auto-unchecked checkbox will skip a model that IS actually loadable.
        from mhm_pipeline.platform_.paths import find_model_weights  # noqa: PLC0415

        _prov_path = find_model_weights("provenance_ner_model.pt")
        _cont_path = find_model_weights("contents_ner_model.pt")
        _marc500_path = find_model_weights("marc500_classifier_model.pt")
        _genre_path = find_model_weights("genre_classifier_model.pt")

        person_default = os.environ.get("MHM_BUNDLED_NER_MODEL", "")
        self._person_check = QCheckBox()
        self._person_check.setChecked(True)
        self._person_model_edit = QLineEdit(person_default if person_default else _DEFAULT_MODEL)

        prov_default = os.environ.get("MHM_BUNDLED_PROVENANCE_MODEL", "")
        self._prov_check = QCheckBox()
        self._prov_check.setChecked(bool(_prov_path) or bool(prov_default))
        self._prov_model_edit = QLineEdit(
            prov_default if prov_default
            else str(_prov_path) if _prov_path
            else "(not found — provenance NER unavailable)"
        )

        cont_default = os.environ.get("MHM_BUNDLED_CONTENTS_MODEL", "")
        self._cont_check = QCheckBox()
        self._cont_check.setChecked(bool(_cont_path) or bool(cont_default))
        self._cont_model_edit = QLineEdit(
            cont_default if cont_default
            else str(_cont_path) if _cont_path
            else "(not found — contents NER unavailable)"
        )

        self._marc500_check = QCheckBox()
        self._marc500_check.setChecked(bool(_marc500_path))
        self._marc500_model_edit = QLineEdit(
            str(_marc500_path) if _marc500_path else "(not found — keyword fallback)"
        )

        self._genre_check = QCheckBox()
        self._genre_check.setChecked(bool(_genre_path))
        self._genre_model_edit = QLineEdit(
            str(_genre_path) if _genre_path else "(not found — MARC 655 only)"
        )

        # Compact summary row + configure button
        models_row = QHBoxLayout()
        self._models_summary = QLabel(self._build_models_summary())
        self._models_summary.setStyleSheet(f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;")
        models_row.addWidget(self._models_summary)
        models_row.addStretch()
        cfg_btn = QPushButton("Configure Models…")
        cfg_btn.setStyleSheet(theme.button_style('config'))
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

        # run button (primary — main action on this panel)
        self._run_btn = QPushButton("Extract Named Entities")
        self._run_btn.setStyleSheet(theme.button_style("primary"))
        self._run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_btn.clicked.connect(self._on_run)

        # load results button (cyan — import existing file)
        self._load_btn = QPushButton("Load Results")
        self._load_btn.setStyleSheet(theme.button_style("load"))
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.setToolTip("Load previously generated NER results JSON")
        self._load_btn.clicked.connect(self._on_load_results)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self._run_btn)
        btn_layout.addWidget(self._load_btn)
        layout.addLayout(btn_layout)

        # Progress bar — DynamicProgressBar (substep label + ETA + colored
        # chunk on success/failure). MainWindow forwards stage_progress /
        # stage_substep / stage_finished / stage_error from the controller.
        self._progress = DynamicProgressBar()
        layout.addWidget(self._progress)

        # ── Review banner (hidden until results load) ──────────────────
        self._review_banner = self._build_review_banner(
            "⚠  AI extraction may contain errors — review and approve each "
            "entity before continuing to the next stage.",
            edit_btn_text="Review & Edit →",
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
        """Open the model-configuration dialog using toggle switches.

        Switches beat checkboxes for binary "enable this model" settings —
        macOS HIG specifically recommends the switch control for on/off
        state. Toggles here are bound live (no Done button needed): each
        flip updates the underlying instance state immediately so the
        summary strip at the top of the panel reflects changes in real
        time.
        """
        from mhm_pipeline.gui.widgets.toggle_switch import ToggleSwitch  # noqa: PLC0415
        from mhm_pipeline.gui.widgets.glass_dialog import install_glass_backdrop  # noqa: PLC0415

        dlg = QDialog(self)
        dlg.setWindowTitle("NER Models")
        dlg.setMinimumWidth(460)
        # Per Rule 37, install the adaptive GraphBackdrop on every popup so
        # the dialog tracks light/dark mode along with the main window.
        # Layouts and widgets attach to the returned `_content`, never to
        # `dlg` directly (install_glass_backdrop owns `dlg`'s root layout).
        _content = install_glass_backdrop(dlg)
        dlg_layout = QVBoxLayout(_content)
        dlg_layout.setSpacing(theme.SPACE_MD)
        dlg_layout.setContentsMargins(
            theme.SPACE_XL, theme.SPACE_LG, theme.SPACE_XL, theme.SPACE_LG,
        )

        info = QLabel("All models are bundled with the application.")
        info.setStyleSheet(
            f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;"
        )
        dlg_layout.addWidget(info)

        def _make_row(
            title: str,
            desc: str,
            tooltip: str,
            source_check: QCheckBox,
        ) -> tuple[QHBoxLayout, "ToggleSwitch"]:
            """Build one switch row bound to *source_check* (QCheckBox).
            The switch mirrors the checkbox state bidirectionally, so the
            existing summary / run-button logic keeps working unchanged.
            """
            h = QHBoxLayout()
            h.setSpacing(theme.SPACE_MD)

            # Fixed-width switch on the LEFT so titles + descriptions align
            sw = ToggleSwitch()
            sw.setChecked(source_check.isChecked())
            sw.setToolTip(tooltip)
            sw.toggled.connect(lambda checked: source_check.setChecked(checked))
            source_check.toggled.connect(sw.setChecked)
            h.addWidget(sw)

            label_col = QVBoxLayout()
            label_col.setSpacing(2)
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(
                f"color: {theme.ui('text')}; font-size: {theme.FONT_BASE}px;"
                f" font-weight: {theme.WEIGHT_SEMIBOLD};"
            )
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(
                f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;"
            )
            desc_lbl.setWordWrap(True)
            label_col.addWidget(title_lbl)
            label_col.addWidget(desc_lbl)
            h.addLayout(label_col, stretch=1)
            return h, sw

        person_row, _ = _make_row(
            "Person NER",
            "Persons & roles — extracted from notes and colophon",
            "Extract persons + roles from notes and colophon",
            self._person_check,
        )
        dlg_layout.addLayout(person_row)

        prov_row, _ = _make_row(
            "Provenance NER",
            "Owners, dates, collections (F1 = 95.9%) — from MARC 561",
            "Extract OWNER, DATE, COLLECTION from MARC 561",
            self._prov_check,
        )
        dlg_layout.addLayout(prov_row)

        cont_row, _ = _make_row(
            "Contents NER",
            "Works, folios (F1 = 99.99%) — from MARC 505",
            "Extract WORK, FOLIO, WORK_AUTHOR from MARC 505",
            self._cont_check,
        )
        dlg_layout.addLayout(cont_row)

        marc500_desc = (
            "Colophon sentences (F1 = 96.4%) — routed to P1684"
            if self._marc500_check.isChecked()
            else "Not found on disk — keyword fallback active"
        )
        m500_row, _ = _make_row(
            "Colophon ML ⚡",
            marc500_desc,
            "Detect colophon sentences in MARC 500 notes → P1684",
            self._marc500_check,
        )
        dlg_layout.addLayout(m500_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            f"background: {theme.ui('border')};"
            f" max-height: 1px; border: none; margin: {theme.SPACE_SM}px 0;"
        )
        dlg_layout.addWidget(sep)

        stage3_lbl = QLabel("Stage 3 — RDF Building")
        stage3_lbl.setStyleSheet(theme.minicaps_label_style())
        dlg_layout.addWidget(stage3_lbl)

        genre_desc = (
            "Genre classification (F1 = 88%) — used for P136"
            if self._genre_check.isChecked()
            else "Not found on disk — MARC 655 only"
        )
        genre_row, _ = _make_row(
            "Genre ML ⚡",
            genre_desc,
            "Predict manuscript genre (P136) from title + MARC 500 notes",
            self._genre_check,
        )
        dlg_layout.addLayout(genre_row)

        dlg_layout.addSpacing(theme.SPACE_MD)

        close_btn = QPushButton("Done")
        close_btn.setStyleSheet(theme.button_style())
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dlg.accept)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(close_btn)
        dlg_layout.addLayout(footer)

        dlg.exec()

        # Switches are live-bound to the instance checkboxes, so nothing to
        # sync here — just refresh the summary strip to reflect any toggles.
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
        # Physics-based glass panel — frost + specular bloom + Fresnel rim +
        # cool body tint, painted via custom QPainter (see GlassPanel). The
        # graph-theory wallpaper behind it gives the specular layer something
        # to lens, which is what makes the glass read as actual glass rather
        # than a flat translucent rectangle.
        from mhm_pipeline.gui.widgets.glass_panel import GlassPanel  # noqa: PLC0415
        preview_frame = GlassPanel(radius=theme.RADIUS_2XL, variant="regular")
        theme.apply_drop_shadow(preview_frame, blur=28, offset_y=6)
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_MD, theme.SPACE_LG, theme.SPACE_MD,
        )
        preview_layout.setSpacing(theme.SPACE_SM)

        # Header row: title + "View All" button
        header = QHBoxLayout()
        self._preview_header = QLabel("No NER results loaded")
        self._preview_header.setStyleSheet(f"font-weight: bold; font-size: {theme.FONT_BASE}px; border: none;")
        header.addWidget(self._preview_header)
        header.addStretch()

        # Edit Entities is now the sole interaction surface for NER results —
        # the full-screen reader has been retired because the editor itself
        # offers filtering, viewing source text, and per-row approval.
        self._edit_entities_btn = QPushButton("Review & Edit Entities")
        self._edit_entities_btn.setStyleSheet(theme.button_style())
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

        # Wrap the preview frame in a scroll area so the filters + entity
        # list never get clipped when the panel is narrow or when many
        # source / type / role chips wrap to additional lines.
        from mhm_pipeline.gui.widgets.flow_layout import make_scrollable  # noqa: PLC0415
        scroll = make_scrollable(preview_frame, horizontal=True, vertical=True)
        parent_layout.addWidget(scroll)

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
    def stage_progress(self) -> DynamicProgressBar:
        """Return the embedded dynamic progress bar."""
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
        self._role_filter_layout.setContentsMargins(0, theme.SPACE_SM, 0, 0)
        self._role_filter_layout.setSpacing(theme.SPACE_SM)

        self._source_checkboxes: dict[str, QCheckBox] = {}
        self._type_checkboxes: dict[str, QCheckBox] = {}
        self._role_checkboxes: dict[str, QCheckBox] = {}

        self._source_row = self._build_filter_row("Sources", self._source_checkboxes,
                                                  on_all=self._on_all_sources,
                                                  on_none=self._on_none_sources)
        self._type_row = self._build_filter_row("Types", self._type_checkboxes,
                                                on_all=self._on_all_types,
                                                on_none=self._on_none_types)
        self._role_row = self._build_filter_row("Roles", self._role_checkboxes,
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
        """Build a single filter row (label + wrapping chip area + All/None).

        The chip area uses ``FlowLayout`` so a long list of filters wraps to
        the next line instead of overflowing the window width. The outer row
        uses a top-aligned QHBoxLayout so the mini-label and action buttons
        stay on the first line even after wrapping.
        """
        from mhm_pipeline.gui.widgets.flow_layout import FlowLayout  # noqa: PLC0415

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_MD)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)

        lbl = QLabel(label_text)
        lbl.setFixedWidth(64)
        lbl.setStyleSheet(theme.minicaps_label_style())
        # Align label with the first chip row
        lbl.setContentsMargins(0, 4, 0, 0)
        row.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignTop)

        # FlowLayout = chips wrap to new lines when the row runs out of width.
        # Hosted inside a plain QWidget so it participates in QHBoxLayout.
        from PyQt6.QtWidgets import QWidget as _Widget  # noqa: PLC0415
        chip_host = _Widget()
        chip_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        flow = FlowLayout(chip_host, margin=0,
                          h_spacing=theme.SPACE_SM,
                          v_spacing=theme.SPACE_SM)
        chip_host.setLayout(flow)
        row.addWidget(chip_host, stretch=1)

        # Keep the same sentinel so existing callers (setChecked loops,
        # _collect_selected) still work — expose the FlowLayout here.
        boxes_dict["__layout__"] = flow  # type: ignore[assignment]

        ghost = theme.ghost_button_style()
        all_btn = QPushButton("All")
        all_btn.setFixedWidth(46)
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.setStyleSheet(ghost)
        all_btn.setToolTip(f"Select all {label_text.lower().rstrip(':')}")
        all_btn.clicked.connect(on_all)  # type: ignore[arg-type]
        row.addWidget(all_btn)

        none_btn = QPushButton("None")
        none_btn.setFixedWidth(56)
        none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        none_btn.setStyleSheet(ghost)
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
        """Rebuild toggle-chip buttons in-place for a filter row.

        Chip pattern (Linear/Notion/Raycast) replaces ``QCheckBox`` to avoid
        the indicator-to-text spacing glitch in dense rows. Each chip is a
        pill-shaped ``QPushButton(checkable=True)`` whose ``isChecked()`` API
        is identical to a ``QCheckBox`` so existing callers work unchanged.
        """
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
        chip_qss = theme.filter_chip_style()
        for v in values:
            label = (label_for or {}).get(v, v.replace("_", " ").title())
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setChecked(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setStyleSheet(chip_qss)
            chip.toggled.connect(lambda _checked: self._on_filter_changed())
            boxes_dict[v] = chip  # type: ignore[assignment]
            layout.addWidget(chip)

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
        Uses the same toggle-chip pattern as the inline preview filter.
        """
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(0, theme.SPACE_SM, 0, 0)
        container_layout.setSpacing(theme.SPACE_SM)

        boxes = {
            "source": {},  # type: ignore[var-annotated]
            "type": {},
            "role": {},
        }
        chip_qss = theme.filter_chip_style()
        ghost_qss = theme.ghost_button_style()

        def apply() -> None:
            sel = {dim: {k for k, cb in boxes[dim].items() if cb.isChecked()} for dim in boxes}
            target.apply_filters(sel["source"], sel["type"], sel["role"])

        def build_row(label_text: str, values: list[str], dim: str,
                      label_for: dict[str, str] | None = None) -> QHBoxLayout:
            from mhm_pipeline.gui.widgets.flow_layout import FlowLayout  # noqa: PLC0415
            from PyQt6.QtWidgets import QWidget as _Widget  # noqa: PLC0415

            row = QHBoxLayout()
            row.setSpacing(theme.SPACE_MD)
            row.setAlignment(Qt.AlignmentFlag.AlignTop)

            lbl = QLabel(label_text)
            lbl.setFixedWidth(64)
            lbl.setStyleSheet(theme.minicaps_label_style())
            lbl.setContentsMargins(0, 4, 0, 0)
            row.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignTop)

            chip_host = _Widget()
            chip_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            chip_layout = FlowLayout(chip_host, margin=0,
                                     h_spacing=theme.SPACE_SM,
                                     v_spacing=theme.SPACE_SM)
            chip_host.setLayout(chip_layout)
            row.addWidget(chip_host, stretch=1)

            for v in values:
                display = (label_for or {}).get(v, v.replace("_", " ").title())
                chip = QPushButton(display)
                chip.setCheckable(True)
                chip.setChecked(True)
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.setStyleSheet(chip_qss)
                chip.toggled.connect(lambda _checked: apply())
                boxes[dim][v] = chip
                chip_layout.addWidget(chip)

            all_btn = QPushButton("All")
            all_btn.setFixedWidth(46)
            all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            all_btn.setStyleSheet(ghost_qss)
            all_btn.clicked.connect(
                lambda: [c.setChecked(True) for c in boxes[dim].values()]
            )
            row.addWidget(all_btn)
            none_btn = QPushButton("None")
            none_btn.setFixedWidth(56)
            none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            none_btn.setStyleSheet(ghost_qss)
            none_btn.clicked.connect(
                lambda: [c.setChecked(False) for c in boxes[dim].values()]
            )
            row.addWidget(none_btn)
            return row

        container_layout.addLayout(build_row("Sources", target.get_all_sources(),
                                             "source", self._SOURCE_LABELS))
        container_layout.addLayout(build_row("Types", target.get_all_types(), "type"))
        container_layout.addLayout(build_row("Roles", target.get_all_roles(), "role"))
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
        if self._marc500_check.isChecked():
            enabled.append("Colophon ML")
        if self._genre_check.isChecked():
            enabled.append("Genre ML (Stage 3)")
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

            self._store_results(results, output_path=path, auto_review=True)
            total_entities = len(self._current_entities)
            n_records = len(self._current_records)
            self._log_viewer.append_line(
                f"Loaded {n_records} records with {total_entities} entities from {path}"
            )

        except Exception as e:
            self._log_viewer.append_line(f"Error loading results: {e}")
            QMessageBox.critical(self, "Load Error", str(e))

    def _store_results(
        self,
        results: object,
        output_path: Path | None = None,
        *,
        auto_review: bool = False,
    ) -> None:
        """Parse raw JSON results, store them, and refresh both tabs.

        Args:
            results: The NER result records (list of dicts).
            output_path: Destination path for Save; retained by the editor.
            auto_review: If True, open the Review & Edit dialog straight
                after loading. Used right after NER extraction finishes and
                after the user clicks "Load Results" — approval is a
                mandatory step in the pipeline flow, so we nudge the user
                to it rather than leaving them on the compact preview.
        """
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

        if auto_review and self._current_entities:
            # Open the review dialog after the current event loop tick so
            # the underlying panel has finished laying out first.
            from PyQt6.QtCore import QTimer  # noqa: PLC0415
            QTimer.singleShot(100, self._on_edit_entities_popup)

    def display_entities(
        self,
        text: str,
        entities: list[Entity],
        records: list[dict] | None = None,
    ) -> None:
        """Display extracted entities in the preview.

        Called by ``MainWindow`` after the NER worker finishes. Auto-opens
        the Review & Edit dialog because approval is a mandatory gate
        before downstream stages.

        Args:
            text: The original note text.
            entities: List of extracted Entity objects.
            records: Optional list of full record dicts for display_records mode.
        """
        if records:
            self._entity_highlighter.display_records(records)
            self._current_entities = self._entity_highlighter.get_entities()
            # Also hydrate the editable model so a review-dialog open is ready
            self._extraction_editor.load_records(records, self._last_output_path)
        else:
            self._entity_highlighter.load_entities(text, entities)
            self._current_entities = entities
        self._current_text = text
        self._current_records = records if records else []
        self._update_preview()
        self._update_role_filter_checkboxes()

        if self._current_entities:
            from PyQt6.QtCore import QTimer  # noqa: PLC0415
            QTimer.singleShot(150, self._on_edit_entities_popup)

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

        # Filter panel bound to the editor, wrapped for overflow safety
        from mhm_pipeline.gui.widgets.flow_layout import make_scrollable  # noqa: PLC0415
        filter_panel = self._build_popup_filter_panel(self._extraction_editor)
        filter_container = QFrame()
        filter_container.setFrameShape(QFrame.Shape.NoFrame)
        filter_container.setLayout(filter_panel)
        filter_container.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_SM, theme.SPACE_LG, theme.SPACE_SM,
        )
        filter_scroll = make_scrollable(filter_container, horizontal=False, vertical=True)
        filter_scroll.setMaximumHeight(160)
        dlg_layout.addWidget(filter_scroll)

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
