"""Renders the eval-agent's per-run report after ``EvalAgentWorker.finished``.

Rule 50 (2026-05-25). The bundled eval-agent emits four artefacts in
``state/runs/<ts>/``:

* ``manifest.json`` — config + token counts + start/finish stamps
* ``results.jsonl`` — one verdict per candidate
* ``summary.csv`` — per-evaluator precision rollup
* ``report.md`` — human-readable markdown summary

This dialog displays ``summary.csv`` as a table at the top and the
rendered ``report.md`` below, with a footer that lets the user open
the run folder in Finder/Explorer for the raw artefacts. Auto-
rejection of low-confidence entities is deferred to a v2 PR.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.widgets.glass_dialog import GlassDialog

logger = logging.getLogger(__name__)


class EvalAgentReportDialog(GlassDialog):
    """Modal report viewer. Constructed with the per-run output dir."""

    def __init__(self, run_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._run_dir = Path(run_dir)
        self.setWindowTitle("AI agent verification report")
        self.setModal(True)
        self.setMinimumSize(720, 640)

        self._build_ui()

    # ── construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self.glass_content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        title = QLabel("<b>AI agent verification report</b>")
        title.setStyleSheet(f"color:{theme.ui('text')}; font-size:{theme.FONT_LG}px;")
        outer.addWidget(title)

        path_label = QLabel(f"Run dir: <code>{self._run_dir}</code>")
        path_label.setTextFormat(Qt.TextFormat.RichText)
        path_label.setStyleSheet(f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;")
        outer.addWidget(path_label)

        # Summary table
        summary = self._build_summary_table()
        if summary is not None:
            outer.addWidget(summary)
        else:
            placeholder = QLabel(
                "<i>summary.csv not found in this run.</i>"
            )
            placeholder.setStyleSheet(f"color:{theme.ui('warning')};")
            outer.addWidget(placeholder)

        # Markdown report
        report_browser = QTextBrowser()
        report_browser.setOpenExternalLinks(True)
        report_md = self._read_report_md()
        if report_md:
            # QTextBrowser handles basic Markdown via setMarkdown when
            # available (Qt 6+); fall back to plain text otherwise.
            try:
                report_browser.setMarkdown(report_md)
            except AttributeError:
                report_browser.setPlainText(report_md)
        else:
            report_browser.setPlainText("report.md not found in this run.")
        outer.addWidget(report_browser, 1)

        # Footer: Apply auto-rejection (DEFERRED — disabled),
        #         Open results folder, Close.
        self._auto_reject = QCheckBox("Apply auto-rejection to low-confidence entities (coming soon)")
        self._auto_reject.setEnabled(False)
        self._auto_reject.setToolTip(
            "v2 will wire this into the ExtractionEditor so low-confidence "
            "entities can be unapproved in bulk based on the AI agent's verdicts."
        )
        outer.addWidget(self._auto_reject)

        buttons = QDialogButtonBox()
        open_folder = QPushButton("Open results folder")
        open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder.clicked.connect(self._on_open_folder)
        buttons.addButton(open_folder, QDialogButtonBox.ButtonRole.ActionRole)
        close_btn = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        outer.addWidget(buttons)

    def _build_summary_table(self) -> QWidget | None:
        csv_path = self._run_dir / "summary.csv"
        if not csv_path.exists():
            return None
        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
        except OSError as exc:
            logger.warning("Could not read %s: %s", csv_path, exc)
            return None
        if not rows:
            return None

        headers = rows[0]
        data_rows = rows[1:]

        table = QTableWidget(len(data_rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        h = table.horizontalHeader()
        if h is not None:
            h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            h.setStretchLastSection(True)
        for r, row in enumerate(data_rows):
            for c, cell in enumerate(row):
                item = QTableWidgetItem(cell)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(r, c, item)
        table.setMaximumHeight(200)
        return table

    def _read_report_md(self) -> str:
        md_path = self._run_dir / "report.md"
        if not md_path.exists():
            return ""
        try:
            return md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read %s: %s", md_path, exc)
            return ""

    # ── slots ───────────────────────────────────────────────────────

    def _on_open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._run_dir)))


__all__ = ["EvalAgentReportDialog"]
