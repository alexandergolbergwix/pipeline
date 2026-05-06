"""Verify the new ``substep = pyqtSignal(str)`` on ``StageWorker`` is emitted
at structural boundaries by each pipeline stage worker.

The signal is independent of the existing ``progress`` / ``log_line`` signals
and is consumed by the ``DynamicProgressBar`` widget (Agent A).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Headless Qt for unit tests (no display needed for QSignalSpy / pyqtSignal).
import os as _os

_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, pyqtSignal  # noqa: E402

from mhm_pipeline.controller.workers import (  # noqa: E402
    AuthorityWorker,
    MarcParseWorker,
    NerWorker,
    StageWorker,
)


@pytest.fixture(scope="module")
def _qt_app() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv)
    return app


def _collect(signal: Any) -> list[str]:
    """Subscribe to a pyqtSignal(str) and return the list it appends to."""
    captured: list[str] = []
    signal.connect(captured.append)
    return captured


# ── Test 4: signal exists on the base class ─────────────────────────────────


class TestSubstepSignalExistsOnStageWorkerBase:
    """``StageWorker`` exposes a ``substep`` PyQt signal of type ``str``."""

    def test_substep_signal_exists_on_stage_worker_base(
        self, _qt_app: QCoreApplication
    ) -> None:
        # Class-level attribute must exist (declared on the base).
        assert hasattr(StageWorker, "substep")

        # Build a trivial concrete subclass — base class is abstract w.r.t.
        # run() but otherwise instantiable.
        class _Trivial(StageWorker):
            def run(self) -> None:
                return None

        worker = _Trivial()
        # Each subclass instance has its own bound signal — connecting must work.
        captured: list[str] = []
        worker.substep.connect(captured.append)
        worker.substep.emit("hello")
        QCoreApplication.processEvents()
        assert captured == ["hello"]


# ── Test 1: MarcParseWorker emits Reading + Writing substeps ─────────────────


class TestMarcParseWorkerSubsteps:
    """``MarcParseWorker`` emits 'Reading <file>' before reading and
    'Writing marc_extracted.json' before writing."""

    def test_marc_parse_worker_emits_reading_and_writing_substeps(
        self, _qt_app: QCoreApplication, tmp_path: Path
    ) -> None:
        input_path = tmp_path / "input.csv"
        input_path.write_text("dummy", encoding="utf-8")
        output_dir = tmp_path / "out"

        # Mock UnifiedReader + extract_all_data so run() never touches I/O
        # beyond the JSON write at the end.
        fake_record = MagicMock()
        fake_record.control_number = "CN-001"

        @dataclasses.dataclass
        class _FakeData:
            title: str = ""

        with (
            patch("converter.parser.unified_reader.UnifiedReader") as MockReader,
            patch(
                "converter.transformer.field_handlers.extract_all_data",
                return_value=_FakeData(),
            ),
        ):
            MockReader.return_value.read_file.return_value = iter([fake_record])

            worker = MarcParseWorker(
                input_path=input_path,
                output_dir=output_dir,
                device="cpu",
            )
            captured: list[str] = []
            errors: list[str] = []
            worker.substep.connect(captured.append)
            worker.error.connect(errors.append)

            # Run synchronously — never start the thread.
            worker.run()
            QCoreApplication.processEvents()

        assert errors == [], f"unexpected errors: {errors}"
        assert any(
            s.startswith("Reading ") and "input.csv" in s for s in captured
        ), f"expected 'Reading input.csv' substep, got {captured!r}"
        assert (
            "Writing marc_extracted.json" in captured
        ), f"expected 'Writing marc_extracted.json' substep, got {captured!r}"


# ── Test 2: AuthorityWorker emits Stage 3.1 / 3.2 / 3.3 substeps ─────────────


class TestAuthorityWorkerSubsteps:
    """``AuthorityWorker`` emits at least Stage 3.1, 3.2, and 3.3 labels."""

    def test_authority_worker_emits_stage_3_1_through_3_5_substeps(
        self, _qt_app: QCoreApplication, tmp_path: Path
    ) -> None:
        # Single-record fixture: one MARC author with name → triggers all matchers.
        marc_records = [
            {
                "_control_number": "CN-001",
                "authors": [{"name": "Maimonides", "type": "person"}],
                "contributors": [],
                "related_places": [],
            }
        ]
        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(json.dumps(marc_records), encoding="utf-8")
        output_dir = tmp_path / "out"

        # Mock all four matcher classes — return no matches so the worker
        # exits cleanly without making real HTTP / DB calls.
        mock_mazal_class = MagicMock()
        mock_mazal_inst = MagicMock()
        mock_mazal_inst.match_person.return_value = None
        mock_mazal_inst.is_available = False
        mock_mazal_inst.index_path = ""
        mock_mazal_class.return_value = mock_mazal_inst

        mock_viaf_class = MagicMock()
        mock_viaf_inst = MagicMock()
        mock_viaf_inst.match_person.return_value = None
        mock_viaf_class.return_value = mock_viaf_inst

        mock_kima_class = MagicMock()
        mock_kima_inst = MagicMock()
        mock_kima_inst.match_place.return_value = None
        mock_kima_class.return_value = mock_kima_inst

        mock_wd_class = MagicMock()
        mock_wd_inst = MagicMock()
        mock_wd_inst.find_qid_by_viaf.return_value = None
        mock_wd_inst.find_qid_by_mazal.return_value = None
        mock_wd_inst.match_person.return_value = None
        mock_wd_inst.last_match_was_latin_only.return_value = False
        mock_wd_class.return_value = mock_wd_inst

        # NLI strict mode: return None so the fallback path runs and the
        # 'Stage 3.1 — Mazal lookup' substep fires.
        from converter.authority import nli_strict_mode

        with (
            patch("converter.authority.kima_matcher.KimaMatcher", mock_kima_class),
            patch("converter.authority.mazal_matcher.MazalMatcher", mock_mazal_class),
            patch("converter.authority.viaf_matcher.VIAFMatcher", mock_viaf_class),
            patch(
                "converter.authority.wikidata_matcher.WikidataMatcher", mock_wd_class
            ),
            patch.object(
                nli_strict_mode, "resolve_with_nli_priority", return_value=None
            ),
        ):
            worker = AuthorityWorker(
                input_path=input_path,
                output_dir=output_dir,
                ner_path=None,
                enable_viaf=True,
                enable_kima=True,
                kima_db_path="",
                mazal_db_path="",
            )
            captured: list[str] = []
            errors: list[str] = []
            worker.substep.connect(captured.append)
            worker.error.connect(errors.append)
            worker.run()
            QCoreApplication.processEvents()

        assert errors == [], f"unexpected errors: {errors}"
        joined = "\n".join(captured)
        assert "Stage 3.1" in joined, f"missing Stage 3.1 in {captured!r}"
        assert "Stage 3.2" in joined, f"missing Stage 3.2 in {captured!r}"
        assert "Stage 3.3" in joined, f"missing Stage 3.3 in {captured!r}"


# ── Test 3: NerWorker emits Loading substeps before NER inference ────────────


class TestNerWorkerSubsteps:
    """``NerWorker`` emits 'Loading … model' substeps BEFORE any
    'NER inference …' substep — model loads always precede inference."""

    def test_ner_worker_emits_model_load_substeps_before_inference(
        self, _qt_app: QCoreApplication, tmp_path: Path
    ) -> None:
        marc_records = [
            {
                "_control_number": "CN-001",
                "notes": ["a short note"],
                "colophon_text": "",
                "provenance": "",
                "contents": [],
                "title": "",
            }
        ]
        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(json.dumps(marc_records), encoding="utf-8")
        output_dir = tmp_path / "out"

        # Mock the JointNERPipeline class so model "load" is a no-op and
        # process_text returns no entities.
        mock_pipe_class = MagicMock()
        mock_pipe_inst = MagicMock()
        mock_pipe_inst.process_text.return_value = []
        mock_pipe_class.return_value = mock_pipe_inst

        # Inject fake module so the lazy import inside NerWorker.run() picks
        # it up regardless of repo layout.
        fake_inference_pipeline = type(sys)("ner.inference_pipeline")
        fake_inference_pipeline.JointNERPipeline = mock_pipe_class

        with (
            patch.dict(
                sys.modules, {"ner.inference_pipeline": fake_inference_pipeline}
            ),
            # Force provenance + contents resolution to fail so only the
            # always-loaded person model fires.
            patch.object(
                NerWorker,
                "_resolve_model_path",
                staticmethod(lambda *a, **kw: ""),
            ),
        ):
            worker = NerWorker(
                input_path=input_path,
                output_dir=output_dir,
                model_path="dummy",
                device="cpu",
                batch_size=1,
            )
            captured: list[str] = []
            errors: list[str] = []
            worker.substep.connect(captured.append)
            worker.error.connect(errors.append)
            worker.run()
            QCoreApplication.processEvents()

        assert errors == [], f"unexpected errors: {errors}"
        # Find the index of the first 'Loading' and the first 'NER inference'.
        loading_idx = next(
            (i for i, s in enumerate(captured) if s.startswith("Loading ")), -1
        )
        inference_idx = next(
            (i for i, s in enumerate(captured) if s.startswith("NER inference")),
            -1,
        )
        assert loading_idx >= 0, f"no 'Loading' substep in {captured!r}"
        assert inference_idx >= 0, f"no 'NER inference' substep in {captured!r}"
        assert (
            loading_idx < inference_idx
        ), f"Loading must precede inference, got {captured!r}"
