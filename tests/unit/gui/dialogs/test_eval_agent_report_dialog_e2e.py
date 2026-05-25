"""End-to-end UI tests for :class:`EvalAgentReportDialog` (Rule 50)."""

from __future__ import annotations

import csv
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTableWidget, QTextBrowser  # noqa: E402

from mhm_pipeline.gui.dialogs.eval_agent_report_dialog import (  # noqa: E402
    EvalAgentReportDialog,
)


@pytest.fixture(autouse=True)
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app


def _make_run_dir(root: Path) -> Path:
    run = root / "run-abc"
    run.mkdir()
    (run / "report.md").write_text("# Report\n\nVerdicts pass 95%.\n")
    with (run / "summary.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["evaluator", "candidates", "pass", "fail"])
        w.writerow(["person_ner", "10", "9", "1"])
        w.writerow(["genre_classifier", "5", "5", "0"])
    return run


class TestEvalAgentReportDialog:
    def test_renders_summary_table_from_csv(self, tmp_path: Path) -> None:
        run = _make_run_dir(tmp_path)
        dialog = EvalAgentReportDialog(run)
        tables = dialog.findChildren(QTableWidget)
        assert tables, "summary table not built"
        table = tables[0]
        # Header row from CSV
        assert table.columnCount() == 4
        assert table.rowCount() == 2
        # Cells uneditable
        item = table.item(0, 0)
        assert item is not None
        from PyQt6.QtCore import Qt
        assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)

    def test_renders_report_md(self, tmp_path: Path) -> None:
        run = _make_run_dir(tmp_path)
        dialog = EvalAgentReportDialog(run)
        browsers = dialog.findChildren(QTextBrowser)
        assert browsers
        text = browsers[0].toPlainText()
        # Markdown body should be visible in the rendered text.
        assert "Verdicts pass 95%" in text

    def test_handles_missing_summary_csv(self, tmp_path: Path) -> None:
        run = tmp_path / "no-summary"
        run.mkdir()
        (run / "report.md").write_text("report body only\n")
        dialog = EvalAgentReportDialog(run)
        # No summary table is fine; the dialog still opens.
        tables = dialog.findChildren(QTableWidget)
        assert tables == []

    def test_handles_missing_report_md(self, tmp_path: Path) -> None:
        run = tmp_path / "no-report"
        run.mkdir()
        # No report.md — dialog must NOT crash.
        dialog = EvalAgentReportDialog(run)
        browsers = dialog.findChildren(QTextBrowser)
        assert browsers
        assert "report.md not found" in browsers[0].toPlainText()

    def test_auto_reject_checkbox_disabled_v1(self, tmp_path: Path) -> None:
        """Auto-rejection is deferred — the checkbox must be disabled
        in v1 so the user doesn't think it's working."""
        run = _make_run_dir(tmp_path)
        dialog = EvalAgentReportDialog(run)
        assert dialog._auto_reject.isEnabled() is False
        assert "coming soon" in dialog._auto_reject.text().lower()
