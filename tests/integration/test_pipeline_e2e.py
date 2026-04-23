"""End-to-end tests for all six MHM Pipeline stage workers.

Each test class covers one stage worker plus a final class that exercises
PipelineController chaining stages 0 → 3 → 4 without external model deps.

Stages requiring large ML models (NER) or optional external services
(Wikidata) are tested with minimal mocks injected via sys.modules so the
worker signal/output infrastructure is still exercised.

Stage → test class mapping (used by /run-tests skill):
  Venv Imports           → TestVenvImports (catches missing deps, VIAF API, KIMA index)
  GUI Widgets            → TestGuiWidgetContracts (catches missing method crashes)
  Stage 0 (MARC Parse)   → TestMarcParseWorker
  Stage 1 (NER)          → TestNerWorker
  Stage 2 (Authority)    → TestAuthorityWorker, TestMazalIndexWorker, TestKimaIndexWorker
  Stage 3 (RDF Build)    → TestRdfBuildWorker
  Stage 4 (SHACL)        → TestShaclValidateWorker
  Stage 5 (Wikidata)     → TestWikidataUploadWorker
  Controller             → TestPipelineControllerChain
  Full GUI Signal Chain  → TestFullGuiProgressChain

CRITICAL: TestVenvImports runs FIRST to catch "tests pass but app crashes" issues.
These tests verify packages are installed in .venv, not just found via PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip data-heavy integration tests in CI (no MARC files, no Mazal DB)
IN_CI = os.environ.get("CI", "").lower() in ("true", "1")

# ── Repository-relative fixtures ─────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent.parent
MRC_FILE = _ROOT / "data/mrc/test_manuscripts/990000836520205171.mrc"
TSV_FILE = _ROOT / "data/tsvs/17th_century_samples.tsv"
SHAPES_FILE = _ROOT / "ontology/shacl-shapes.ttl"
XML_DIR = _ROOT / "data/NLI_AUTHORITY_XML"
KIMA_TSV_DIR = _ROOT / "data" / "kima"

# Collect available input files — skip MRC in CI (worker too slow / hangs)
_available_inputs: list[object] = []
_available_ids: list[str] = []
if MRC_FILE.exists():
    if IN_CI:
        _available_inputs.append(
            pytest.param(MRC_FILE, marks=pytest.mark.skip("MRC worker too slow in CI"))
        )
    else:
        _available_inputs.append(MRC_FILE)
    _available_ids.append("mrc")
if TSV_FILE.exists():
    _available_inputs.append(TSV_FILE)
    _available_ids.append("tsv")

if not _available_inputs:
    _available_inputs.append(
        pytest.param(Path("/nonexistent"), marks=pytest.mark.skip("no test data"))
    )
    _available_ids.append("skip")

INPUT_FILES = pytest.mark.parametrize("input_file", _available_inputs, ids=_available_ids)


# ── Venv Import Verification ────────────────────────────────────────────────
# These tests verify that critical packages are installed in the venv.
# They catch the "tests pass but app crashes" issue where PYTHONPATH finds
# packages via filesystem but .venv/bin/python cannot.


class TestVenvImports:
    """Verify all critical imports work from venv context (not just PYTHONPATH)."""

    def test_pymarc_imports_from_venv(self) -> None:
        """pymarc must be importable from venv — used by MarcParseWorker."""
        try:
            import pymarc  # noqa: PLC0415
        except ImportError as e:
            pytest.fail(f"pymarc not installed in venv: {e}. Run: uv sync")

    def test_rdflib_imports_from_venv(self) -> None:
        """rdflib must be importable from venv — used by RdfBuildWorker."""
        try:
            import rdflib  # noqa: PLC0415
        except ImportError as e:
            pytest.fail(f"rdflib not installed in venv: {e}. Run: uv sync")

    def test_pyshacl_imports_from_venv(self) -> None:
        """pyshacl must be importable from venv — used by ShaclValidateWorker."""
        try:
            import pyshacl  # noqa: PLC0415
        except ImportError as e:
            pytest.fail(f"pyshacl not installed in venv: {e}. Run: uv sync")

    def test_pyqt6_imports_from_venv(self) -> None:
        """PyQt6 must be importable from venv — used by all GUI workers."""
        try:
            import PyQt6  # noqa: PLC0415
        except ImportError as e:
            pytest.fail(f"PyQt6 not installed in venv: {e}. Run: uv sync")

    def test_stage_0_worker_imports_from_venv(self) -> None:
        """Stage 0 imports must work from venv — catches 90% of runtime crashes."""
        try:
            from converter.parser.unified_reader import UnifiedReader  # noqa: PLC0415
            from converter.transformer.field_handlers import extract_all_data  # noqa: PLC0415
        except ImportError as e:
            pytest.fail(f"Stage 0 imports failed from venv: {e}. Run: uv sync")

    def test_viaf_matcher_uses_json_accept_header(self) -> None:
        """VIAFMatcher must set Accept: application/json on its session."""
        from converter.authority.viaf_matcher import VIAFMatcher  # noqa: PLC0415

        matcher = VIAFMatcher()
        assert matcher._session.headers.get("Accept") == "application/json", (
            "VIAFMatcher session missing Accept: application/json header"
        )

    def test_kima_index_exists_and_is_available(self) -> None:
        """KIMA index DB must exist — matcher silently returns None when missing.

        The matcher's is_available check returns False if the DB doesn't exist,
        but only logs at DEBUG level. This caused zero KIMA matches with no visible error.
        """
        import pytest  # noqa: PLC0415
        from converter.authority.kima_matcher import KimaMatcher  # noqa: PLC0415

        kima = KimaMatcher()
        if not kima.is_available:
            pytest.skip(
                f"KIMA index not available at {kima.index_path} "
                "(not built in CI — run build_kima_index locally)"
            )
        # Verify it can actually match a known place
        uri = kima.match_place("ירושלים")
        kima.close()
        assert uri, "KIMA index exists but failed to match 'ירושלים' — index may be corrupt"

    def test_stage_0_worker_runs_in_qthread_without_crash(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """MarcParseWorker must run in QThread without segfault — real crash was dictiter_iternextitem.

        The actual crash happens when running in a QThread (GUI mode), not when calling run() directly.
        This test catches the threading issue that caused: SIGABRT in dictiter_iternextitem.
        """
        from mhm_pipeline.controller.workers import MarcParseWorker

        worker = MarcParseWorker(TSV_FILE, tmp_path, "cpu", start=0, end=10)

        # Track signals
        progress_values: list[int] = []
        log_lines: list[str] = []
        finished_paths: list[Path] = []
        error_msgs: list[str] = []

        worker.progress.connect(progress_values.append)
        worker.log_line.connect(log_lines.append)
        worker.finished.connect(finished_paths.append)
        worker.error.connect(error_msgs.append)

        # Run in actual QThread — this is where the crash happened
        with qtbot.waitSignal(worker.finished, timeout=30_000):  # type: ignore[attr-defined]
            worker.start()

        # Verify no error was emitted
        assert not error_msgs, f"MarcParseWorker emitted error: {error_msgs}"
        assert finished_paths, "MarcParseWorker did not emit finished"

        # Verify output file exists and is valid JSON
        output_path = finished_paths[0]
        assert output_path.exists(), f"Output file not created: {output_path}"

        records = json.loads(output_path.read_text(encoding="utf-8"))
        assert len(records) == 10, f"Expected 10 records, got {len(records)}"

        # Verify progress was emitted
        assert progress_values, "No progress signals emitted"
        assert progress_values[-1] == 100, "Progress did not reach 100%"


# ── GUI Widget Contract Tests ────────────────────────────────────────────────
# These tests verify that every panel's stage_progress widget exposes the
# set_progress(int) method required by MainWindow._on_stage_progress.
# A missing method causes an unhandled AttributeError inside a Qt slot,
# which PyQt6 escalates to QMessageLogger::fatal → abort() → SIGABRT.
# This killed the app on every Stage 1 run on the 897-record TSV file.


class TestGuiWidgetContracts:
    """Verify GUI widgets expose the API that signal handlers expect."""

    def test_all_panels_stage_progress_has_set_progress(self, qtbot: object) -> None:
        """Every panel.stage_progress must have set_progress(int).

        MainWindow._on_stage_progress calls panel.stage_progress.set_progress(pct)
        for whichever stage is running. If any panel's widget lacks the method,
        the app crashes with SIGABRT (PyQt6 fatal on unhandled AttributeError).
        """
        from mhm_pipeline.gui.panels.authority_panel import AuthorityPanel
        from mhm_pipeline.gui.panels.convert_panel import ConvertPanel
        from mhm_pipeline.gui.panels.ner_panel import NerPanel
        from mhm_pipeline.gui.panels.rdf_panel import RdfPanel
        from mhm_pipeline.gui.panels.validate_panel import ValidatePanel
        from mhm_pipeline.gui.panels.wikidata_panel import WikidataPanel

        panels = [
            ConvertPanel(),
            NerPanel(),
            AuthorityPanel(),
            RdfPanel(),
            ValidatePanel(),
            WikidataPanel(),
        ]

        for panel in panels:
            name = type(panel).__name__
            assert hasattr(panel, "stage_progress"), f"{name} missing stage_progress property"
            widget = panel.stage_progress
            assert hasattr(widget, "set_progress"), (
                f"{name}.stage_progress ({type(widget).__name__}) "
                f"missing set_progress method — this causes SIGABRT"
            )
            # Actually call it to verify no runtime error
            widget.set_progress(50)

    def test_stage_progress_widget_set_progress_accepts_0_to_100(self, qtbot: object) -> None:
        """StageProgressWidget.set_progress must accept the full 0-100 range."""
        from mhm_pipeline.gui.widgets.stage_progress import StageProgressWidget

        widget = StageProgressWidget()
        for pct in (0, 1, 50, 99, 100):
            widget.set_progress(pct)
        assert widget._progress_pct == 100

    def test_stage_progress_widget_set_stage_state_resets_progress(self, qtbot: object) -> None:
        """Setting state to 'done' or 'error' must reset the progress percentage."""
        from mhm_pipeline.gui.widgets.stage_progress import StageProgressWidget

        widget = StageProgressWidget()
        widget.set_progress(75)
        widget.set_stage_state(0, "done")
        assert widget._progress_pct == 0

    def test_main_window_on_stage_progress_does_not_crash(self, qtbot: object) -> None:
        """Simulate the exact signal path that caused the SIGABRT crash.

        worker.progress(int) → controller._on_worker_progress → controller.stage_progress
        → main_window._on_stage_progress → panel.stage_progress.set_progress(pct)
        """
        from mhm_pipeline.controller.pipeline_controller import PipelineController
        from mhm_pipeline.gui.main_window import MainWindow
        from mhm_pipeline.settings.settings_manager import SettingsManager

        settings = SettingsManager()
        controller = PipelineController(settings)
        window = MainWindow(settings, controller)

        # Call _on_stage_progress for every stage index — none should crash
        for stage_idx in range(7):
            window._on_stage_progress(stage_idx, 0)
            window._on_stage_progress(stage_idx, 50)
            window._on_stage_progress(stage_idx, 100)


# ── Shared helpers ────────────────────────────────────────────────────────────


def _ner_json(tmp_path: Path, control_number: str = "990000836520205171") -> Path:
    """Write a minimal NER-results JSON (one record, one entity) to tmp_path."""
    data = [
        {
            "_control_number": control_number,
            "entities": [
                {
                    "type": "PERSON",
                    "person": "משה בן יצחק",
                    "text": "משה בן יצחק",
                    "start": 0,
                    "end": 12,
                }
            ],
        }
    ]
    path = tmp_path / "ner_results.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _marc_json(tmp_path: Path) -> Path:
    """Run MarcParseWorker synchronously and return the output path.

    Calls worker.run() directly (bypasses QThread) so we can use the result
    as a fixture without needing a nested event loop.
    """
    from mhm_pipeline.controller.workers import MarcParseWorker

    worker = MarcParseWorker(MRC_FILE, tmp_path, "cpu")
    # Capture finish/error via direct attribute inspection after run()
    finished_paths: list[Path] = []
    error_msgs: list[str] = []
    worker.finished.connect(finished_paths.append)
    worker.error.connect(error_msgs.append)
    worker.run()  # synchronous — no thread
    assert not error_msgs, f"MarcParseWorker error: {error_msgs}"
    assert finished_paths, "MarcParseWorker did not emit finished"
    return finished_paths[0]


# ── Stage 0: MARC Parse ───────────────────────────────────────────────────────


class TestMarcParseWorker:
    @INPUT_FILES
    def test_produces_json_with_records(
        self, qtbot: object, tmp_path: Path, input_file: Path
    ) -> None:
        from mhm_pipeline.controller.workers import MarcParseWorker

        worker = MarcParseWorker(input_file, tmp_path, "cpu")
        with qtbot.waitSignal(worker.finished, timeout=60_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        output_path: Path = blocker.args[0]
        assert output_path.exists()
        assert output_path.name == "marc_extracted.json"

        records = json.loads(output_path.read_text(encoding="utf-8"))
        assert len(records) >= 1
        assert "_control_number" in records[0]

    def test_tsv_extracts_expected_record_count(self, qtbot: object, tmp_path: Path) -> None:
        """TSV file has 897 data rows — all should parse successfully."""
        from mhm_pipeline.controller.workers import MarcParseWorker

        worker = MarcParseWorker(TSV_FILE, tmp_path, "cpu")
        with qtbot.waitSignal(worker.finished, timeout=60_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        records = json.loads(Path(blocker.args[0]).read_text(encoding="utf-8"))
        assert len(records) == 897

    def test_tsv_first_500_records_parse_with_authority_fields(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """First 500 records from TSV should parse and include all MARC name fields (100, 110, 111, 700, 710, 711)."""
        from mhm_pipeline.controller.workers import MarcParseWorker

        # Process first 500 records (start=0, end=500)
        worker = MarcParseWorker(TSV_FILE, tmp_path, "cpu", start=0, end=500)
        with qtbot.waitSignal(worker.finished, timeout=60_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        records = json.loads(Path(blocker.args[0]).read_text(encoding="utf-8"))
        assert len(records) == 500, f"Expected 500 records, got {len(records)}"

        # Verify some records have name fields that should be authority-matched
        records_with_names = 0
        for record in records:
            has_names = bool(record.get("authors") or record.get("contributors"))
            if has_names:
                records_with_names += 1

        # At least some records should have name fields
        assert records_with_names > 0, "No records had name fields for authority matching"

    @INPUT_FILES
    def test_emits_increasing_progress(
        self, qtbot: object, tmp_path: Path, input_file: Path
    ) -> None:
        from mhm_pipeline.controller.workers import MarcParseWorker

        worker = MarcParseWorker(input_file, tmp_path, "cpu")
        progress_values: list[int] = []
        worker.progress.connect(progress_values.append)  # type: ignore[attr-defined]

        with qtbot.waitSignal(worker.finished, timeout=60_000):  # type: ignore[attr-defined]
            worker.start()

        assert progress_values, "No progress signals emitted"
        assert progress_values[-1] == 100

    def test_missing_input_emits_error(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import MarcParseWorker

        worker = MarcParseWorker(tmp_path / "ghost.mrc", tmp_path, "cpu")
        with qtbot.waitSignal(worker.error, timeout=10_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        assert blocker.args[0]  # non-empty error message

    @INPUT_FILES
    def test_log_lines_emitted(self, qtbot: object, tmp_path: Path, input_file: Path) -> None:
        from mhm_pipeline.controller.workers import MarcParseWorker

        worker = MarcParseWorker(input_file, tmp_path, "cpu")
        log_lines: list[str] = []
        worker.log_line.connect(log_lines.append)  # type: ignore[attr-defined]

        with qtbot.waitSignal(worker.finished, timeout=60_000):  # type: ignore[attr-defined]
            worker.start()

        assert log_lines, "No log_line signals emitted"


# ── Stage 1: NER ──────────────────────────────────────────────────────────────


class TestNerWorker:
    """NER stage tests use a sys.modules mock because the real models are large."""

    _MODEL_PATH = "alexgoldberg/hebrew-manuscript-joint-ner-v2"

    def _make_mock_ner_modules(self) -> dict[str, MagicMock]:
        mock_entity = {
            "type": "PERSON",
            "person": "משה",
            "text": "משה",
            "start": 0,
            "end": 3,
        }
        mock_pipeline = MagicMock()
        mock_pipeline.process_text.return_value = [mock_entity]

        mock_ner_module = MagicMock()
        # Worker imports JointNERPipeline (not ProductionNERPipeline)
        mock_ner_module.JointNERPipeline.return_value = mock_pipeline

        return {
            "ner.inference_pipeline": mock_ner_module,
            "ner.postprocessing_rules": MagicMock(),
        }

    def test_produces_ner_json(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import NerWorker

        input_data = [
            {
                "_control_number": "990000836520205171",
                "notes": ["כתב יד מהמאה ה-15, נכתב על ידי משה"],
                "colophon_text": "נשלם",
            }
        ]
        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")

        with patch.dict(sys.modules, self._make_mock_ner_modules()):
            worker = NerWorker(input_path, tmp_path, self._MODEL_PATH, "cpu", batch_size=4)
            with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
                worker.start()

        output_path: Path = blocker.args[0]
        assert output_path.exists()
        assert output_path.name == "ner_results.json"

        results = json.loads(output_path.read_text(encoding="utf-8"))
        assert len(results) == 1
        assert "entities" in results[0]
        assert isinstance(results[0]["entities"], list)

    def test_emits_progress_per_record(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import NerWorker

        input_data = [
            {"_control_number": "REC001", "notes": ["text one"], "colophon_text": ""},
            {"_control_number": "REC002", "notes": ["text two"], "colophon_text": ""},
        ]
        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")

        progress_values: list[int] = []

        with patch.dict(sys.modules, self._make_mock_ner_modules()):
            worker = NerWorker(input_path, tmp_path, self._MODEL_PATH, "cpu", batch_size=4)
            worker.progress.connect(progress_values.append)  # type: ignore[attr-defined]
            with qtbot.waitSignal(worker.finished, timeout=30_000):  # type: ignore[attr-defined]
                worker.start()

        assert len(progress_values) == 2  # one per record
        assert progress_values[-1] == 100

    def test_empty_input_emits_error(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import NerWorker

        empty_path = tmp_path / "empty.json"
        empty_path.write_text("[]", encoding="utf-8")

        with patch.dict(sys.modules, self._make_mock_ner_modules()):
            worker = NerWorker(empty_path, tmp_path, self._MODEL_PATH, "cpu", batch_size=4)
            with qtbot.waitSignal(worker.error, timeout=10_000) as blocker:  # type: ignore[attr-defined]
                worker.start()

        assert blocker.args[0]

    def test_ner_worker_runs_in_qthread_without_crash(self, qtbot: object, tmp_path: Path) -> None:
        """NerWorker must run in QThread without crash — catches PyTorch/threading issues.

        Real crashes occur when PyTorch models run in QThread due to:
        1. Thread-safety issues with model forward passes
        2. Signal emission during model inference
        3. Memory issues with model loading/unloading

        This test uses mocked models to verify the threading infrastructure works.
        """
        from mhm_pipeline.controller.workers import NerWorker

        input_data = [
            {"_control_number": "REC001", "notes": ["text one"], "colophon_text": ""},
            {"_control_number": "REC002", "notes": ["text two"], "colophon_text": ""},
            {"_control_number": "REC003", "notes": ["text three"], "colophon_text": ""},
        ]
        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")

        # Track signals
        progress_values: list[int] = []
        log_lines: list[str] = []
        finished_paths: list[Path] = []
        error_msgs: list[str] = []

        with patch.dict(sys.modules, self._make_mock_ner_modules()):
            worker = NerWorker(input_path, tmp_path, self._MODEL_PATH, "cpu", batch_size=4)

            worker.progress.connect(progress_values.append)
            worker.log_line.connect(log_lines.append)
            worker.finished.connect(finished_paths.append)
            worker.error.connect(error_msgs.append)

            # Run in actual QThread
            with qtbot.waitSignal(worker.finished, timeout=30_000):  # type: ignore[attr-defined]
                worker.start()

        # Verify no error was emitted
        assert not error_msgs, f"NerWorker emitted error: {error_msgs}"
        assert finished_paths, "NerWorker did not emit finished"

        # Verify output file exists
        output_path = finished_paths[0]
        assert output_path.exists(), f"Output file not created: {output_path}"

        # Verify all records processed
        results = json.loads(output_path.read_text(encoding="utf-8"))
        assert len(results) == 3, f"Expected 3 records, got {len(results)}"

        # Verify progress reached 100%
        assert progress_values, "No progress signals emitted"
        assert progress_values[-1] == 100, "Progress did not reach 100%"


# ── Stage 2: Authority Matching ───────────────────────────────────────────────


class TestAuthorityWorker:
    """AuthorityWorker takes MARC extract (stage 0) as primary input and
    optionally merges NER results (stage 1) before authority matching.
    """

    def test_produces_enriched_json(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import AuthorityWorker

        # MARC extract as primary input, NER as secondary
        marc_data = [{"_control_number": "990000836520205171"}]
        marc_path = tmp_path / "marc_extracted.json"
        marc_path.write_text(json.dumps(marc_data, ensure_ascii=False), encoding="utf-8")

        ner_path = _ner_json(tmp_path)
        worker = AuthorityWorker(
            input_path=marc_path,
            output_dir=tmp_path,
            ner_path=ner_path,
            enable_viaf=False,
            enable_kima=False,
        )
        with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        output_path: Path = blocker.args[0]
        assert output_path.exists()
        assert output_path.name == "authority_enriched.json"

        records = json.loads(output_path.read_text(encoding="utf-8"))
        assert len(records) == 1
        assert "entities" in records[0]

    def test_authority_id_added_when_matched(self, qtbot: object, tmp_path: Path) -> None:
        """If MazalMatcher returns an ID for a person, it should appear in output."""
        from mhm_pipeline.controller.workers import AuthorityWorker

        marc_data = [{"_control_number": "990000836520205171"}]
        marc_path = tmp_path / "marc_extracted.json"
        marc_path.write_text(json.dumps(marc_data, ensure_ascii=False), encoding="utf-8")

        ner_path = _ner_json(tmp_path)

        mock_matcher = MagicMock()
        mock_matcher.match_person.return_value = "NLI12345"

        with patch("converter.authority.mazal_matcher.MazalMatcher", return_value=mock_matcher):
            worker = AuthorityWorker(
                marc_path,
                tmp_path,
                ner_path=ner_path,
                enable_viaf=False,
                enable_kima=False,
            )
            with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
                worker.start()

        records = json.loads(Path(blocker.args[0]).read_text(encoding="utf-8"))
        entity = records[0]["entities"][0]
        assert entity.get("mazal_id") == "NLI12345"

    def test_empty_entities_still_finishes(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import AuthorityWorker

        data = [{"_control_number": "99001"}]
        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        worker = AuthorityWorker(
            input_path,
            tmp_path,
            ner_path=None,
            enable_viaf=False,
            enable_kima=False,
        )
        with qtbot.waitSignal(worker.finished, timeout=30_000):  # type: ignore[attr-defined]
            worker.start()

    def test_place_matching_uses_marc_data(self, qtbot: object, tmp_path: Path) -> None:
        """MARC records with related_places should trigger KIMA matching."""
        from mhm_pipeline.controller.workers import AuthorityWorker

        marc_data = [
            {
                "_control_number": "990000836520205171",
                "related_places": ["ירושלים"],
            }
        ]
        marc_path = tmp_path / "marc_extracted.json"
        marc_path.write_text(json.dumps(marc_data, ensure_ascii=False), encoding="utf-8")

        mock_kima = MagicMock()
        mock_kima.match_place.return_value = "https://www.wikidata.org/entity/Q1218"

        with patch("converter.authority.kima_matcher.KimaMatcher", return_value=mock_kima):
            worker = AuthorityWorker(
                marc_path,
                tmp_path,
                ner_path=None,
                enable_viaf=False,
                enable_kima=True,
            )
            with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
                worker.start()

        records = json.loads(Path(blocker.args[0]).read_text(encoding="utf-8"))
        assert records[0].get("kima_places"), "Expected KIMA place matches"

    @pytest.mark.skipif(IN_CI, reason="Mazal DB mock patch unreliable in CI")
    def test_marc_name_fields_100_110_111_700_710_711_matched(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """AuthorityWorker should match all MARC name fields: 100, 110, 111, 700, 710, 711."""
        from mhm_pipeline.controller.workers import AuthorityWorker

        # MARC extract with all name field types as primary input
        marc_data = [
            {
                "_control_number": "990000836520205171",
                "authors": [
                    {"name": "משה בן יצחק", "type": "person"},  # 100
                    {"name": "הקהילה הקדושה", "type": "organization"},  # 110
                    {"name": "כנסת הגדולים", "type": "meeting"},  # 111
                ],
                "contributors": [
                    {"name": "דוד המלך", "type": "person"},  # 700
                    {"name": "בית המדרש", "type": "organization"},  # 710
                    {"name": "ועידת הרבנים", "type": "meeting"},  # 711
                ],
            }
        ]
        marc_path = tmp_path / "marc_extracted.json"
        marc_path.write_text(json.dumps(marc_data, ensure_ascii=False), encoding="utf-8")

        mock_mazal = MagicMock()
        mock_mazal.match_person.return_value = "NLI_TEST_123"
        mock_mazal.is_available = True

        with patch("converter.authority.mazal_matcher.MazalMatcher", return_value=mock_mazal):
            worker = AuthorityWorker(
                marc_path,
                tmp_path,
                ner_path=None,
                enable_viaf=False,
                enable_kima=False,
            )
            with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
                worker.start()

        records = json.loads(Path(blocker.args[0]).read_text(encoding="utf-8"))

        # Verify marc_authority_matches exists and has entries
        marc_matches = records[0].get("marc_authority_matches", [])
        assert len(marc_matches) == 6, f"Expected 6 name field matches, got {len(marc_matches)}"

        # Verify each field label is present
        field_labels = {m.get("field") for m in marc_matches}
        assert "100/110/111" in field_labels, "Missing 100/110/111 field label"
        assert "700/710/711" in field_labels, "Missing 700/710/711 field label"


# ── Stage 2 utility: Mazal Index Builder ─────────────────────────────────────


class TestMazalIndexWorker:
    def test_emits_error_when_no_xml_files(self, qtbot: object, tmp_path: Path) -> None:
        """Empty directory → error signal, no crash."""
        from mhm_pipeline.controller.workers import MazalIndexWorker

        worker = MazalIndexWorker(xml_dir=tmp_path, db_path=tmp_path / "test.db")
        with qtbot.waitSignal(worker.error, timeout=10_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        assert "NLIAUT" in blocker.args[0]

    @pytest.mark.skipif(
        IN_CI or not XML_DIR.exists() or not list(XML_DIR.glob("NLIAUT*.xml")),
        reason="NLI XML files not present or CI environment",
    )
    def test_builds_index_from_xml(self, qtbot: object, tmp_path: Path) -> None:
        """Smoke test: processes first XML file, emits finished with a non-empty DB."""
        import sqlite3

        from mhm_pipeline.controller.workers import MazalIndexWorker

        # Point at a single-file sub-dir to keep the test fast
        single_xml = sorted(XML_DIR.glob("NLIAUT*.xml"))[0]
        single_dir = tmp_path / "xml"
        single_dir.mkdir()
        import shutil

        shutil.copy(single_xml, single_dir / single_xml.name)

        db_path = tmp_path / "mazal_test.db"
        worker = MazalIndexWorker(xml_dir=single_dir, db_path=db_path)

        log_lines: list[str] = []
        worker.log_line.connect(log_lines.append)  # type: ignore[attr-defined]

        with qtbot.waitSignal(worker.finished, timeout=300_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        assert blocker.args[0] == db_path
        assert db_path.exists()
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM name_index").fetchone()[0]
        conn.close()
        assert count > 0, "Index was built but contains no name variants"
        assert any("records" in line for line in log_lines)


# ── Stage 2 utility: KIMA Index Builder ──────────────────────────────────────


class TestKimaIndexWorker:
    def test_emits_error_when_no_tsv_files(self, qtbot: object, tmp_path: Path) -> None:
        """Empty directory → error signal, no crash."""
        from mhm_pipeline.controller.workers import KimaIndexWorker

        worker = KimaIndexWorker(tsv_dir=tmp_path, db_path=tmp_path / "test.db")
        with qtbot.waitSignal(worker.error, timeout=10_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        assert "KIMA places file" in blocker.args[0] or blocker.args[0]

    @pytest.mark.skipif(
        not KIMA_TSV_DIR.exists() or not list(KIMA_TSV_DIR.glob("*Kima places*")),
        reason="KIMA TSV files not present",
    )
    def test_builds_index_from_tsvs(self, qtbot: object, tmp_path: Path) -> None:
        """Smoke test: builds KIMA index, emits finished, DB is non-empty."""
        import sqlite3

        from mhm_pipeline.controller.workers import KimaIndexWorker

        db_path = tmp_path / "kima_test.db"
        worker = KimaIndexWorker(tsv_dir=KIMA_TSV_DIR, db_path=db_path)

        progress_vals: list[int] = []
        log_lines: list[str] = []
        worker.progress.connect(progress_vals.append)  # type: ignore[attr-defined]
        worker.log_line.connect(log_lines.append)  # type: ignore[attr-defined]

        with qtbot.waitSignal(worker.finished, timeout=600_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        assert blocker.args[0] == db_path
        assert db_path.exists()
        conn = sqlite3.connect(str(db_path))
        places = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        names = conn.execute("SELECT COUNT(*) FROM name_index").fetchone()[0]
        conn.close()
        assert places > 10_000, f"Expected >10k places, got {places}"
        assert names > places, "Should have more name variants than places"
        assert 100 in progress_vals

    @pytest.mark.skipif(
        not KIMA_TSV_DIR.exists() or not list(KIMA_TSV_DIR.glob("*Kima places*")),
        reason="KIMA TSV files not present",
    )
    def test_kima_matcher_finds_jerusalem(self, tmp_path: Path) -> None:
        """After building, KimaMatcher should resolve ירושלים to a Wikidata URI."""
        from converter.authority.kima_index import build_kima_index
        from converter.authority.kima_matcher import KimaMatcher

        db_path = str(tmp_path / "kima.db")
        build_kima_index(str(KIMA_TSV_DIR), db_path)

        matcher = KimaMatcher(index_path=db_path)
        uri = matcher.match_place("ירושלים")
        matcher.close()

        assert uri is not None, "ירושלים should resolve in KIMA"
        assert "wikidata.org" in uri or "viaf.org" in uri


# ── Stage 3: RDF Build ────────────────────────────────────────────────────────


class TestRdfBuildWorker:
    @INPUT_FILES
    def test_produces_ttl_file(self, qtbot: object, tmp_path: Path, input_file: Path) -> None:
        from mhm_pipeline.controller.workers import RdfBuildWorker

        worker = RdfBuildWorker(input_file, tmp_path)
        with qtbot.waitSignal(worker.finished, timeout=120_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        output_path: Path = blocker.args[0]
        assert output_path.exists()
        assert output_path.suffix == ".ttl"

        content = output_path.read_text(encoding="utf-8")
        assert "@prefix" in content or "PREFIX" in content, "TTL has no namespace declarations"

    @INPUT_FILES
    def test_ttl_contains_triples(self, qtbot: object, tmp_path: Path, input_file: Path) -> None:
        from mhm_pipeline.controller.workers import RdfBuildWorker

        worker = RdfBuildWorker(input_file, tmp_path)
        with qtbot.waitSignal(worker.finished, timeout=120_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        from rdflib import Graph

        g = Graph()
        g.parse(blocker.args[0], format="turtle")
        assert len(g) > 0, "Serialised TTL graph has no triples"

    def test_tsv_builds_more_triples_than_single_mrc(self, qtbot: object, tmp_path: Path) -> None:
        """897-record TSV should produce more triples than a single MRC record."""
        from rdflib import Graph

        from mhm_pipeline.controller.workers import RdfBuildWorker

        mrc_dir = tmp_path / "mrc"
        tsv_dir = tmp_path / "tsv"
        mrc_dir.mkdir()
        tsv_dir.mkdir()

        w_mrc = RdfBuildWorker(MRC_FILE, mrc_dir)
        with qtbot.waitSignal(w_mrc.finished, timeout=60_000) as b_mrc:  # type: ignore[attr-defined]
            w_mrc.start()

        w_tsv = RdfBuildWorker(TSV_FILE, tsv_dir)
        with qtbot.waitSignal(w_tsv.finished, timeout=120_000) as b_tsv:  # type: ignore[attr-defined]
            w_tsv.start()

        g_mrc = Graph()
        g_mrc.parse(b_mrc.args[0], format="turtle")

        g_tsv = Graph()
        g_tsv.parse(b_tsv.args[0], format="turtle")

        assert len(g_tsv) > len(g_mrc), (
            f"TSV graph ({len(g_tsv)} triples) should have more triples "
            f"than single MRC ({len(g_mrc)} triples)"
        )

    @INPUT_FILES
    def test_emits_progress_100(self, qtbot: object, tmp_path: Path, input_file: Path) -> None:
        from mhm_pipeline.controller.workers import RdfBuildWorker

        worker = RdfBuildWorker(input_file, tmp_path)
        progress_values: list[int] = []
        worker.progress.connect(progress_values.append)  # type: ignore[attr-defined]

        with qtbot.waitSignal(worker.finished, timeout=120_000):  # type: ignore[attr-defined]
            worker.start()

        assert 100 in progress_values


# ── Stage 4: SHACL Validate ───────────────────────────────────────────────────


class TestShaclValidateWorker:
    @INPUT_FILES
    def test_produces_report_file(self, qtbot: object, tmp_path: Path, input_file: Path) -> None:
        from mhm_pipeline.controller.workers import RdfBuildWorker, ShaclValidateWorker

        rdf_worker = RdfBuildWorker(input_file, tmp_path)
        with qtbot.waitSignal(rdf_worker.finished, timeout=120_000) as rdf_blocker:  # type: ignore[attr-defined]
            rdf_worker.start()

        worker = ShaclValidateWorker(rdf_blocker.args[0], SHAPES_FILE, tmp_path)
        with qtbot.waitSignal(worker.finished, timeout=120_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        report_path: Path = blocker.args[0]
        assert report_path.exists()
        assert report_path.name == "shacl_report.txt"
        assert "Conforms:" in report_path.read_text(encoding="utf-8")

    @INPUT_FILES
    def test_report_contains_conforms_line(
        self, qtbot: object, tmp_path: Path, input_file: Path
    ) -> None:
        from mhm_pipeline.controller.workers import RdfBuildWorker, ShaclValidateWorker

        rdf_worker = RdfBuildWorker(input_file, tmp_path)
        with qtbot.waitSignal(rdf_worker.finished, timeout=120_000) as rdf_blocker:  # type: ignore[attr-defined]
            rdf_worker.start()

        shacl_worker = ShaclValidateWorker(rdf_blocker.args[0], SHAPES_FILE, tmp_path)
        with qtbot.waitSignal(shacl_worker.finished, timeout=120_000) as blocker:  # type: ignore[attr-defined]
            shacl_worker.start()

        text = Path(blocker.args[0]).read_text(encoding="utf-8")
        first_line = text.splitlines()[0]
        assert first_line.startswith("Conforms:"), f"Unexpected first line: {first_line!r}"

    def test_invalid_ttl_emits_error(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import ShaclValidateWorker

        bad_ttl = tmp_path / "bad.ttl"
        bad_ttl.write_text("this is not valid turtle content @@@@", encoding="utf-8")

        worker = ShaclValidateWorker(bad_ttl, SHAPES_FILE, tmp_path)
        with qtbot.waitSignal(worker.error, timeout=30_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        assert blocker.args[0]


# ── Stage 5: Wikidata Upload (stub) ───────────────────────────────────────────


class TestWikidataUploadWorker:
    @staticmethod
    def _make_enriched_json(tmp_path: Path) -> Path:
        """Create a minimal authority_enriched.json for WikidataUploadWorker."""
        json_path = tmp_path / "authority_enriched.json"
        data = [{"_control_number": "990000000000000001", "title": "Test MS"}]
        json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return json_path

    def test_stub_finishes_with_same_path(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import WikidataUploadWorker

        json_path = self._make_enriched_json(tmp_path)
        worker = WikidataUploadWorker(json_path, tmp_path, token="", dry_run=True)
        with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        assert blocker.args[0] is not None

    def test_stub_emits_log_line(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import WikidataUploadWorker

        json_path = self._make_enriched_json(tmp_path)
        worker = WikidataUploadWorker(json_path, tmp_path, token="test-token", dry_run=True)
        log_lines: list[str] = []
        worker.log_line.connect(log_lines.append)  # type: ignore[attr-defined]

        with qtbot.waitSignal(worker.finished, timeout=30_000):  # type: ignore[attr-defined]
            worker.start()

        assert log_lines, "No log_line emitted by Wikidata stub"

    def test_progress_reaches_100(self, qtbot: object, tmp_path: Path) -> None:
        from mhm_pipeline.controller.workers import WikidataUploadWorker

        json_path = self._make_enriched_json(tmp_path)
        worker = WikidataUploadWorker(json_path, tmp_path, token="", dry_run=True)
        progress_values: list[int] = []
        worker.progress.connect(progress_values.append)  # type: ignore[attr-defined]

        with qtbot.waitSignal(worker.finished, timeout=10_000):  # type: ignore[attr-defined]
            worker.start()

        assert 100 in progress_values


# ── PipelineController: chained stages 0 → 3 → 4 ────────────────────────────


class TestPipelineControllerChain:
    """Exercise PipelineController signal wiring across three real stages.

    Stages 1 (NER) and 2 (Authority) are skipped to avoid model deps;
    Stage 3 (RDF) receives the original MARC file via explicit input_path kwarg.
    """

    @pytest.fixture()
    def controller(self, tmp_path: Path) -> object:

        from mhm_pipeline.controller.pipeline_controller import PipelineController
        from mhm_pipeline.settings.settings_manager import SettingsManager

        settings = SettingsManager()
        settings.output_dir = tmp_path
        return PipelineController(settings)

    def test_stage_0_tsv_started_and_finished_signals(
        self, qtbot: object, controller: object, tmp_path: Path
    ) -> None:
        started: list[int] = []
        finished_stages: list[int] = []

        controller.stage_started.connect(started.append)  # type: ignore[attr-defined]
        controller.stage_finished.connect(lambda idx, _: finished_stages.append(idx))  # type: ignore[attr-defined]

        with qtbot.waitSignal(controller.stage_finished, timeout=60_000):  # type: ignore[attr-defined]
            controller.start_stage(0, input_path=TSV_FILE)  # type: ignore[attr-defined]

        assert 0 in started
        assert 0 in finished_stages

    def test_stage_3_tsv_builds_ttl(
        self, qtbot: object, controller: object, tmp_path: Path
    ) -> None:
        with qtbot.waitSignal(controller.stage_finished, timeout=120_000) as blocker:  # type: ignore[attr-defined]
            controller.start_stage(3, input_path=TSV_FILE)  # type: ignore[attr-defined]

        _stage_idx, output_path = blocker.args
        assert output_path.suffix == ".ttl"
        assert output_path.exists()

    def test_stage_4_validates_tsv_derived_ttl(
        self, qtbot: object, controller: object, tmp_path: Path
    ) -> None:
        """Full chain: TSV → RDF (stage 3) → SHACL (stage 4)."""
        with qtbot.waitSignal(controller.stage_finished, timeout=120_000) as rdf_blocker:  # type: ignore[attr-defined]
            controller.start_stage(3, input_path=TSV_FILE)  # type: ignore[attr-defined]
        ttl_path: Path = rdf_blocker.args[1]

        with qtbot.waitSignal(controller.stage_finished, timeout=120_000) as shacl_blocker:  # type: ignore[attr-defined]
            controller.start_stage(4, input_path=ttl_path, shapes_path=SHAPES_FILE)  # type: ignore[attr-defined]

        report_path: Path = shacl_blocker.args[1]
        assert report_path.exists()
        assert "Conforms:" in report_path.read_text(encoding="utf-8")

    def test_stage_error_emits_stage_error_signal(
        self, qtbot: object, controller: object, tmp_path: Path
    ) -> None:
        with qtbot.waitSignal(controller.stage_error, timeout=10_000) as blocker:  # type: ignore[attr-defined]
            controller.start_stage(0, input_path=tmp_path / "missing.tsv")  # type: ignore[attr-defined]

        stage_idx, msg = blocker.args
        assert stage_idx == 0
        assert msg


# ── Full GUI + QThread Signal Chain ──────────────────────────────────────────
# This test reproduces the exact crash scenario: Stage 0 worker running in a
# QThread with the full MainWindow processing progress signals. The crash was
# SIGABRT in the main thread when _on_stage_progress called set_progress on a
# widget that lacked the method.


class TestFullGuiProgressChain:
    """Run a real worker in QThread with full GUI receiving progress signals.

    This catches crashes caused by signal dispatch from worker → controller
    → MainWindow → panel widget. The original crash was:
      - Worker emits progress(int) from QThread
      - Main thread receives it, calls panel.stage_progress.set_progress(pct)
      - StageProgressWidget had no set_progress → AttributeError
      - PyQt6 escalates to QMessageLogger::fatal → abort() → SIGABRT
    """

    def test_stage_0_progress_reaches_gui_without_crash(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """Run MarcParseWorker in QThread with full MainWindow — must not SIGABRT."""
        from mhm_pipeline.controller.pipeline_controller import PipelineController
        from mhm_pipeline.gui.main_window import MainWindow
        from mhm_pipeline.settings.settings_manager import SettingsManager

        settings = SettingsManager()
        settings.output_dir = tmp_path
        controller = PipelineController(settings)
        window = MainWindow(settings, controller)
        window.show()

        finished_stages: list[int] = []
        error_stages: list[tuple[int, str]] = []

        controller.stage_finished.connect(lambda idx, _: finished_stages.append(idx))
        controller.stage_error.connect(lambda idx, msg: error_stages.append((idx, msg)))

        # Run Stage 0 on first 10 records — enough to emit many progress signals
        with qtbot.waitSignal(controller.stage_finished, timeout=60_000):  # type: ignore[attr-defined]
            controller.start_stage(
                0,
                input_path=TSV_FILE,
                output_dir=tmp_path,
                start=0,
                end=10,
            )

        assert not error_stages, f"Stage 0 error: {error_stages}"
        assert 0 in finished_stages

        # Verify output is valid
        output = tmp_path / "marc_extracted.json"
        assert output.exists()
        records = json.loads(output.read_text(encoding="utf-8"))
        assert len(records) == 10


# ── Real Model Inference Tests ────────────────────────────────────────────────
# These tests load the actual trained model files and verify real predictions.
# Skipped in CI (no GPU, no model files) and when model files are absent.
# They are intentionally slow (model load + inference) — run locally only.

_PROV_MODEL = _ROOT / "ner" / "provenance_ner_model.pt"
_CONT_MODEL = _ROOT / "ner" / "contents_ner_model.pt"
_GENRE_MODEL = _ROOT / "ner" / "genre_classifier_model.pt"
_MARC500_MODEL = _ROOT / "ner" / "marc500_classifier_model.pt"

_require_prov_model = pytest.mark.skipif(
    IN_CI or not _PROV_MODEL.exists(),
    reason="Provenance NER model not present or CI",
)

_require_marc500_model = pytest.mark.skipif(
    IN_CI or not _MARC500_MODEL.exists(),
    reason="MARC 500 classifier model not present or CI",
)
_require_cont_model = pytest.mark.skipif(
    IN_CI or not _CONT_MODEL.exists(),
    reason="Contents NER model not present or CI",
)
_require_genre_model = pytest.mark.skipif(
    IN_CI or not _GENRE_MODEL.exists(),
    reason="Genre classifier model not present or CI",
)


class TestNerModelsRealInference:
    """Verify all three NER models + genre classifier produce real predictions.

    These tests load actual .pt checkpoints and run forward passes on short
    Hebrew texts.  They guard against:
    - Broken checkpoint format (wrong keys, mismatched architecture)
    - Tokenisation regressions (wrong tokenizer, wrong max_length)
    - Silent all-zero output (threshold too high, wrong sigmoid polarity)
    - Wrong label mapping (id2label reversed or off-by-one)
    """

    # ── Provenance NER ────────────────────────────────────────────────

    @_require_prov_model
    def test_provenance_ner_loads_without_error(self) -> None:
        """Provenance NER checkpoint must load cleanly."""
        import sys  # noqa: PLC0415
        ner_dir = str(_ROOT / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415

        pipe = NERInferencePipeline(str(_PROV_MODEL), device="cpu")
        assert pipe is not None

    @_require_prov_model
    def test_provenance_ner_extracts_owner_from_marc_561(self) -> None:
        """Should extract OWNER entity from a typical MARC 561 provenance note."""
        import sys  # noqa: PLC0415
        ner_dir = str(_ROOT / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415

        pipe = NERInferencePipeline(str(_PROV_MODEL), device="cpu")
        # Classic MARC 561 provenance note: owned by a named person
        text = "נכתב עבור ר' משה בן אברהם הלוי בשנת ת\"פ"
        entities = pipe.process_text(text)
        assert isinstance(entities, list), "predict() must return a list"
        assert len(entities) > 0, (
            f"Provenance NER returned no entities for known provenance text: {text!r}"
        )
        labels = {e.get("label") or e.get("type") for e in entities}
        assert labels & {"OWNER", "DATE"}, (
            f"Expected OWNER or DATE entity; got labels: {labels}"
        )

    @_require_prov_model
    def test_provenance_ner_returns_empty_for_non_provenance_text(self) -> None:
        """Should not hallucinate OWNER/DATE entities from clearly irrelevant text."""
        import sys  # noqa: PLC0415
        ner_dir = str(_ROOT / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415

        pipe = NERInferencePipeline(str(_PROV_MODEL), device="cpu")
        # Pure layout description — no provenance signal
        text = "כתוב על קלף, שתי עמודות, עשרים ואחת שורות בעמוד"
        entities = pipe.process_text(text)
        owner_date = [e for e in entities if (e.get("label") or e.get("type")) in ("OWNER", "DATE")]
        assert len(owner_date) == 0, (
            f"False-positive OWNER/DATE entities in non-provenance text: {owner_date}"
        )

    # ── Contents NER ─────────────────────────────────────────────────

    @_require_cont_model
    def test_contents_ner_loads_without_error(self) -> None:
        """Contents NER checkpoint must load cleanly."""
        import sys  # noqa: PLC0415
        ner_dir = str(_ROOT / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415

        pipe = NERInferencePipeline(str(_CONT_MODEL), device="cpu")
        assert pipe is not None

    @_require_cont_model
    def test_contents_ner_extracts_work_from_marc_505(self) -> None:
        """Should extract WORK entity from a typical MARC 505 contents note."""
        import sys  # noqa: PLC0415
        ner_dir = str(_ROOT / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415

        pipe = NERInferencePipeline(str(_CONT_MODEL), device="cpu")
        # Typical MARC 505 contents entry
        text = "א. שולחן ערוך, אורח חיים, דף א ע\"א -- ב. תוספות יום טוב, דף כ ע\"ב"
        entities = pipe.process_text(text)
        assert isinstance(entities, list)
        assert len(entities) > 0, (
            f"Contents NER returned no entities for known contents text: {text!r}"
        )
        labels = {e.get("label") or e.get("type") for e in entities}
        assert labels & {"WORK", "WORK_AUTHOR", "FOLIO"}, (
            f"Expected WORK/WORK_AUTHOR/FOLIO entity; got labels: {labels}"
        )

    @_require_cont_model
    def test_contents_ner_extracts_folio_reference(self) -> None:
        """Should extract FOLIO entity from a contents note with folio markers."""
        import sys  # noqa: PLC0415
        ner_dir = str(_ROOT / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415

        pipe = NERInferencePipeline(str(_CONT_MODEL), device="cpu")
        text = "משנה תורה, דף א ע\"א -- דף קכ ע\"ב"
        entities = pipe.process_text(text)
        labels = {e.get("label") or e.get("type") for e in entities}
        assert labels & {"WORK", "FOLIO"}, (
            f"Expected WORK or FOLIO entity; got labels: {labels}"
        )

    # ── Genre Classifier ──────────────────────────────────────────────

    @_require_genre_model
    def test_genre_classifier_loads_without_error(self) -> None:
        """Genre classifier checkpoint must load cleanly."""
        from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415

        clf = GenreClassifier(str(_GENRE_MODEL), device="cpu")
        assert clf is not None
        assert len(clf.genre_label2id) == 9  # 8 genres + NOTA

    @_require_genre_model
    def test_genre_classifier_predicts_piyyutim_for_siddur(self) -> None:
        """A siddur with piyyut notes should be classified as Piyyutim."""
        from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415

        clf = GenreClassifier(str(_GENRE_MODEL), device="cpu")
        results = clf.predict(
            title="סידור תפילה",
            notes=["מכיל פיוטים לשבת ולחגים", "כתב יד עברי מן המאה הי\"ז"],
        )
        assert results, "Classifier returned no predictions for clear piyyutim text"
        top_genre = results[0][0]
        assert top_genre == "Piyyutim", (
            f"Expected 'Piyyutim' as top genre; got {top_genre!r} (full: {results})"
        )

    @_require_genre_model
    def test_genre_classifier_abstains_on_nota_text(self) -> None:
        """A purely codicological note should trigger abstention (NOTA / 'other')."""
        from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415

        clf = GenreClassifier(str(_GENRE_MODEL), device="cpu")
        results = clf.predict(
            title="כתב יד עברי",
            notes=["כתוב על קלף בכתב אשכנזי, שתי עמודות, שלושים שורות"],
        )
        # Either no genre predicted, or only "other" (NOTA)
        non_nota = [r for r in results if r[0] != "other"]
        assert len(non_nota) == 0, (
            f"Classifier should abstain on codicological-only text; got: {results}"
        )

    @_require_genre_model
    def test_genre_classifier_predicts_personal_correspondence(self) -> None:
        """A letter text should be classified as Personal correspondence."""
        from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415

        clf = GenreClassifier(str(_GENRE_MODEL), device="cpu")
        results = clf.predict(
            title="אגרת",
            notes=["מכתב ששלח הרב לתלמידו, בו הוא עונה על שאלותיו"],
        )
        assert results, "Classifier returned no predictions for letter text"
        genres = [r[0] for r in results if r[0] != "other"]
        assert "Personal correspondence" in genres, (
            f"Expected 'Personal correspondence'; got {genres}"
        )

    @_require_genre_model
    def test_genre_classifier_confidence_in_range(self) -> None:
        """All confidence scores must be in [0, 1]."""
        from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415

        clf = GenreClassifier(str(_GENRE_MODEL), device="cpu")
        results = clf.predict("פיוטים לשבת", ["מכיל פיוטים ופזמונות"])
        for genre, conf in results:
            assert 0.0 <= conf <= 1.0, (
                f"Confidence out of range for {genre!r}: {conf}"
            )


# ── MARC 500 Sentence Classifier — real-inference tests ──────────────────────
# Require ner/marc500_classifier_model.pt.  Skipped in CI and when model absent.


class TestMarc500ModelRealInference:
    """Verify the MARC 500 sentence classifier produces correct predictions.

    Guards against:
    - Broken checkpoint format or architecture mismatch
    - Per-class threshold stored as wrong type (float vs dict)
    - COLOPHON head silent failure (classic colophon sentence → False)
    - PROVENANCE head silent failure (ownership sentence → False)
    - False positives on clearly codicological sentences
    """

    @_require_marc500_model
    def test_marc500_classifier_loads_without_error(self) -> None:
        from converter.authority.marc500_classifier import Marc500Classifier  # noqa: PLC0415

        clf = Marc500Classifier(str(_MARC500_MODEL), device="cpu")
        assert clf is not None
        assert "COLOPHON" in clf.label2id
        # colophon-only model: threshold is a plain float (not a dict)
        assert isinstance(clf.threshold, float)

    @_require_marc500_model
    def test_colophon_detected_in_classic_colophon_sentence(self) -> None:
        """נשלם הספר ביד משה הסופר should be classified as COLOPHON."""
        from converter.authority.marc500_classifier import Marc500Classifier  # noqa: PLC0415

        clf = Marc500Classifier(str(_MARC500_MODEL), device="cpu")
        above_thr, conf = clf.is_colophon("נשלם הספר הזה ביד משה הסופר בשנת תפ")
        assert above_thr, (
            f"Expected COLOPHON=True for classic colophon sentence; conf={conf:.3f}"
        )

    @_require_marc500_model
    def test_physical_description_not_colophon(self) -> None:
        """Pure codicological note should NOT be classified as COLOPHON."""
        from converter.authority.marc500_classifier import Marc500Classifier  # noqa: PLC0415

        clf = Marc500Classifier(str(_MARC500_MODEL), device="cpu")
        above_thr, conf = clf.is_colophon("כתוב על קלף בכתב אשכנזי שתי עמודות")
        assert not above_thr, (
            f"Expected COLOPHON=False for codicological note; conf={conf:.3f}"
        )

    @_require_marc500_model
    def test_sentence_splitter_returns_nonempty_list(self) -> None:
        from mhm_pipeline.controller.workers import _split_marc500_sentences  # noqa: PLC0415

        note = "נשלם הספר הזה. כתוב על קלף בכתב אשכנזי."
        sents = _split_marc500_sentences(note)
        assert isinstance(sents, list)
        assert len(sents) >= 1

    @_require_marc500_model
    def test_classify_sentence_confidence_in_range(self) -> None:
        from converter.authority.marc500_classifier import Marc500Classifier  # noqa: PLC0415

        clf = Marc500Classifier(str(_MARC500_MODEL), device="cpu")
        result = clf.classify_sentence("נשלם הספר הזה ביד משה הסופר")
        # colophon-only model: only COLOPHON key
        assert "COLOPHON" in result
        above_thr, conf = result["COLOPHON"]
        assert isinstance(above_thr, bool)
        assert 0.0 <= conf <= 1.0, f"COLOPHON confidence out of range: {conf}"

    @_require_marc500_model
    def test_ner_worker_output_contains_ml_colophon_sentences_key(
        self, tmp_path: Path,
    ) -> None:
        """NerWorker must emit ml_colophon_sentences in its JSON output."""
        import json  # noqa: PLC0415

        from mhm_pipeline.controller.workers import NerWorker  # noqa: PLC0415

        # Minimal MARC extract with a colophon note
        record = {
            "_control_number": "TEST001",
            "title": "ספר בדיקה",
            "notes": ["נשלם הספר הזה ביד משה הסופר בשנת תפ"],
            "provenance": "",
            "contents": [],
        }
        marc_extract = tmp_path / "marc.json"
        marc_extract.write_text(json.dumps([record], ensure_ascii=False), encoding="utf-8")

        model_path = str(_ROOT / "ner" / "alexgoldberg" / "hebrew-manuscript-joint-ner-v2")
        hf_id = "alexgoldberg/hebrew-manuscript-joint-ner-v2"
        person_model = model_path if Path(model_path).exists() else hf_id

        worker = NerWorker(
            input_path=marc_extract,
            output_dir=tmp_path,
            model_path=person_model,
            device="cpu",
            batch_size=1,
        )
        from PyQt6.QtCore import Qt  # noqa: PLC0415

        finished_paths: list[Path] = []
        worker.finished.connect(
            lambda p: finished_paths.append(p),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        worker.wait(120_000)

        assert finished_paths, "NerWorker did not emit finished signal"
        results = json.loads(finished_paths[0].read_text(encoding="utf-8"))
        assert results, "NerWorker output is empty"
        assert "ml_colophon_sentences" in results[0], (
            "NerWorker output missing ml_colophon_sentences key"
        )

    @_require_marc500_model
    def test_merge_propagates_ml_colophon_into_record(self) -> None:
        """_merge_ner_into_records must copy ml_colophon_sentences into colophon_text."""
        from mhm_pipeline.controller.workers import AuthorityWorker  # noqa: PLC0415

        colophon_sent = "נשלם הספר הזה ביד משה הסופר"
        marc_records = [{"_control_number": "X1", "colophon_text": ""}]
        ner_by_cn = {"X1": {"entities": [], "ml_colophon_sentences": [colophon_sent]}}

        AuthorityWorker._merge_ner_into_records(marc_records, ner_by_cn)
        assert marc_records[0].get("colophon_text") == colophon_sent


# ── Wikidata Preview Panel Tests ──────────────────────────────────────────────


class TestWikidataPreviewPanel:
    """Verify the WikidataPreviewPanel interactive review screen.

    Guards against:
    - Panel fails to initialize (import error, missing Qt deps)
    - load_authority_output() fails to populate the record list
    - _extract_fields() misidentifies source types (ML/NER/MARC/authority)
    - In-cell edits not tracked in _edits dict
    - _apply_edits_to_records() not propagating title/date/language changes
    - continue_clicked signal not emitted with correct output path
    - Stage count mismatch (must be 7, not 6)
    """

    def test_panel_initializes_without_error(self, qtbot: object) -> None:
        """WikidataPreviewPanel must construct without raising."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import WikidataPreviewPanel

        panel = WikidataPreviewPanel()
        assert panel is not None

    def test_load_authority_output_populates_record_list(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """load_authority_output() must populate the left QListWidget."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import WikidataPreviewPanel

        records = [
            {"_control_number": "A001", "title": "ספר ראשון"},
            {"_control_number": "A002", "title": "ספר שני"},
        ]
        json_path = tmp_path / "authority_enriched.json"
        json_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

        panel = WikidataPreviewPanel()
        panel.load_authority_output(json_path)

        assert panel._record_list.count() == 2
        assert panel._continue_btn.isEnabled()

    def test_load_authority_output_selects_first_record(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """After loading, the first record should be selected and table populated."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import WikidataPreviewPanel

        records = [{"_control_number": "B001", "title": "ספר בדיקה", "date": "מאה י\"ז"}]
        json_path = tmp_path / "authority_enriched.json"
        json_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

        panel = WikidataPreviewPanel()
        panel.load_authority_output(json_path)

        assert panel._table.rowCount() > 0, "Table must be populated after loading first record"

    def test_extract_fields_colophon_ml_source(self, qtbot: object) -> None:
        """_extract_fields must return 'Colophon ML' source when ml_colophon_sentences present."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import _extract_fields

        record = {
            "_control_number": "C001",
            "colophon_text": "נשלם הספר הזה ביד משה הסופר",
            "ml_colophon_sentences": ["נשלם הספר הזה ביד משה הסופר"],
        }
        fields = _extract_fields(record)
        inscription = [(p, l, v, s) for p, l, v, s in fields if p == "P1684"]
        assert inscription, "P1684 (inscription) must appear when colophon_text is non-empty"
        assert inscription[0][3] == "Colophon ML", (
            f"Expected source='Colophon ML' when ml_colophon_sentences present; "
            f"got {inscription[0][3]!r}"
        )

    def test_extract_fields_colophon_marc_source_without_ml(self, qtbot: object) -> None:
        """Without ml_colophon_sentences, colophon_text source must be 'MARC'."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import _extract_fields

        record = {
            "_control_number": "C002",
            "colophon_text": "נשלם בשנת תפ",
        }
        fields = _extract_fields(record)
        inscription = [(p, l, v, s) for p, l, v, s in fields if p == "P1684"]
        assert inscription, "P1684 must appear when colophon_text is non-empty"
        assert inscription[0][3] == "MARC", (
            f"Expected source='MARC' without ml_colophon_sentences; got {inscription[0][3]!r}"
        )

    def test_extract_fields_ner_entity_source(self, qtbot: object) -> None:
        """Person NER entities must surface as source='Person NER' on P50."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import _extract_fields

        record = {
            "_control_number": "D001",
            "entities": [
                {
                    "text": "משה בן יצחק",
                    "source": "person_ner",
                    "label": "PERSON",
                    "role": "AUTHOR",
                }
            ],
        }
        fields = _extract_fields(record)
        ner_fields = [(p, l, v, s) for p, l, v, s in fields if s == "Person NER"]
        assert ner_fields, "Expected at least one field with source='Person NER'"

    def test_extract_fields_viaf_authority_source(self, qtbot: object) -> None:
        """MARC authority match with viaf_uri must surface as source='VIAF'."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import _extract_fields

        record = {
            "_control_number": "E001",
            "marc_authority_matches": [
                {
                    "display_name": "שלמה בן אברהם",
                    "role": "AUTHOR",
                    "viaf_uri": "http://viaf.org/viaf/12345",
                }
            ],
        }
        fields = _extract_fields(record)
        viaf_fields = [(p, l, v, s) for p, l, v, s in fields if s == "VIAF"]
        assert viaf_fields, "Expected field with source='VIAF' for authority match with viaf_uri"

    def test_apply_edits_propagates_title_change(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """_apply_edits_to_records must write title edit back into the records copy."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import WikidataPreviewPanel

        records = [{"_control_number": "F001", "title": "כותרת ישנה"}]
        json_path = tmp_path / "authority_enriched.json"
        json_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

        panel = WikidataPreviewPanel()
        panel.load_authority_output(json_path)

        # Simulate an edit: inject directly into _edits for the title row
        # Row 0 is P1476 (title) — key format is "0|P1476|title"
        panel._edits["F001"] = {"0|P1476|title": "כותרת חדשה"}

        edited = panel._apply_edits_to_records()
        assert edited[0]["title"] == "כותרת חדשה", (
            f"Expected edited title='כותרת חדשה'; got {edited[0]['title']!r}"
        )

    def test_continue_clicked_emits_with_reviewed_path(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """Clicking 'Save & Continue' must emit continue_clicked with the reviewed JSON path."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import WikidataPreviewPanel

        records = [{"_control_number": "G001", "title": "ספר גדול"}]
        json_path = tmp_path / "authority_enriched.json"
        json_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

        panel = WikidataPreviewPanel()
        panel.load_authority_output(json_path)

        emitted_paths: list[Path] = []
        panel.continue_clicked.connect(emitted_paths.append)

        panel._on_continue()

        assert emitted_paths, "continue_clicked was not emitted"
        assert emitted_paths[0].name == "authority_enriched_reviewed.json"
        assert emitted_paths[0].exists(), "Reviewed JSON file must be written to disk"

    def test_continue_writes_valid_json(self, qtbot: object, tmp_path: Path) -> None:
        """The reviewed JSON written by _on_continue must be valid and preserve records."""
        from mhm_pipeline.gui.panels.wikidata_preview_panel import WikidataPreviewPanel

        records = [
            {"_control_number": "H001", "title": "ספר א"},
            {"_control_number": "H002", "title": "ספר ב"},
        ]
        json_path = tmp_path / "authority_enriched.json"
        json_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

        panel = WikidataPreviewPanel()
        panel.load_authority_output(json_path)
        panel._on_continue()

        reviewed_path = tmp_path / "authority_enriched_reviewed.json"
        assert reviewed_path.exists()
        reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
        assert len(reviewed) == 2
        assert reviewed[0]["_control_number"] == "H001"

    def test_stage_count_is_seven(self, qtbot: object) -> None:
        """Pipeline has 7 stages (0-6) after inserting Wikidata Preview as stage 3."""
        from mhm_pipeline.gui.main_window import MainWindow, _STAGE_LABELS
        from mhm_pipeline.controller.pipeline_controller import PipelineController
        from mhm_pipeline.settings.settings_manager import SettingsManager

        settings = SettingsManager()
        controller = PipelineController(settings)
        window = MainWindow(settings, controller)

        assert len(window._panels) == 7, (
            f"Expected 7 stage panels; got {len(window._panels)}"
        )
        assert len(_STAGE_LABELS) == 7, (
            f"Expected 7 stage labels; got {len(_STAGE_LABELS)}"
        )
        assert _STAGE_LABELS[3] == "Wikidata Preview", (
            f"Stage 3 must be 'Wikidata Preview'; got {_STAGE_LABELS[3]!r}"
        )


# ── NER Worker — all 4 models end-to-end ──────────────────────────────────────
# Require all model checkpoints.  Skipped in CI and when any model is absent.
# Uses MPS if available (speeds up loading from ~5 min to ~45 s on M-series Mac).

_PERSON_MODEL_ID = "alexgoldberg/hebrew-manuscript-joint-ner-v2"

_require_all_ner_models = pytest.mark.skipif(
    IN_CI
    or not _PROV_MODEL.exists()
    or not _CONT_MODEL.exists()
    or not _MARC500_MODEL.exists(),
    reason="One or more NER model checkpoints missing, or running in CI",
)

# A synthetic MARC record designed to trigger all 4 model heads.
_ALL_MODELS_RECORD = {
    "_control_number": "TEST_ALL_MODELS_001",
    "title": "ספר בדיקה כולל",
    # notes: colophon sentence triggers MARC 500 classifier + person names for Person NER
    "notes": [
        "נשלם הספר הזה ביד משה הסופר בשנת ת\"פ בפאדובה",
        "כתוב על קלף, שתי עמודות",
    ],
    # provenance: classic owner mark for Provenance NER
    "provenance": "ציון בעלים: \"שלמה בכ\"ר יצחק הלוי\" (דף 1א)",
    # contents: work + folio for Contents NER
    "contents": [
        {"folio_range": "1א", "title": "שולחן ערוך אורח חיים", "sequence": 1},
        {"folio_range": "50ב", "title": "תוספות יום טוב", "sequence": 2},
    ],
    "colophon_text": "",
}


def _detect_device() -> str:
    """Return 'mps' if available, else 'cpu'."""
    try:
        import torch  # noqa: PLC0415
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


class TestNerWorkerAllModels:
    """End-to-end NerWorker QThread tests with all 4 real NER models loaded.

    Guards against:
    - Provenance / contents models silently skipped (prov_path resolved to "")
    - Model loading exception swallowed by outer try/except
    - Output JSON missing expected entity sources
    - MARC 500 classifier not being invoked on notes
    """

    @_require_all_ner_models
    def test_all_4_models_load_and_worker_finishes(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """NerWorker must finish (not error) when all 4 models are present."""
        from mhm_pipeline.controller.workers import NerWorker  # noqa: PLC0415

        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(
            json.dumps([_ALL_MODELS_RECORD], ensure_ascii=False), encoding="utf-8"
        )

        device = _detect_device()
        worker = NerWorker(
            input_path,
            tmp_path,
            _PERSON_MODEL_ID,
            device,
            batch_size=4,
        )

        errors: list[str] = []
        worker.error.connect(errors.append)  # type: ignore[attr-defined]

        # 5-minute timeout: loading 3 × 704 MB models on CPU can take 4 min
        with qtbot.waitSignal(worker.finished, timeout=300_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        assert not errors, f"NerWorker emitted error: {errors}"
        output = blocker.args[0]
        assert output.exists(), "NerWorker finished but output file not found"

    @_require_all_ner_models
    def test_person_ner_entities_present_in_output(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """Output must contain at least one entity with source='person_ner'."""
        from mhm_pipeline.controller.workers import NerWorker  # noqa: PLC0415

        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(
            json.dumps([_ALL_MODELS_RECORD], ensure_ascii=False), encoding="utf-8"
        )

        device = _detect_device()
        worker = NerWorker(input_path, tmp_path, _PERSON_MODEL_ID, device, batch_size=4)
        with qtbot.waitSignal(worker.finished, timeout=300_000):  # type: ignore[attr-defined]
            worker.start()

        results = json.loads((tmp_path / "ner_results.json").read_text(encoding="utf-8"))
        entities = results[0].get("entities", [])
        person_ents = [e for e in entities if e.get("source") == "person_ner"]
        assert person_ents, (
            "Expected at least one person_ner entity in output; "
            f"got sources: {[e.get('source') for e in entities]}"
        )

    @_require_all_ner_models
    def test_provenance_ner_entities_present_in_output(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """Output must contain at least one entity with source='provenance_ner'."""
        from mhm_pipeline.controller.workers import NerWorker  # noqa: PLC0415

        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(
            json.dumps([_ALL_MODELS_RECORD], ensure_ascii=False), encoding="utf-8"
        )

        device = _detect_device()
        worker = NerWorker(input_path, tmp_path, _PERSON_MODEL_ID, device, batch_size=4)
        with qtbot.waitSignal(worker.finished, timeout=300_000):  # type: ignore[attr-defined]
            worker.start()

        results = json.loads((tmp_path / "ner_results.json").read_text(encoding="utf-8"))
        entities = results[0].get("entities", [])
        prov_ents = [e for e in entities if e.get("source") == "provenance_ner"]
        assert prov_ents, (
            "Expected at least one provenance_ner entity in output — "
            "provenance NER model may not have been loaded. "
            f"Got sources: {list({e.get('source') for e in entities})}"
        )

    @_require_all_ner_models
    def test_contents_ner_entities_present_in_output(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """Output must contain at least one entity with source='contents_ner'."""
        from mhm_pipeline.controller.workers import NerWorker  # noqa: PLC0415

        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(
            json.dumps([_ALL_MODELS_RECORD], ensure_ascii=False), encoding="utf-8"
        )

        device = _detect_device()
        worker = NerWorker(input_path, tmp_path, _PERSON_MODEL_ID, device, batch_size=4)
        with qtbot.waitSignal(worker.finished, timeout=300_000):  # type: ignore[attr-defined]
            worker.start()

        results = json.loads((tmp_path / "ner_results.json").read_text(encoding="utf-8"))
        entities = results[0].get("entities", [])
        cont_ents = [e for e in entities if e.get("source") == "contents_ner"]
        assert cont_ents, (
            "Expected at least one contents_ner entity in output — "
            "contents NER model may not have been loaded. "
            f"Got sources: {list({e.get('source') for e in entities})}"
        )

    @_require_all_ner_models
    def test_marc500_colophon_sentences_in_output(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """Output must contain non-empty ml_colophon_sentences for a colophon note."""
        from mhm_pipeline.controller.workers import NerWorker  # noqa: PLC0415

        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(
            json.dumps([_ALL_MODELS_RECORD], ensure_ascii=False), encoding="utf-8"
        )

        device = _detect_device()
        worker = NerWorker(input_path, tmp_path, _PERSON_MODEL_ID, device, batch_size=4)
        with qtbot.waitSignal(worker.finished, timeout=300_000):  # type: ignore[attr-defined]
            worker.start()

        results = json.loads((tmp_path / "ner_results.json").read_text(encoding="utf-8"))
        ml_sents = results[0].get("ml_colophon_sentences", [])
        assert ml_sents, (
            "Expected ml_colophon_sentences to be non-empty for record with colophon note; "
            "MARC 500 classifier may not have been loaded or threshold too high."
        )

    @_require_all_ner_models
    def test_all_4_sources_present_in_combined_record(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        """The combined test record should yield entities from all 4 sources."""
        from mhm_pipeline.controller.workers import NerWorker  # noqa: PLC0415

        input_path = tmp_path / "marc_extracted.json"
        input_path.write_text(
            json.dumps([_ALL_MODELS_RECORD], ensure_ascii=False), encoding="utf-8"
        )

        device = _detect_device()
        worker = NerWorker(input_path, tmp_path, _PERSON_MODEL_ID, device, batch_size=4)
        with qtbot.waitSignal(worker.finished, timeout=300_000):  # type: ignore[attr-defined]
            worker.start()

        results = json.loads((tmp_path / "ner_results.json").read_text(encoding="utf-8"))
        rec = results[0]
        entities = rec.get("entities", [])
        sources_found = {e.get("source") for e in entities}
        ml_sents = rec.get("ml_colophon_sentences", [])

        missing: list[str] = []
        if "person_ner" not in sources_found:
            missing.append("person_ner")
        if "provenance_ner" not in sources_found:
            missing.append("provenance_ner")
        if "contents_ner" not in sources_found:
            missing.append("contents_ner")
        if not ml_sents:
            missing.append("ml_colophon_sentences (MARC 500 classifier)")

        assert not missing, (
            f"Missing outputs from {len(missing)} model(s): {missing}. "
            f"Sources present: {sources_found}. "
            f"ml_colophon_sentences: {ml_sents[:2]}"
        )
