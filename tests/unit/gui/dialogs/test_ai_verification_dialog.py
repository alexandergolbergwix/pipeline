"""End-to-end UI tests for :class:`AiVerificationDialog` (Rule 52).

Drives the live 4-tab AI verification dialog. Tests cover:

* Friendly tab labels.
* Post-mortem mode (worker = None) opens cleanly.
* Live worker signals route into the Working tab + diagram.
* The verdicts table loads ``results.jsonl`` on ``finished``.
* Advanced toggle widens the verdicts model.
* Filter chips drive the underlying ``VerdictTableModel`` filters.
* Stop button confirmation honours user "No".
* Close button hides the dialog WITHOUT terminating the worker.
* Status pills surface friendly labels in the detail card.
* ``finished_loaded`` and ``refresh()`` semantics.
* Diagram is wired to the same worker signal surface.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from mhm_pipeline.gui.dialogs.ai_verification_dialog import (  # noqa: E402
    AiVerificationDialog,
)
from mhm_pipeline.gui.dialogs.widgets.agent_system_diagram import (  # noqa: E402
    AgentSystemDiagram,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app


class _StubEvalAgentWorker(QObject):
    """In-test stand-in for ``EvalAgentWorker`` exposing the same signals."""

    substep = pyqtSignal(str)
    stats_update = pyqtSignal(dict)
    log_line = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(Path)
    # ``connect_progress_signals`` reads ``progress`` too — provide it so
    # the no-op connect path doesn't trip the harness.
    progress = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.terminate_calls = 0

    def terminate_subprocess(self) -> None:
        self.terminate_calls += 1


@pytest.fixture
def worker() -> _StubEvalAgentWorker:
    return _StubEvalAgentWorker()


def _seed_run_dir(root: Path, *, rows: int = 3) -> Path:
    run = root / "run-eval-001"
    run.mkdir(parents=True, exist_ok=True)

    results = run / "results.jsonl"
    with results.open("w", encoding="utf-8") as handle:
        for i in range(rows):
            payload = {
                "record_id": f"99000{i}",
                "evaluator_id": "person_ner",
                "candidate": {"text": f"Person {i}"},
                "verdict": {
                    "overall": ["full", "fail", "partial"][i % 3],
                    "name_ok": "yes",
                    "type_ok": "yes",
                    "role_ok": "yes",
                    "reasoning": "The AI is confident.",
                },
                "cache_key": "sha256:abc" if i == 1 else None,
            }
            handle.write(json.dumps(payload) + "\n")

    summary = run / "summary.csv"
    with summary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["evaluator_id", "candidates_total", "full", "partial", "fail"],
        )
        writer.writerow(["person_ner", "10", "9", "1", "0"])
        writer.writerow(["provenance_ner", "5", "4", "1", "0"])

    (run / "report.md").write_text(
        "## Summary\nLooks good.\n", encoding="utf-8",
    )
    (run / "manifest.json").write_text(
        json.dumps({
            "started_at": "2026-05-23T10:00:00",
            "finished_at": "2026-05-23T10:05:00",
            "judge_id": "gemini-2.5-pro",
            "evaluators": ["person_ner", "provenance_ner"],
            "candidates_total": 15,
            "cache_hits": 4,
        }),
        encoding="utf-8",
    )
    return run


# ── Rendering & tabs ────────────────────────────────────────────────


class TestAiVerificationDialogRender:
    def test_four_tabs_with_friendly_labels(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        tab_labels = [
            dialog._tabs.tabText(i) for i in range(dialog._tabs.count())
        ]
        assert tab_labels == [
            "Working…",
            "What the AI thought",
            "Overall results",
            "About this check",
        ]

    def test_worker_none_opens_in_post_mortem_mode(self, tmp_path: Path) -> None:
        # Without a worker, the dialog still builds every tab — but the
        # Stop button is disabled (nothing to stop) and the diagram is
        # in its "done" sweep so the user sees a populated summary
        # rather than an empty idle scene.
        dialog = AiVerificationDialog(tmp_path, worker=None)
        assert dialog._tabs.count() == 4
        assert dialog._stop_btn.isEnabled() is False
        assert isinstance(dialog._diagram, AgentSystemDiagram)


# ── Live signal routing ─────────────────────────────────────────────


class TestSignalRouting:
    def test_substep_updates_working_status_label(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.substep.emit("[STEP] Judging person_ner 47/143")
        # The Working tab's friendly status line picks up the rewrite.
        status_text = dialog._working_status.text()
        assert "Currently:" in status_text
        assert "Person AI" in status_text or "Judging" in status_text
        assert "47" in status_text and "143" in status_text

    def test_stats_update_populates_stats_card(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.stats_update.emit({
            "total": 143, "judged": 47, "cache_hits": 10,
            "input_tokens": 5000, "output_tokens": 1200,
        })
        assert dialog._stat_total_value.text() == "143"
        # remaining = total - judged
        assert dialog._stat_remaining_value.text() == "96"
        # cache reuse counter
        assert dialog._stat_reused_value.text() == "10"

    def test_log_line_appends_to_log_viewer(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.log_line.emit("Loading rubrics")
        # ``LogViewer`` proxies onto a QPlainTextEdit internally.
        log_text = dialog._log_viewer._text_edit.toPlainText()
        assert "Loading rubrics" in log_text

    def test_finished_signal_loads_verdicts_table_from_results_jsonl(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        run = _seed_run_dir(tmp_path, rows=3)
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.finished.emit(run)
        assert dialog._verdict_model.rowCount() >= 3


# ── Advanced toggle ─────────────────────────────────────────────────


class TestAdvancedToggle:
    def test_advanced_toggle_widens_verdict_columns(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        run = _seed_run_dir(tmp_path, rows=2)
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.finished.emit(run)

        cols_default = dialog._verdict_model.columnCount()
        dialog._advanced_toggle.setChecked(True)
        cols_advanced = dialog._verdict_model.columnCount()
        assert cols_advanced > cols_default


# ── Filter chips ────────────────────────────────────────────────────


class TestFilterChips:
    def test_failures_chip_filters_to_fail_only(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        run = _seed_run_dir(tmp_path, rows=6)  # 2 full + 2 fail + 2 partial
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.finished.emit(run)

        all_rows = dialog._verdict_model.rowCount()
        assert all_rows >= 6

        dialog._chip_failures.setChecked(True)
        filtered = dialog._verdict_model.rowCount()
        # We seeded 2 of the 6 records as ``overall == "fail"``.
        assert filtered < all_rows
        assert filtered == 2


# ── Stop button confirmation ────────────────────────────────────────


class TestStopButton:
    def test_stop_cancelled_does_not_terminate_worker(
        self,
        tmp_path: Path,
        worker: _StubEvalAgentWorker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # User clicks Stop, then "No" on the confirmation prompt.
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *_a, **_k: QMessageBox.StandardButton.No,
        )
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        dialog._on_stop_clicked()
        assert worker.terminate_calls == 0
        # And the Stop button stays enabled — they can change their mind.
        assert dialog._stop_btn.isEnabled() is True


# ── Close button (hides without terminating) ────────────────────────


class TestCloseHides:
    def test_close_button_hides_without_terminating_worker(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        dialog.show()
        assert dialog.isVisible()
        dialog._on_hide_clicked()
        # Hidden but not terminated.
        assert dialog.isVisible() is False
        assert worker.terminate_calls == 0


# ── Detail card rendering ───────────────────────────────────────────


class TestDetailCardRendering:
    def test_friendly_verdict_label_appears_in_detail_body(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        record = {
            "record_id": "990001",
            "evaluator_id": "person_ner",
            "candidate": {"text": "Maimonides"},
            "verdict": {
                "overall": "fail",
                "reasoning": "Name doesn't match the source.",
            },
        }
        dialog._render_detail(record)
        body = dialog._detail_body.text()
        # Friendly verdict + friendly evaluator name in the title.
        assert "Got it wrong" in body
        title = dialog._detail_title.text()
        assert "Person AI" in title


# ── finished_loaded signal + refresh() idempotency ──────────────────


class TestFinishedLoadedSignal:
    def test_finished_loaded_fires_with_run_dir_after_finished(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        run = _seed_run_dir(tmp_path, rows=3)
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        spy: list[Path] = []
        dialog.finished_loaded.connect(lambda p: spy.append(p))
        worker.finished.emit(run)
        assert spy
        assert spy[0] == run


class TestRefreshIdempotent:
    def test_refresh_called_twice_is_safe_and_stable(
        self, tmp_path: Path,
    ) -> None:
        run = _seed_run_dir(tmp_path, rows=3)
        dialog = AiVerificationDialog(tmp_path, worker=None, run_dir=run)
        before = dialog._verdict_model.rowCount()
        dialog.refresh()
        dialog.refresh()
        after = dialog._verdict_model.rowCount()
        assert before == after


# ── Diagram wiring ──────────────────────────────────────────────────


class TestDiagramWiring:
    def test_diagram_is_an_agent_system_diagram_and_receives_substep(
        self,
        tmp_path: Path,
        worker: _StubEvalAgentWorker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Replace the diagram's on_substep with a spy AFTER the dialog
        # wires it up — the connection is held by Qt so the slot
        # reference is captured at connect time, not at emit time.
        # To validate the wiring, we install the spy before constructing
        # the dialog so the dialog connects to the spy directly.
        spy = MagicMock()
        original_init = AgentSystemDiagram.__init__

        def _patched_init(self: AgentSystemDiagram, *a: object, **kw: object) -> None:
            original_init(self, *a, **kw)
            self.on_substep = spy  # type: ignore[assignment]

        monkeypatch.setattr(AgentSystemDiagram, "__init__", _patched_init)

        dialog = AiVerificationDialog(tmp_path, worker=worker)
        assert isinstance(dialog._diagram, AgentSystemDiagram)
        worker.substep.emit("Judging person_ner 47/143")
        assert spy.called
