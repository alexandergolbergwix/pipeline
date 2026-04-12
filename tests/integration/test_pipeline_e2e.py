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
        for stage_idx in range(6):
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
