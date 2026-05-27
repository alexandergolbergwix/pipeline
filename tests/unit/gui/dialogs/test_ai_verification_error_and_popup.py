"""Tests for the AiVerificationDialog error banner + MARC popup wiring.

Covers the two Rule-52 follow-up features owned by this dialog:

* A dedicated, theme-tokened error banner at the top of the dialog that
  surfaces (a) the worker ``error`` signal and (b) model-execution-
  failure lines detected in the streamed log.
* Clicking the "Manuscript" column (index 0) of a verdict row opens the
  shared MARC record popup for that control number.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui.dialogs import ai_verification_dialog  # noqa: E402
from mhm_pipeline.gui.dialogs.ai_verification_dialog import (  # noqa: E402
    AiVerificationDialog,
    looks_like_model_error,
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
    progress = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.terminate_calls = 0

    def terminate_subprocess(self) -> None:
        self.terminate_calls += 1


@pytest.fixture
def worker() -> _StubEvalAgentWorker:
    return _StubEvalAgentWorker()


def _seed_results(root: Path, *, control_number: str = "990001") -> Path:
    run = root / "run-eval-001"
    run.mkdir(parents=True, exist_ok=True)
    results = run / "results.jsonl"
    payload = {
        "record_id": f"https://example.org/record/{control_number}",
        "evaluator_id": "person_ner",
        "candidate": {"text": "Maimonides"},
        "verdict": {
            "overall": "full",
            "name_ok": "yes",
            "type_ok": "yes",
            "role_ok": "yes",
            "reasoning": "Confident.",
        },
        "cache_key": None,
    }
    results.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return run


# ── Goal 1: model-error detection helper ────────────────────────────


class TestLooksLikeModelError:
    @pytest.mark.parametrize(
        "line",
        [
            "model gemini-x not found",
            "HTTP 404 Not Found",
            "google.api_core.exceptions: 429 RESOURCE_EXHAUSTED",
            "INVALID_ARGUMENT: bad model id",
            "PERMISSION_DENIED on the API key",
        ],
    )
    def test_matches_model_failures(self, line: str) -> None:
        assert looks_like_model_error(line) is True

    @pytest.mark.parametrize(
        "line",
        [
            "[STEP] Judging 1/10",
            "Loading rubrics",
            "[PROGRESS] 5",
            "",
        ],
    )
    def test_ignores_normal_lines(self, line: str) -> None:
        assert looks_like_model_error(line) is False


# ── Goal 1: banner wiring ───────────────────────────────────────────


class TestErrorBanner:
    def test_banner_hidden_by_default(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        assert dialog._error_banner.isVisibleTo(dialog) is False

    def test_worker_error_shows_banner(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.error.emit("subprocess crashed: traceback")
        assert dialog._error_banner.isVisibleTo(dialog) is True
        text = dialog._error_label.text()
        assert "Verification error:" in text
        assert "subprocess crashed" in text

    def test_model_error_log_line_shows_banner(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.log_line.emit("error: model gemini-x not found")
        assert dialog._error_banner.isVisibleTo(dialog) is True
        assert "Model error:" in dialog._error_label.text()

    def test_normal_log_line_does_not_show_banner(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.log_line.emit("[STEP] Judging person_ner 1/10")
        assert dialog._error_banner.isVisibleTo(dialog) is False

    def test_only_first_model_error_surfaces(
        self, tmp_path: Path, worker: _StubEvalAgentWorker,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.log_line.emit("HTTP 500 first failure")
        first = dialog._error_label.text()
        worker.log_line.emit("HTTP 503 second failure")
        # Banner still shows the FIRST error, not the second.
        assert dialog._error_label.text() == first
        assert "500" in first


# ── Goal 2: clicking the Manuscript column opens the MARC popup ──────


class TestManuscriptColumnOpensMarcPopup:
    def test_click_manuscript_column_opens_popup(
        self,
        tmp_path: Path,
        worker: _StubEvalAgentWorker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run = _seed_results(tmp_path, control_number="990001")
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.finished.emit(run)
        assert dialog._verdict_model.rowCount() >= 1

        calls: list[str] = []
        monkeypatch.setattr(
            ai_verification_dialog,
            "open_marc_popup",
            lambda cn, rec, parent=None: calls.append(cn),
        )

        index = dialog._verdict_proxy.index(0, 0)  # Manuscript column
        dialog._on_verdict_row_clicked(index)
        # record_id is a URI; the last segment is the control number.
        assert calls == ["990001"]

    def test_click_non_manuscript_column_does_not_open_popup(
        self,
        tmp_path: Path,
        worker: _StubEvalAgentWorker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run = _seed_results(tmp_path, control_number="990001")
        dialog = AiVerificationDialog(tmp_path, worker=worker)
        worker.finished.emit(run)

        calls: list[str] = []
        monkeypatch.setattr(
            ai_verification_dialog,
            "open_marc_popup",
            lambda cn, rec, parent=None: calls.append(cn),
        )

        index = dialog._verdict_proxy.index(0, 1)  # "Which AI checker"
        dialog._on_verdict_row_clicked(index)
        assert calls == []
