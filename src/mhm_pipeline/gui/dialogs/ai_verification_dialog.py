"""4-tab live AI-verification dialog (Rule 52).

Replaces the post-run-only ``EvalAgentReportDialog``. Opens BEFORE the
worker starts so the user sees live progress + log streaming, then
populates the remaining three tabs from the eval-agent's per-run
artefacts on ``finished``.

Audience
--------
Medieval Hebrew manuscript researchers and ontology specialists — not
engineers. Every visible label is plain English; every technical
identifier (record_id, evaluator_id, cache_key, schema_version,
tokens, raw JSON) is either hidden, renamed, or tucked behind the
**Show advanced details** disclosure in the footer.

The dialog can also be opened in **post-mortem** mode: pass
``worker=None`` and ``run_dir=<path>`` to inspect a previously
completed run. The Working… tab then shows a "this verification has
already finished" message and the other tabs load from disk
immediately.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.controller.approval_store import ApprovalStore
from mhm_pipeline.gui import theme
from mhm_pipeline.gui.dialogs.widgets.agent_system_diagram import AgentSystemDiagram
from mhm_pipeline.gui.dialogs.widgets.friendly_copy import (
    _FRIENDLY_INPUT_KEYS,
    compose_headline,
    humanise_evaluator,
    humanise_log_line,
    humanise_model,
    humanise_report_md,
    humanise_verdict,
)
from mhm_pipeline.gui.dialogs.widgets.json_tree_viewer import JsonTreeViewer
from mhm_pipeline.gui.dialogs.widgets.marc_record_popup import (
    load_marc_index,
    open_marc_popup,
)
from mhm_pipeline.gui.dialogs.widgets.status_pill import StatusPill
from mhm_pipeline.gui.dialogs.widgets.verdict_table_model import (
    VerdictTableModel,
)
from mhm_pipeline.gui.widgets.column_filter_popup import (
    install_column_filters,
)
from mhm_pipeline.gui.widgets.dynamic_progress_bar import (
    DynamicProgressBar,
    connect_progress_signals,
)
from mhm_pipeline.gui.widgets.glass_dialog import (
    GlassDialog,
    glass_panel_style,
    glass_tab_style,
    glass_table_style,
)
from mhm_pipeline.gui.widgets.log_viewer import LogViewer

logger = logging.getLogger(__name__)


# ── Model-execution-failure detection ──────────────────────────────

_MODEL_ERROR_RE = re.compile(
    r"(not found|INVALID_ARGUMENT|PERMISSION_DENIED|quota|RESOURCE_EXHAUSTED|"
    r"HTTP [45]\d\d|model .* (?:is not|not supported|does not exist))",
    re.IGNORECASE,
)


def looks_like_model_error(line: str) -> bool:
    """Return True when a streamed log line signals a model/API failure."""
    return bool(_MODEL_ERROR_RE.search(line or ""))


# ── Filter proxy that implements ColumnFilteredProxy ────────────────


class VerdictFilterProxy(QSortFilterProxyModel):
    """``QSortFilterProxyModel`` that satisfies ``ColumnFilteredProxy``.

    The right-click column-filter popup needs ``set_column_filter`` /
    ``column_filter`` / ``clear_all_column_filters``. The proxy also
    powers the free-text search box above the table.
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._column_filters: dict[int, set[str]] = {}
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        # Search across every column by default.
        self.setFilterKeyColumn(-1)

    def set_column_filter(self, column: int, values: set[str]) -> None:
        if values:
            self._column_filters[column] = set(values)
        else:
            self._column_filters.pop(column, None)
        self.invalidateFilter()

    def clear_all_column_filters(self) -> None:
        self._column_filters.clear()
        self.invalidateFilter()

    def column_filter(self, column: int) -> set[str]:
        return set(self._column_filters.get(column, set()))

    def filterAcceptsRow(  # noqa: N802 — Qt-style camelCase
        self,
        source_row: int,
        source_parent: QModelIndex,
    ) -> bool:
        # Per-column filters AND with the inherited free-text filter.
        m = self.sourceModel()
        if m is not None:
            for column, allowed in self._column_filters.items():
                index = m.index(source_row, column, source_parent)
                value = str(m.data(index, int(Qt.ItemDataRole.DisplayRole)) or "")
                if value not in allowed:
                    return False
        return super().filterAcceptsRow(source_row, source_parent)


# ── Delegate that paints StatusPill into verdict columns ────────────


class _PillDelegate(QStyledItemDelegate):
    """Render the verdict + per-aspect columns as ``StatusPill`` widgets.

    Uses ``VerdictTableModel.StatusRole`` to read the raw status and
    paints a small ``StatusPill`` (created on demand and cached) over
    each cell. Cells without a status fall through to default text
    rendering.
    """

    def __init__(self, parent: Any = None, *, glyph_only: bool = False) -> None:
        super().__init__(parent)
        self._glyph_only = bool(glyph_only)
        # Lazy: build a single pill widget and re-style it for each paint.
        self._pill = StatusPill("", glyph_only=glyph_only)

    def paint(
        self,
        painter: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        status = str(index.data(int(VerdictTableModel.StatusRole)) or "")
        if not status:
            super().paint(painter, option, index)
            return
        self._pill.setStatus(status)
        self._pill.resize(option.rect.size())
        painter.save()
        painter.translate(option.rect.topLeft())
        self._pill.render(painter)
        painter.restore()


# ── The dialog ──────────────────────────────────────────────────────


class AiVerificationDialog(GlassDialog):
    """4-tab live verification dialog. See module docstring."""

    # Public signals the parent window or the test harness can hook.
    finished_loaded = pyqtSignal(Path)         # emitted after refresh()
    advanced_toggled = pyqtSignal(bool)        # emitted on footer toggle

    def __init__(
        self,
        pipeline_output_dir: Path,
        worker: Any | None = None,
        run_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pipeline_output_dir = Path(pipeline_output_dir)
        self._worker = worker
        self._run_dir: Path | None = Path(run_dir) if run_dir is not None else None
        self._advanced: bool = False
        # Only the FIRST model-error log line per run surfaces to the
        # banner — the rest still stream into the log viewer.
        self._error_shown: bool = False
        # Source-model row of the verdict currently shown in the detail
        # pane; -1 when nothing is selected (Approve/Reject act on it).
        self._detail_row: int = -1

        # Shared approval store keyed at the (stable) pipeline output
        # dir — the NER editor + authority editor read/write the same
        # file, so approvals sync live across all three surfaces.
        self._approval_store = ApprovalStore(self._pipeline_output_dir, self)

        self.setWindowTitle("AI verification")
        # Non-modal — the curator may keep using the rest of the app
        # while the AI works. Closing the dialog does NOT stop the
        # worker (the "Close window — keeps running" wording in the
        # button row is honest).
        self.setModal(False)
        self.setMinimumSize(880, 660)

        self._build_ui()
        self._wire_worker()

        # Post-mortem mode: a run dir was provided up-front. Load now.
        if self._run_dir is not None:
            self._refresh_all_tabs()

    # ────────────────────────────────────────────────────────────────
    # UI construction
    # ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self.glass_content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        title = QLabel("<b>AI verification</b>")
        title.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_2XL}px;"
        )
        outer.addWidget(title)

        subtitle = QLabel(
            "An AI reviewer is checking every prediction the Stage 2 models made. "
            "You can keep this window open or close it — the check keeps running."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(
            f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;"
        )
        outer.addWidget(subtitle)

        # Error banner — hidden by default, surfaced at the very top
        # (above the tabs) when the worker or a streamed log line
        # signals a model/API failure.
        outer.addWidget(self._build_error_banner())

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(glass_tab_style(theme))
        self._tabs.addTab(self._build_working_tab(),    "Working…")
        self._tabs.addTab(self._build_verdicts_tab(),   "What the AI thought")
        self._tabs.addTab(self._build_overall_tab(),    "Overall results")
        self._tabs.addTab(self._build_about_tab(),      "About this check")
        outer.addWidget(self._tabs, 1)

        # Footer
        outer.addLayout(self._build_footer())

    # ── Error banner ────────────────────────────────────────────────

    def _build_error_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("errorBanner")
        error_hex = theme.ui("error")
        # Low-alpha error-tint background so the banner reads in both
        # themes; dark mode needs a brighter tint, light mode a paler
        # one. Border + text use the solid theme error colour.
        is_dark = theme.is_dark()
        tint = "rgba(248,113,113, 38)" if is_dark else "rgba(220,38,38, 28)"
        banner.setStyleSheet(
            f"QFrame#errorBanner {{"
            f" background: {tint};"
            f" border: 1px solid {error_hex};"
            f" border-radius: {theme.RADIUS_MD}px;"
            f" }}"
        )
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM,
        )
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(
            f"color:{error_hex}; font-size:{theme.FONT_SM}px;"
            f" font-weight:{theme.WEIGHT_SEMIBOLD}; background: transparent;"
            f" border: none;"
        )
        layout.addWidget(self._error_label, 1)

        self._error_banner = banner
        banner.setVisible(False)
        return banner

    def _show_error_banner(self, headline: str) -> None:
        self._error_label.setText(headline)
        self._error_banner.setVisible(True)

    def _hide_error_banner(self) -> None:
        self._error_label.setText("")
        self._error_banner.setVisible(False)
        self._error_shown = False

    # ── Tab 1: Working… ─────────────────────────────────────────────

    def _build_working_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD,
        )
        layout.setSpacing(theme.SPACE_MD)

        self._working_status = QLabel(
            "The AI is reviewing every prediction the NER models and the "
            "genre classifier made."
        )
        self._working_status.setWordWrap(True)
        self._working_status.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_LG}px;"
        )
        layout.addWidget(self._working_status)

        # Live system-design diagram — centerpiece of the Working tab.
        # Agent work isn't linear (parallel evaluators, cache short-
        # circuits, judge → cache feedback), so a system diagram in
        # motion represents the actual data flow better than a bar.
        self._diagram = AgentSystemDiagram()
        self._diagram.setMinimumSize(600, 400)
        layout.addWidget(self._diagram, 1)

        # Linear progress bar — kept as a thin secondary indicator
        # under the diagram with a small caption.
        progress_caption = QLabel("Overall progress")
        progress_caption.setStyleSheet(
            f"color:{theme.ui('subtext')}; font-size:{theme.FONT_XS}px;"
        )
        layout.addWidget(progress_caption)
        self._progress_bar = DynamicProgressBar()
        self._progress_bar.setToolTip(
            "Each tick equals one prediction the AI has finished reviewing."
        )
        layout.addWidget(self._progress_bar)

        # Stats card
        layout.addWidget(self._build_stats_card())

        # Recent activity disclosure (closed by default)
        self._log_toggle = QToolButton()
        self._log_toggle.setText("▸ Show recent activity")
        self._log_toggle.setCheckable(True)
        self._log_toggle.setChecked(False)
        self._log_toggle.setStyleSheet(
            f"QToolButton {{"
            f" color:{theme.ui('subtext')};"
            f" background: transparent;"
            f" border: none;"
            f" font-size: {theme.FONT_SM}px;"
            f" padding: {theme.SPACE_XS}px 0px;"
            f" text-align: left;"
            f" }}"
        )
        self._log_toggle.toggled.connect(self._on_log_toggled)
        layout.addWidget(self._log_toggle)

        self._log_viewer = LogViewer()
        self._log_viewer.setMaximumBlockCount(2000)
        self._log_viewer.setVisible(False)
        layout.addWidget(self._log_viewer, 1)

        # Action row
        action_row = QHBoxLayout()
        self._stop_btn = QPushButton("Stop verification")
        self._stop_btn.setStyleSheet(theme.button_style("danger"))
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.setEnabled(self._worker is not None)
        action_row.addWidget(self._stop_btn)

        action_row.addStretch(1)

        self._hide_btn = QPushButton("Close window — keeps running")
        self._hide_btn.setStyleSheet(theme.button_style("ghost"))
        self._hide_btn.clicked.connect(self._on_hide_clicked)
        action_row.addWidget(self._hide_btn)

        layout.addLayout(action_row)

        # Post-mortem reassurance — Working tab is mostly idle if no
        # worker is running.
        if self._worker is None:
            self._working_status.setText(
                "This verification has already finished — see the other tabs."
            )

        return container

    def _build_stats_card(self) -> QWidget:
        """Run-progress card.

        Rule 52 update (2026-05-25): the previous version laid out
        labels in a left column with ``subtext`` (light-grey) colour
        and values in a right column with regular text colour. In
        light mode the grey labels faded into the backdrop, leaving
        a column of bare numbers floating to the right of the
        diagram — visually disconnected from any context. Users read
        "112 / 0 / 112" without explanation.

        New layout: a horizontal row of four pill-cards. Each pill
        is a self-contained `[Label / Value]` stack, so the value is
        always visually paired with its label even if part of the
        card is occluded. The whole row sits in a clearly-bordered
        outer frame with a "Run progress" heading.
        """
        outer = QFrame()
        outer.setObjectName("glassPanel")
        outer.setStyleSheet(glass_panel_style(theme))

        v = QVBoxLayout(outer)
        v.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD,
        )
        v.setSpacing(theme.SPACE_SM)

        heading = QLabel("Run progress")
        heading.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_SM}px;"
            f" font-weight:{theme.WEIGHT_SEMIBOLD};"
            f" letter-spacing: 1px; text-transform: uppercase;"
        )
        v.addWidget(heading)

        pills = QHBoxLayout()
        pills.setSpacing(theme.SPACE_MD)

        # Four pills, one per metric. Each pill packs its label
        # directly above its value so the pairing can't be lost.
        self._stat_total_label, self._stat_total_value, total_pill = self._build_stat_pill(
            "Predictions to check",
            "How many of Stage 2's predictions the AI is reviewing.",
        )
        self._stat_done_label, self._stat_done_value, done_pill = self._build_stat_pill(
            "Already done",
            "How many predictions the AI has already reviewed. The split shows fresh judgements vs. answers reused from a prior run's cache.",
        )
        self._stat_remaining_label, self._stat_remaining_value, remaining_pill = self._build_stat_pill(
            "Still to go",
            "How many predictions the AI still has to review.",
        )
        self._stat_reused_label, self._stat_reused_value, reused_pill = self._build_stat_pill(
            "Reused from prior run",
            "How many of today's predictions matched a cached answer from a previous verification — no Gemini call needed.",
        )

        pills.addWidget(total_pill)
        pills.addWidget(done_pill)
        pills.addWidget(remaining_pill)
        pills.addWidget(reused_pill)
        v.addLayout(pills)

        # Advanced-only token counters, hidden until "Show advanced
        # details" is toggled on.
        self._stat_tokens_label, self._stat_tokens_value, tokens_pill = self._build_stat_pill(
            "Words analysed (advanced)",
            "Total Gemini input + output tokens used during this run.",
        )
        tokens_pill.setVisible(False)
        # Stash the pill widget so the advanced toggle can flip it.
        self._stat_tokens_pill = tokens_pill
        v.addWidget(tokens_pill)

        return outer

    def _build_stat_pill(
        self, label_text: str, tooltip: str,
    ) -> tuple[QLabel, QLabel, QFrame]:
        """One [Label / Value] pill in the Run-progress row.

        Returns (label_widget, value_widget, container_frame) so the
        caller can keep references to update the value and toggle
        visibility.
        """
        pill = QFrame()
        is_dark = theme.is_dark()
        pill_bg = "rgba(255,255,255, 18)" if is_dark else "rgba(0,0,0, 8)"
        pill_border = "rgba(255,255,255, 30)" if is_dark else "rgba(0,0,0, 22)"
        pill.setStyleSheet(
            f"QFrame {{"
            f" background: {pill_bg};"
            f" border: 1px solid {pill_border};"
            f" border-radius: {theme.RADIUS_MD}px;"
            f" }}"
        )
        pill.setToolTip(tooltip)
        layout = QVBoxLayout(pill)
        layout.setContentsMargins(
            theme.SPACE_SM, theme.SPACE_SM, theme.SPACE_SM, theme.SPACE_SM,
        )
        layout.setSpacing(2)

        label = QLabel(label_text)
        # Theme-aware text (not subtext) so the label stays readable on
        # both dark + light backdrops. Smaller font keeps the value
        # the visual anchor.
        label.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_XS}px;"
            f" font-weight:{theme.WEIGHT_MEDIUM};"
            f" background: transparent; border: none;"
        )
        label.setWordWrap(True)

        value = QLabel("—")
        value.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_2XL}px;"
            f" font-weight:{theme.WEIGHT_SEMIBOLD};"
            f" background: transparent; border: none;"
        )

        layout.addWidget(label)
        layout.addWidget(value)
        return label, value, pill

    def _stat_label(self, text: str) -> QLabel:
        """Small label for label/value grids (e.g. the manifest grid)."""
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_SM}px;"
            f" font-weight:{theme.WEIGHT_MEDIUM};"
            f" background: transparent; border: none;"
        )
        lbl.setWordWrap(True)
        return lbl

    def _stat_value(self, text: str) -> QLabel:
        """Value label for label/value grids."""
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_SM}px;"
            f" font-weight:{theme.WEIGHT_SEMIBOLD};"
            f" background: transparent; border: none;"
        )
        lbl.setWordWrap(True)
        return lbl

    # ── Tab 2: What the AI thought ──────────────────────────────────

    def _build_verdicts_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD,
        )
        layout.setSpacing(theme.SPACE_SM)

        # Filter chips row
        chips_row = QHBoxLayout()
        self._chip_failures = QCheckBox("Show only the ones the AI flagged as wrong")
        self._chip_unsure = QCheckBox("Show only the ones the AI was unsure about")
        self._chip_reused = QCheckBox("Show only ones reused from a prior run")
        self._chip_errors = QCheckBox("Show only errors")
        self._chip_novel = QCheckBox("Show only new info (not already in MARC)")
        self._chip_novel.setToolTip(
            "Filter to predictions the AI agreed with that are NOT already "
            "captured in the manuscript's structured catalog fields. These "
            "are the predictions Stage 2 actually enriched the record with."
        )
        self._chip_approved = QCheckBox("Show only approved")
        self._chip_approved.setToolTip(
            "Filter to predictions you've approved — synced live with the "
            "NER and authority editors."
        )
        for chip in (
            self._chip_failures,
            self._chip_unsure,
            self._chip_reused,
            self._chip_errors,
            self._chip_novel,
            self._chip_approved,
        ):
            chip.setStyleSheet(
                f"QCheckBox {{ color:{theme.ui('text')}; "
                f" font-size:{theme.FONT_SM}px; padding:2px; }}"
            )
            chips_row.addWidget(chip)
        chips_row.addStretch(1)
        layout.addLayout(chips_row)

        self._chip_failures.toggled.connect(
            lambda on: self._verdict_model.filter_failures_only(bool(on))
        )
        self._chip_unsure.toggled.connect(
            lambda on: self._verdict_model.filter_unsure_only(bool(on))
        )
        self._chip_reused.toggled.connect(
            lambda on: self._verdict_model.filter_reused_only(bool(on))
        )
        self._chip_errors.toggled.connect(
            lambda on: self._verdict_model.filter_errors_only(bool(on))
        )
        self._chip_novel.toggled.connect(
            lambda on: self._verdict_model.filter_novel_only(bool(on))
        )
        self._chip_approved.toggled.connect(
            lambda on: self._verdict_model.filter_approved_only(bool(on))
        )

        # Search box
        self._verdict_search = QLineEdit()
        self._verdict_search.setPlaceholderText("Search the predictions…")
        self._verdict_search.textChanged.connect(self._on_verdict_search)
        layout.addWidget(self._verdict_search)

        # Splitter: table on top, detail card below
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Table
        self._verdict_model = VerdictTableModel()
        self._verdict_model.set_approval_store(self._approval_store)
        # Editor approvals (or any external write) flip our Approved
        # column live via the store's debounced file watcher.
        self._approval_store.changed.connect(self._verdict_model.refresh_from_store)
        self._verdict_proxy = VerdictFilterProxy()
        self._verdict_proxy.setSourceModel(self._verdict_model)

        self._verdict_table = QTableView()
        self._verdict_table.setModel(self._verdict_proxy)
        self._verdict_table.setStyleSheet(glass_table_style(theme))
        viewport = self._verdict_table.viewport()
        if viewport is not None:
            viewport.setAutoFillBackground(False)
        self._verdict_table.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows,
        )
        self._verdict_table.setSelectionMode(
            QTableView.SelectionMode.SingleSelection,
        )
        self._verdict_table.setAlternatingRowColors(True)
        self._verdict_table.setSortingEnabled(True)
        vheader = self._verdict_table.verticalHeader()
        if vheader is not None:
            vheader.setVisible(False)
        hheader = self._verdict_table.horizontalHeader()
        if hheader is not None:
            hheader.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            hheader.setStretchLastSection(True)

        # Pill delegates for the verdict, Approved, + per-aspect columns.
        # Column order: manuscript(0) checker(1) candidate(2) verdict(3)
        # approved(4) name_ok(5) type_ok(6) role_ok(7) …
        for col in (3, 4):
            self._verdict_table.setItemDelegateForColumn(
                col, _PillDelegate(self._verdict_table),
            )
        for col in (5, 6, 7):
            self._verdict_table.setItemDelegateForColumn(
                col, _PillDelegate(self._verdict_table, glyph_only=True),
            )

        self._verdict_table.clicked.connect(self._on_verdict_row_clicked)

        install_column_filters(
            self._verdict_table,
            self._verdict_proxy,
            distinct_values_for=self._distinct_values_for_column,
        )

        splitter.addWidget(self._verdict_table)

        # Detail pane
        self._detail_card = self._build_detail_card()
        splitter.addWidget(self._detail_card)
        splitter.setSizes([400, 200])
        layout.addWidget(splitter, 1)

        # Cap notice
        self._cap_notice = QLabel("")
        self._cap_notice.setStyleSheet(
            f"color:{theme.ui('subtext')}; font-size:{theme.FONT_XS}px;"
        )
        self._cap_notice.setVisible(False)
        layout.addWidget(self._cap_notice)

        # Empty-state placeholder when nothing is loaded yet
        self._verdicts_placeholder = QLabel(
            "The AI is still working — its feedback will show up here when "
            "it's done. You can keep this window open or close it; the "
            "check keeps running."
        )
        self._verdicts_placeholder.setWordWrap(True)
        self._verdicts_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._verdicts_placeholder.setStyleSheet(
            f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;"
            f" padding: {theme.SPACE_LG}px;"
        )
        layout.addWidget(self._verdicts_placeholder)

        return container

    def _build_detail_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("glassPanel")
        card.setStyleSheet(glass_panel_style(theme))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD,
        )
        layout.setSpacing(theme.SPACE_SM)

        self._detail_title = QLabel("Click a row to see what the AI said")
        self._detail_title.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_LG}px;"
            f" font-weight:{theme.WEIGHT_SEMIBOLD};"
        )
        layout.addWidget(self._detail_title)

        self._detail_body = QLabel("")
        self._detail_body.setWordWrap(True)
        self._detail_body.setTextFormat(Qt.TextFormat.RichText)
        self._detail_body.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_SM}px;"
        )
        layout.addWidget(self._detail_body)

        # Approve / Reject row — acts on the currently-selected verdict
        # and writes through the shared store, so the NER + authority
        # editors flip live too.
        approve_row = QHBoxLayout()
        self._approve_btn = QPushButton("Approve")
        self._approve_btn.setStyleSheet(theme.button_style("success"))
        self._approve_btn.clicked.connect(lambda: self._on_approve_clicked(True))
        self._approve_btn.setEnabled(False)
        approve_row.addWidget(self._approve_btn)

        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setStyleSheet(theme.button_style("ghost"))
        self._reject_btn.clicked.connect(lambda: self._on_approve_clicked(False))
        self._reject_btn.setEnabled(False)
        approve_row.addWidget(self._reject_btn)

        approve_row.addStretch(1)
        layout.addLayout(approve_row)

        # Advanced details disclosure inside the card
        self._detail_advanced_toggle = QToolButton()
        self._detail_advanced_toggle.setText("▸ Show the raw record")
        self._detail_advanced_toggle.setCheckable(True)
        self._detail_advanced_toggle.setStyleSheet(
            f"QToolButton {{"
            f" color:{theme.ui('subtext')};"
            f" background: transparent;"
            f" border: none;"
            f" font-size: {theme.FONT_XS}px;"
            f" padding: 2px 0px;"
            f" text-align: left;"
            f" }}"
        )
        self._detail_advanced_toggle.toggled.connect(self._on_detail_advanced_toggled)
        layout.addWidget(self._detail_advanced_toggle)

        self._detail_advanced_body = QTextBrowser()
        self._detail_advanced_body.setVisible(False)
        self._detail_advanced_body.setMaximumHeight(180)
        layout.addWidget(self._detail_advanced_body)

        return card

    # ── Tab 3: Overall results ──────────────────────────────────────

    def _build_overall_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD,
        )
        layout.setSpacing(theme.SPACE_MD)

        # Headline card
        headline_card = QFrame()
        headline_card.setObjectName("glassPanel")
        headline_card.setStyleSheet(glass_panel_style(theme))
        headline_layout = QVBoxLayout(headline_card)
        headline_layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        self._headline_label = QLabel(
            "Results will appear here when the AI finishes."
        )
        self._headline_label.setWordWrap(True)
        self._headline_label.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_XL}px;"
            f" font-weight:{theme.WEIGHT_SEMIBOLD};"
        )
        headline_layout.addWidget(self._headline_label)
        layout.addWidget(headline_card)

        # Per-checker table
        self._summary_table = QTableView()
        self._summary_table.setStyleSheet(glass_table_style(theme))
        viewport = self._summary_table.viewport()
        if viewport is not None:
            viewport.setAutoFillBackground(False)
        self._summary_model = _SummaryTableModel(self)
        self._summary_table.setModel(self._summary_model)
        self._summary_table.setAlternatingRowColors(True)
        self._summary_table.setSelectionMode(
            QTableView.SelectionMode.NoSelection,
        )
        vheader = self._summary_table.verticalHeader()
        if vheader is not None:
            vheader.setVisible(False)
        hheader = self._summary_table.horizontalHeader()
        if hheader is not None:
            hheader.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            hheader.setStretchLastSection(True)
        self._summary_table.setMaximumHeight(220)
        layout.addWidget(self._summary_table)

        # Detailed write-up — theme-aware text + glass tint that adapts
        # for the active OS theme. Rule 52 contrast fix (2026-05-25).
        self._report_browser = QTextBrowser()
        self._report_browser.setOpenExternalLinks(True)
        _is_dark = theme.is_dark()
        _bg_rgba = "rgba(0,0,0, 70)" if _is_dark else "rgba(255,255,255, 140)"
        _border_rgba = "rgba(255,255,255, 22)" if _is_dark else "rgba(0,0,0, 28)"
        self._report_browser.setStyleSheet(
            f"QTextBrowser {{"
            f" background: {_bg_rgba};"
            f" color: {theme.ui('text')};"
            f" border: 1px solid {_border_rgba};"
            f" border-radius: {theme.RADIUS_MD}px;"
            f" padding: {theme.SPACE_SM}px;"
            f" }}"
        )
        self._report_browser.setPlainText(
            "The detailed write-up appears here once the AI is done."
        )
        layout.addWidget(self._report_browser, 1)

        # Footer with open-folder action
        footer = QHBoxLayout()
        footer.addStretch(1)
        open_folder_btn = QPushButton("Open results folder")
        open_folder_btn.setStyleSheet(theme.button_style("load"))
        open_folder_btn.clicked.connect(self._on_open_folder)
        footer.addWidget(open_folder_btn)
        layout.addLayout(footer)

        return container

    # ── Tab 4: About this check ─────────────────────────────────────

    def _build_about_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD,
        )
        layout.setSpacing(theme.SPACE_MD)

        # Friendly manifest cards
        self._manifest_grid = QGridLayout()
        self._manifest_grid.setHorizontalSpacing(theme.SPACE_LG)
        self._manifest_grid.setVerticalSpacing(theme.SPACE_SM)
        manifest_container = QFrame()
        manifest_container.setObjectName("glassPanel")
        manifest_container.setStyleSheet(glass_panel_style(theme))
        manifest_container.setLayout(self._manifest_grid)
        layout.addWidget(manifest_container)

        self._manifest_empty = QLabel(
            "Information about this check will show up once the AI finishes."
        )
        self._manifest_empty.setStyleSheet(
            f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;"
        )
        layout.addWidget(self._manifest_empty)

        # Inputs cards (lazy-loaded JsonTreeViewer)
        inputs_label = QLabel("<b>What the AI looked at</b>")
        inputs_label.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_LG}px;"
        )
        layout.addWidget(inputs_label)

        self._inputs_marc_toggle = self._make_disclosure(
            "▸ The MARC record extract",
            self._on_toggle_marc_input,
        )
        layout.addWidget(self._inputs_marc_toggle)
        self._inputs_marc_viewer: JsonTreeViewer | None = None
        self._inputs_marc_container = QWidget()
        marc_layout = QVBoxLayout(self._inputs_marc_container)
        marc_layout.setContentsMargins(0, 0, 0, 0)
        self._inputs_marc_container.setVisible(False)
        layout.addWidget(self._inputs_marc_container, 1)

        self._inputs_ner_toggle = self._make_disclosure(
            "▸ The Stage 2 NER predictions",
            self._on_toggle_ner_input,
        )
        layout.addWidget(self._inputs_ner_toggle)
        self._inputs_ner_viewer: JsonTreeViewer | None = None
        self._inputs_ner_container = QWidget()
        ner_layout = QVBoxLayout(self._inputs_ner_container)
        ner_layout.setContentsMargins(0, 0, 0, 0)
        self._inputs_ner_container.setVisible(False)
        layout.addWidget(self._inputs_ner_container, 1)

        # Advanced raw-manifest disclosure
        self._raw_manifest_toggle = self._make_disclosure(
            "▸ Show the raw manifest (advanced)",
            self._on_toggle_raw_manifest,
        )
        layout.addWidget(self._raw_manifest_toggle)
        self._raw_manifest_viewer: JsonTreeViewer | None = None
        self._raw_manifest_container = QWidget()
        raw_layout = QVBoxLayout(self._raw_manifest_container)
        raw_layout.setContentsMargins(0, 0, 0, 0)
        self._raw_manifest_container.setVisible(False)
        layout.addWidget(self._raw_manifest_container, 1)

        layout.addStretch(1)
        return container

    def _make_disclosure(self, text: str, on_toggled: Any) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setCheckable(True)
        btn.setStyleSheet(
            f"QToolButton {{"
            f" color:{theme.ui('subtext')};"
            f" background: transparent;"
            f" border: none;"
            f" font-size: {theme.FONT_SM}px;"
            f" padding: {theme.SPACE_XS}px 0px;"
            f" text-align: left;"
            f" }}"
        )
        btn.toggled.connect(on_toggled)
        return btn

    # ── Footer ──────────────────────────────────────────────────────

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_SM)

        self._advanced_toggle = QCheckBox("Show advanced details")
        self._advanced_toggle.setStyleSheet(
            f"QCheckBox {{ color:{theme.ui('subtext')}; "
            f" font-size:{theme.FONT_SM}px; }}"
        )
        self._advanced_toggle.toggled.connect(self._on_advanced_toggled)
        row.addWidget(self._advanced_toggle)

        row.addStretch(1)

        open_btn = QPushButton("Open the run folder")
        open_btn.setStyleSheet(theme.button_style("load"))
        open_btn.clicked.connect(self._on_open_folder)
        row.addWidget(open_btn)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(theme.button_style("ghost"))
        close_btn.clicked.connect(self._on_hide_clicked)
        row.addWidget(close_btn)

        return row

    # ────────────────────────────────────────────────────────────────
    # Worker wiring
    # ────────────────────────────────────────────────────────────────

    def _wire_worker(self) -> None:
        if self._worker is None:
            # Post-mortem: every node visible as 'done' so the diagram
            # is a useful summary instead of an empty idle scene.
            self._diagram.on_finished()
            return

        # Progress bar, finished, error wiring via the shared helper.
        connect_progress_signals(
            self._progress_bar,
            self._worker,
            success_label="Verification complete",
            failure_label="Verification failed",
        )

        # Diagram subscribes to the same signal surface so the user
        # sees the live data flow alongside the progress bar.
        substep_signal = getattr(self._worker, "substep", None)
        if substep_signal is not None and hasattr(substep_signal, "connect"):
            substep_signal.connect(self._diagram.on_substep)
        stats_signal = getattr(self._worker, "stats_update", None)
        if stats_signal is not None and hasattr(stats_signal, "connect"):
            stats_signal.connect(self._diagram.on_stats)
        log_signal = getattr(self._worker, "log_line", None)
        if log_signal is not None and hasattr(log_signal, "connect"):
            log_signal.connect(self._diagram.on_log_line)
        error_signal = getattr(self._worker, "error", None)
        if error_signal is not None and hasattr(error_signal, "connect"):
            error_signal.connect(self._diagram.on_error)
        finished_signal = getattr(self._worker, "finished", None)
        if finished_signal is not None and hasattr(finished_signal, "connect"):
            finished_signal.connect(lambda _path: self._diagram.on_finished())

        # Stream log lines into the (collapsed-by-default) log viewer,
        # rewriting [STEP] markers through humanise_log_line.
        log_signal = getattr(self._worker, "log_line", None)
        if log_signal is not None and hasattr(log_signal, "connect"):
            log_signal.connect(self._on_log_line)

        # Substep updates also refresh the friendly "Currently:" text
        # at the top of the Working tab.
        substep_signal = getattr(self._worker, "substep", None)
        if substep_signal is not None and hasattr(substep_signal, "connect"):
            substep_signal.connect(self._on_substep)

        stats_signal = getattr(self._worker, "stats_update", None)
        if stats_signal is not None and hasattr(stats_signal, "connect"):
            stats_signal.connect(self._on_stats_update)

        finished_signal = getattr(self._worker, "finished", None)
        if finished_signal is not None and hasattr(finished_signal, "connect"):
            finished_signal.connect(self._on_worker_finished)

        error_signal = getattr(self._worker, "error", None)
        if error_signal is not None and hasattr(error_signal, "connect"):
            error_signal.connect(self._on_worker_error)

    # ────────────────────────────────────────────────────────────────
    # Slots
    # ────────────────────────────────────────────────────────────────

    def _on_log_line(self, raw: str) -> None:
        if not raw:
            return
        # Surface the FIRST model-execution-failure line to the banner.
        # The full line still streams into the log viewer below.
        if not self._error_shown and looks_like_model_error(raw):
            self._error_shown = True
            self._show_error_banner(f"Model error: {raw.strip()[:160]}")
        # Hide [STATS]/[PROGRESS] markers in non-advanced view to keep
        # the curator-facing log readable; engineers see them when
        # Advanced details is on.
        if not self._advanced and (raw.startswith("[STATS]") or raw.startswith("[PROGRESS]")):
            return
        if raw.startswith("[STEP]"):
            self._log_viewer.append_line(humanise_log_line(raw))
        else:
            self._log_viewer.append_line(raw)

    def _on_substep(self, label: str) -> None:
        friendly = humanise_log_line(label)
        if friendly:
            self._working_status.setText(f"Currently: {friendly}")

    def _on_stats_update(self, stats: dict[str, Any]) -> None:
        total = int(stats.get("total") or 0)
        judged = int(stats.get("judged") or 0)
        cache_hits = int(stats.get("cache_hits") or 0)
        remaining = max(total - judged, 0)
        fresh = max(judged - cache_hits, 0)

        self._stat_total_value.setText(str(total) if total else "—")
        self._stat_remaining_value.setText(str(remaining))
        self._stat_reused_value.setText(str(cache_hits))
        if total > 0:
            self._stat_done_value.setText(
                f"{judged}  ({fresh} fresh + {cache_hits} reused)"
            )
        else:
            self._stat_done_value.setText("—")

        in_tok = int(stats.get("input_tokens") or 0)
        out_tok = int(stats.get("output_tokens") or 0)
        self._stat_tokens_value.setText(f"{in_tok:,} in / {out_tok:,} out")

    def _on_worker_finished(self, run_dir: Any) -> None:
        try:
            self._run_dir = Path(run_dir)
        except TypeError:
            logger.warning("Eval-agent finished but emitted an unexpected path: %r", run_dir)
            return
        self._stop_btn.setEnabled(False)
        self._refresh_all_tabs()

    def _on_worker_error(self, message: str) -> None:
        # Keep stop button disabled (the subprocess is already done).
        self._stop_btn.setEnabled(False)
        if message:
            self._show_error_banner(f"Verification error: {message.strip()[:160]}")
            self._log_viewer.setVisible(True)
            self._log_toggle.setChecked(True)
            self._log_viewer.append_line(f"ERROR: {message}")

    def _on_log_toggled(self, on: bool) -> None:
        self._log_viewer.setVisible(bool(on))
        self._log_toggle.setText("▾ Hide recent activity" if on else "▸ Show recent activity")

    def _on_stop_clicked(self) -> None:
        if self._worker is None:
            return
        reply = QMessageBox.question(
            self,
            "Stop AI verification?",
            "Stop the AI from checking any more predictions? "
            "Anything it's already finished is saved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        terminate = getattr(self._worker, "terminate_subprocess", None)
        if callable(terminate):
            try:
                terminate()
            except Exception:  # noqa: BLE001 — defensive
                logger.exception("terminate_subprocess failed")
        self._stop_btn.setEnabled(False)

    def _on_hide_clicked(self) -> None:
        # CLOSE only hides the dialog. The worker keeps running. This
        # matches the wording on the "Close window — keeps running"
        # button so the contract is honest.
        self.hide()

    def _on_advanced_toggled(self, on: bool) -> None:
        self._advanced = bool(on)
        self._verdict_model.set_advanced(self._advanced)
        # Stats card advanced rows (pill toggled as a whole)
        self._stat_tokens_pill.setVisible(self._advanced)
        self.advanced_toggled.emit(self._advanced)

    def _on_detail_advanced_toggled(self, on: bool) -> None:
        self._detail_advanced_body.setVisible(bool(on))
        self._detail_advanced_toggle.setText(
            "▾ Hide the raw record" if on else "▸ Show the raw record"
        )

    def _on_verdict_search(self, text: str) -> None:
        self._verdict_proxy.setFilterFixedString(text or "")

    def _on_verdict_row_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        source_index = self._verdict_proxy.mapToSource(index)
        record = self._verdict_model.raw_row(source_index.row())
        if record is None:
            return
        self._detail_row = source_index.row()
        self._approve_btn.setEnabled(True)
        self._reject_btn.setEnabled(True)
        self._render_detail(record)
        # The "Manuscript" column (index 0) opens the full original
        # MARC record popup for the clicked control number.
        if index.column() == 0:
            self._open_marc_popup_for(record)

    def _on_approve_clicked(self, approved: bool) -> None:
        if self._detail_row < 0:
            return
        self._verdict_model.approve_row(self._detail_row, approved)

    def _open_marc_popup_for(self, record: dict[str, Any]) -> None:
        record_id = str(record.get("record_id") or "")
        if not record_id:
            return
        # record_id may be a URI — take the last path segment.
        cn = record_id.split("/")[-1]
        if not cn:
            return
        marc_index = load_marc_index(self._pipeline_output_dir)
        open_marc_popup(cn, marc_index.get(cn), parent=self)

    def _on_open_folder(self) -> None:
        target = self._run_dir if self._run_dir is not None else self._pipeline_output_dir
        if target is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    # ── Inputs disclosures ──────────────────────────────────────────

    def _on_toggle_marc_input(self, on: bool) -> None:
        self._inputs_marc_container.setVisible(bool(on))
        self._inputs_marc_toggle.setText(
            "▾ The MARC record extract" if on else "▸ The MARC record extract"
        )
        if on and self._inputs_marc_viewer is None:
            self._inputs_marc_viewer = self._load_input_viewer(
                self._pipeline_output_dir / "marc_extracted.json",
            )
            if self._inputs_marc_viewer is not None:
                layout = self._inputs_marc_container.layout()
                if layout is not None:
                    layout.addWidget(self._inputs_marc_viewer)

    def _on_toggle_ner_input(self, on: bool) -> None:
        self._inputs_ner_container.setVisible(bool(on))
        self._inputs_ner_toggle.setText(
            "▾ The Stage 2 NER predictions" if on else "▸ The Stage 2 NER predictions"
        )
        if on and self._inputs_ner_viewer is None:
            self._inputs_ner_viewer = self._load_input_viewer(
                self._pipeline_output_dir / "ner_results.json",
            )
            if self._inputs_ner_viewer is not None:
                layout = self._inputs_ner_container.layout()
                if layout is not None:
                    layout.addWidget(self._inputs_ner_viewer)

    def _on_toggle_raw_manifest(self, on: bool) -> None:
        self._raw_manifest_container.setVisible(bool(on))
        self._raw_manifest_toggle.setText(
            "▾ Hide the raw manifest" if on else "▸ Show the raw manifest (advanced)"
        )
        if on and self._raw_manifest_viewer is None and self._run_dir is not None:
            manifest = _safe_read_json(self._run_dir / "manifest.json")
            if manifest is not None:
                self._raw_manifest_viewer = JsonTreeViewer(manifest)
                layout = self._raw_manifest_container.layout()
                if layout is not None:
                    layout.addWidget(self._raw_manifest_viewer)

    def _load_input_viewer(self, path: Path) -> JsonTreeViewer | None:
        data = _safe_read_json(path)
        if data is None:
            placeholder = QLabel(
                f"Couldn't open {path.name}. The file may be missing or unreadable."
            )
            placeholder.setStyleSheet(f"color:{theme.ui('warning')};")
            # Return None — caller short-circuits and we add the
            # placeholder directly so the user is not left staring at
            # an empty disclosure.
            layout = (
                self._inputs_marc_container.layout()
                if path.name.startswith("marc")
                else self._inputs_ner_container.layout()
            )
            if layout is not None:
                layout.addWidget(placeholder)
            return None
        return JsonTreeViewer(data, friendly_key_map=_FRIENDLY_INPUT_KEYS)

    # ── Filter helpers ──────────────────────────────────────────────

    def _distinct_values_for_column(self, column: int) -> list[str]:
        model = self._verdict_proxy.sourceModel()
        if not isinstance(model, QAbstractItemModel):
            return []
        values: set[str] = set()
        rows = model.rowCount()
        for row in range(rows):
            value = str(
                model.data(model.index(row, column), int(Qt.ItemDataRole.DisplayRole)) or ""
            )
            if value:
                values.add(value)
        return sorted(values)

    # ────────────────────────────────────────────────────────────────
    # Refresh from disk
    # ────────────────────────────────────────────────────────────────

    def _refresh_all_tabs(self) -> None:
        if self._run_dir is None:
            return
        self._refresh_verdicts()
        self._refresh_overall()
        self._refresh_about()
        self.finished_loaded.emit(self._run_dir)

    def refresh(self) -> None:
        """Public re-load entrypoint. Tests + the parent window call this."""
        self._hide_error_banner()
        self._refresh_all_tabs()

    def _refresh_verdicts(self) -> None:
        assert self._run_dir is not None  # noqa: S101 — guarded by caller
        results_path = self._run_dir / "results.jsonl"
        # Build the MARC structured-field index so verdicts can be
        # annotated with "✨ New" when the value is not already in the
        # catalog. Missing file → empty index → every row is non-novel
        # (the column simply renders blank rather than misleading).
        from mhm_pipeline.gui.dialogs.widgets.marc_structured_index import (  # noqa: PLC0415
            MarcStructuredIndex,
        )
        marc_index = MarcStructuredIndex.load(
            self._pipeline_output_dir / "marc_extracted.json",
        )
        self._verdict_model.load(results_path, marc_index=marc_index)
        has_rows = self._verdict_model.total_row_count() > 0
        self._verdicts_placeholder.setVisible(not has_rows)
        if self._verdict_model.is_capped():
            self._cap_notice.setText(
                f"Showing 5000 of {self._verdict_model.total_row_count()} — "
                "narrow your filters to see more."
            )
            self._cap_notice.setVisible(True)
        else:
            self._cap_notice.setVisible(False)

    def _refresh_overall(self) -> None:
        assert self._run_dir is not None  # noqa: S101 — guarded by caller
        summary_rows = _read_summary_csv(self._run_dir / "summary.csv")
        self._summary_model.set_rows(summary_rows)
        self._headline_label.setText(compose_headline(summary_rows))

        report_path = self._run_dir / "report.md"
        if report_path.exists():
            try:
                raw = report_path.read_text(encoding="utf-8")
            except OSError:
                raw = ""
        else:
            raw = ""
        markdown = humanise_report_md(raw)
        try:
            self._report_browser.setMarkdown(
                markdown or "No detailed write-up was produced for this run."
            )
        except AttributeError:
            self._report_browser.setPlainText(markdown)

    def _refresh_about(self) -> None:
        assert self._run_dir is not None  # noqa: S101 — guarded by caller
        manifest = _safe_read_json(self._run_dir / "manifest.json") or {}
        self_verify = _safe_read_json(self._run_dir / "self_verify.json")

        # Wipe existing grid contents (cheap for ~5 rows)
        while self._manifest_grid.count() > 0:
            item = self._manifest_grid.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        rows = self._build_manifest_rows(manifest, self_verify)
        if not rows:
            self._manifest_empty.setVisible(True)
            return
        self._manifest_empty.setVisible(False)
        for r, (lhs, rhs) in enumerate(rows):
            lhs_label = self._stat_label(lhs)
            rhs_label = self._stat_value(rhs)
            self._manifest_grid.addWidget(lhs_label, r, 0)
            self._manifest_grid.addWidget(rhs_label, r, 1)

    def _build_manifest_rows(
        self,
        manifest: dict[str, Any],
        self_verify: dict[str, Any] | None,
    ) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []

        when = _format_when(
            str(manifest.get("started_at") or ""),
            str(manifest.get("finished_at") or ""),
        )
        if when:
            rows.append(("When it ran", when))

        judge = str(manifest.get("judge_id") or "")
        if judge:
            rows.append(("The AI we asked", humanise_model(judge)))

        evaluators = manifest.get("evaluators") or manifest.get("models") or []
        if isinstance(evaluators, list) and evaluators:
            friendly = ", ".join(humanise_evaluator(str(ev)) for ev in evaluators)
            rows.append(("Which checkers ran", friendly))

        candidates_total = manifest.get("candidates_total")
        if candidates_total is not None:
            rows.append(("Predictions reviewed", str(candidates_total)))

        cache_hits = manifest.get("cache_hits")
        if isinstance(cache_hits, (int, float)) and int(cache_hits) > 0:
            rows.append(("Saved by reuse from prior runs", str(int(cache_hits))))

        if isinstance(self_verify, dict):
            n_checked = self_verify.get("n") or self_verify.get("total") or 0
            agreed = self_verify.get("agreed") or self_verify.get("matches") or 0
            try:
                pct = round((int(agreed) / int(n_checked)) * 100) if int(n_checked) else 0
            except (TypeError, ValueError):
                pct = 0
            rows.append((
                "Self-check result",
                f"The AI agreed with itself on a re-check of {n_checked} predictions "
                f"({pct}% — {'passed' if pct >= 80 else 'review the report'})",
            ))

        return rows

    # ── Detail card rendering ───────────────────────────────────────

    def _render_detail(self, record: dict[str, Any]) -> None:
        verdict = record.get("verdict") or {}
        record_id = str(record.get("record_id") or "")
        evaluator = humanise_evaluator(str(record.get("evaluator_id") or ""))

        self._detail_title.setText(
            f"<b>{evaluator}</b> reviewed prediction from <span style='color:{theme.ui('subtext')}'>"
            f"{record_id}</span>"
        )

        candidate = record.get("candidate")
        if isinstance(candidate, dict):
            candidate_text = candidate.get("text") or candidate.get("name") or ""
        else:
            candidate_text = str(candidate or "")

        reasoning = str(verdict.get("reasoning") or "")
        overall = humanise_verdict(str(verdict.get("overall") or ""))

        body_html = (
            f"<p><b>What the model originally predicted:</b><br>"
            f"<span style='color:{theme.ui('text')}'>{_escape(candidate_text)}</span></p>"
            f"<p><b>What the AI says about it:</b><br>"
            f"<span style='color:{theme.ui('text')}'>{_escape(overall)}</span></p>"
            f"<p><b>The AI's full reasoning:</b><br>"
            f"<span style='color:{theme.ui('subtext')}'>{_escape(reasoning) or '—'}</span></p>"
        )
        self._detail_body.setText(body_html)

        try:
            self._detail_advanced_body.setPlainText(
                json.dumps(record, ensure_ascii=False, indent=2)
            )
        except (TypeError, ValueError):
            self._detail_advanced_body.setPlainText(repr(record))


# ────────────────────────────────────────────────────────────────────
# Summary table model (used by Tab 3)
# ────────────────────────────────────────────────────────────────────


class _SummaryTableModel(QAbstractItemModel):
    """Friendly per-checker rollup table for the Overall results tab."""

    HEADERS_FRIENDLY: list[str] = [
        "AI checker", "Looked at", "Looks right", "Got wrong",
        "Partly right", "Couldn't tell",
    ]
    HEADERS_ADVANCED: list[str] = HEADERS_FRIENDLY + ["Strict precision"]

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex | None = None,
    ) -> QModelIndex:
        if parent is not None and parent.isValid():
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, _index: QModelIndex | None = None) -> QModelIndex:  # type: ignore[override]
        return QModelIndex()

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self.HEADERS_FRIENDLY)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if role != int(Qt.ItemDataRole.DisplayRole):
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS_FRIENDLY):
                return self.HEADERS_FRIENDLY[section]
        return None

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid() or role != int(Qt.ItemDataRole.DisplayRole):
            return None
        row = self._rows[index.row()]
        col = index.column()
        if col == 0:
            return humanise_evaluator(str(row.get("evaluator_id") or ""))
        if col == 1:
            return str(row.get("candidates_total") or row.get("total") or "")
        if col == 2:
            return str(row.get("full") or "0")
        if col == 3:
            return str(row.get("fail") or "0")
        if col == 4:
            return str(row.get("partial") or "0")
        if col == 5:
            return str(row.get("abstain") or row.get("errors") or "0")
        return ""


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _safe_read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def _read_summary_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for row in reader]
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return []


def _format_when(started: str, finished: str) -> str:
    if not started:
        return ""
    started_dt = _parse_iso(started)
    finished_dt = _parse_iso(finished) if finished else None
    if started_dt is None:
        return started
    started_hhmm = started_dt.strftime("%H:%M")
    if finished_dt is None:
        return started_hhmm
    finished_hhmm = finished_dt.strftime("%H:%M")
    minutes = int((finished_dt - started_dt).total_seconds() // 60)
    if minutes <= 0:
        return f"{started_hhmm} → {finished_hhmm}"
    if minutes == 1:
        return f"{started_hhmm} → {finished_hhmm} (1 minute)"
    return f"{started_hhmm} → {finished_hhmm} ({minutes} minutes)"


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # Python's fromisoformat handles "+00:00" suffixes since 3.11.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _escape(text: str) -> str:
    """Minimal HTML escape for the detail card."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


__all__ = ["AiVerificationDialog", "VerdictFilterProxy", "looks_like_model_error"]
