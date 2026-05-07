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
    """NER stage tests use a sys.modules mock because the real models are large.

    The mock at the ``ner.inference_pipeline`` level only stops the joint
    Person/Provenance/Contents NER load. ``NerWorker.run()`` ALSO calls
    two module-level lazy singletons — ``_get_marc500_classifier()`` and
    ``_get_genre_classifier()`` — which independently load real ~700 MB
    ``.pt`` checkpoints from disk on first invocation. The first test
    triggers those loads; the second test re-enters the worker and the
    PyTorch deserialiser deadlocks against the still-extant tokeniser
    Rust workers from the first load (see investigator I1's report,
    2026-05-06). The autouse fixture below stubs both singletons to
    ``None`` for the duration of every test in this class so the multi-
    test session never touches the real ``.pt`` files.
    """

    _MODEL_PATH = "alexgoldberg/hebrew-manuscript-joint-ner-v2"

    @pytest.fixture(autouse=True)
    def _stub_aux_classifier_loaders(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Force aux classifier loaders to return None for the whole class.

        Without this, ``_get_marc500_classifier()`` / ``_get_genre_classifier()``
        execute ``torch.load(...)`` on real 700 MB checkpoints and the
        resulting tokeniser-Rust workers deadlock across consecutive
        tests in the same pytest process.
        """
        from mhm_pipeline.controller import workers as _workers_mod

        monkeypatch.setattr(_workers_mod, "_get_marc500_classifier", lambda: None)
        monkeypatch.setattr(_workers_mod, "_get_genre_classifier", lambda: None)
        # Also reset the module-level cached singletons so a previous
        # test's partially-loaded classifier doesn't leak through.
        monkeypatch.setattr(_workers_mod, "_MARC500_CLASSIFIER", "unloaded")
        monkeypatch.setattr(_workers_mod, "_GENRE_CLASSIFIER", "unloaded")

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


# ── Stage 2: 4-source authority chain (Mazal + VIAF + KIMA + Wikidata) ───────


class TestAuthorityWorker4SourceIntegration:
    """Tests for the 4-source authority chain (Mazal + VIAF + KIMA + Wikidata).

    INVARIANT — Wikidata coverage of Hebrew manuscript figures is incomplete.
    Many obscure scribes, owners, and minor authors are simply not in
    Wikidata yet. ``WikidataMatcher`` returning ``None`` for these entities
    is the expected baseline behaviour, NOT an error condition. The
    confidence ladder must continue to produce sensible results from the
    remaining 3 sources (Mazal/VIAF/KIMA) when Wikidata is silent.

    These tests verify that absence-from-Wikidata never:
    1. Causes a hard failure or exception
    2. Forces ``confidence: low`` (it just means ``has_wikidata: False``)
    3. Triggers ``wikidata_disagrees`` (absence != disagreement)
    4. Blocks the worker from emitting valid authority_enriched.json
    """

    @staticmethod
    def _marc_with_one_author(
        tmp_path: Path,
        name: str,
        control_number: str = "990000000000000001",
    ) -> Path:
        """Write a synthetic single-record MARC extract to tmp_path."""
        marc_data = [
            {
                "_control_number": control_number,
                "authors": [{"name": name, "type": "person"}],
            }
        ]
        marc_path = tmp_path / "marc_extracted.json"
        marc_path.write_text(json.dumps(marc_data, ensure_ascii=False), encoding="utf-8")
        return marc_path

    @staticmethod
    def _make_mazal_mock(
        match_id: str | None,
        preferred_name_lat: str | None = None,
        dates: str | None = None,
    ) -> MagicMock:
        """Build a MazalMatcher mock with the methods Stage 3 calls."""
        mock = MagicMock()
        mock.is_available = True
        mock.index_path = ":memory:"
        mock.match_person.return_value = match_id
        details: dict[str, str] = {}
        if preferred_name_lat:
            details["preferred_name_lat"] = preferred_name_lat
        if dates:
            details["dates"] = dates
        mock.get_person_details.return_value = details
        # Force nli_strict_mode._iter_candidates to return None so
        # Path-2 Levenshtein never iterates a MagicMock auto-attribute.
        mock.iter_person_candidates = None
        return mock

    @staticmethod
    def _make_viaf_mock(viaf_uri: str | None) -> MagicMock:
        """Build a VIAFMatcher mock that returns a fixed URI and empty cluster."""
        mock = MagicMock()
        mock.match_person.return_value = viaf_uri
        mock.get_cluster_identifiers.return_value = {
            "gnd": None, "lc": None, "isni": None, "bnf": None,
            "birth_date": None, "death_date": None,
        }
        mock.get_cluster_raw.return_value = {}
        return mock

    @staticmethod
    def _make_wikidata_mock(
        *,
        find_qid_by_viaf: str | None = None,
        find_qid_by_mazal: str | None = None,
        match_person: str | None = None,
        latin_only: bool = False,
    ) -> MagicMock:
        """Build a WikidataMatcher mock with the 4 public methods Stage 3 calls."""
        mock = MagicMock()
        mock.find_qid_by_viaf.return_value = find_qid_by_viaf
        mock.find_qid_by_mazal.return_value = find_qid_by_mazal
        mock.match_person.return_value = match_person
        mock.last_match_was_latin_only.return_value = latin_only
        return mock

    def test_three_sources_agree_high_confidence(
        self, qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mazal + VIAF + Wikidata all resolve to the same real-world entity.

        With a Latin preferred name from Mazal, sources>=2 + has_preferred_name_lat
        promotes the verdict to ``confidence == "high"``.
        """
        from mhm_pipeline.controller.workers import AuthorityWorker

        # Disable F2 SPARQL cross-check (still testable without real HTTP).
        monkeypatch.setenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", "1")

        marc_path = self._marc_with_one_author(tmp_path, "Maimonides, Moses")

        mazal_mock = self._make_mazal_mock(
            match_id="987007388484005171",
            preferred_name_lat="Maimonides, Moses",
            dates="1138-1204",
        )
        viaf_mock = self._make_viaf_mock("https://viaf.org/viaf/100184235")
        wd_mock = self._make_wikidata_mock(
            find_qid_by_viaf="Q127398",
            find_qid_by_mazal="Q127398",
        )

        with (
            patch(
                "converter.authority.mazal_matcher.MazalMatcher",
                return_value=mazal_mock,
            ),
            patch(
                "converter.authority.viaf_matcher.VIAFMatcher",
                return_value=viaf_mock,
            ),
            patch(
                "converter.authority.wikidata_matcher.WikidataMatcher",
                return_value=wd_mock,
            ),
        ):
            worker = AuthorityWorker(
                input_path=marc_path,
                output_dir=tmp_path,
                ner_path=None,
                enable_viaf=True,
                enable_kima=False,
            )
            with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
                worker.start()

        records = json.loads(Path(blocker.args[0]).read_text(encoding="utf-8"))
        marc_matches = records[0].get("marc_authority_matches", [])
        assert len(marc_matches) == 1, f"Expected 1 author match, got {len(marc_matches)}"
        match = marc_matches[0]
        assert match.get("wikidata_qid") == "Q127398"
        assert match.get("confidence") == "high"
        assert match.get("matched") == 1
        # 3 sources agree → no cross-source conflict flag
        assert "cross_source_conflict" not in match

    def test_wikidata_not_found_is_acceptable_3source_fallback(
        self, qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wikidata coverage of Hebrew manuscript figures is incomplete.

        A ``None`` from ``WikidataMatcher`` is the expected baseline for
        ~50%+ of provenance entities, NOT an error. The confidence ladder
        must still produce a usable verdict (medium when Mazal alone
        resolves with a Latin preferred name) and ``wikidata_disagrees``
        must NOT be flagged (absence != disagreement).
        """
        from mhm_pipeline.controller.workers import AuthorityWorker

        monkeypatch.setenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", "1")

        # Real 16th-c. owner — plausibly NOT in Wikidata yet.
        marc_path = self._marc_with_one_author(tmp_path, "אזכרי, אלעזר בן משה")

        mazal_mock = self._make_mazal_mock(
            match_id="987012345",
            preferred_name_lat="Azkari, Eleazar ben Moshe",
        )
        viaf_mock = self._make_viaf_mock(None)  # Not in VIAF either
        wd_mock = self._make_wikidata_mock(
            find_qid_by_viaf=None,
            find_qid_by_mazal=None,  # Not in Wikidata — the normal case
            match_person=None,        # Hebrew label search also empty
        )

        with (
            patch(
                "converter.authority.mazal_matcher.MazalMatcher",
                return_value=mazal_mock,
            ),
            patch(
                "converter.authority.viaf_matcher.VIAFMatcher",
                return_value=viaf_mock,
            ),
            patch(
                "converter.authority.wikidata_matcher.WikidataMatcher",
                return_value=wd_mock,
            ),
        ):
            worker = AuthorityWorker(
                input_path=marc_path,
                output_dir=tmp_path,
                ner_path=None,
                enable_viaf=True,
                enable_kima=False,
            )
            with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
                worker.start()

        records = json.loads(Path(blocker.args[0]).read_text(encoding="utf-8"))
        marc_matches = records[0].get("marc_authority_matches", [])
        assert len(marc_matches) == 1, f"Expected 1 author match, got {len(marc_matches)}"
        match = marc_matches[0]

        # Mazal hit preserved — the 3-source ladder still has signal.
        assert match.get("mazal_id") == "987012345"

        # No Wikidata QID — that's normal, not a failure mode.
        assert not match.get("wikidata_qid")

        # Mazal-only with Latin preferred name → 1-source rule = "medium".
        assert match.get("confidence") == "medium"

        # Not auto-approved at high (matched flag is 0 when not "high").
        assert match.get("matched") == 0

        # Absence != disagreement: the wikidata_disagrees flag MUST NOT fire.
        guard_flags = match.get("guard_flags", []) or []
        assert "wikidata_disagrees" not in guard_flags

    def test_cross_source_conflict_drops_to_low(
        self, qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mazal NLI ID and VIAF cluster point to DIFFERENT Wikidata QIDs.

        When triangulation through P214 (VIAF) and P8189 (NLI) lands on
        two distinct items, the sources contradict each other — the
        deterministic 5-guard layer cannot detect this without the
        4-source matrix probe (Step 9). Confidence must drop to "low"
        and ``cross_source_conflict`` must surface.
        """
        from mhm_pipeline.controller.workers import AuthorityWorker

        monkeypatch.setenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", "1")

        marc_path = self._marc_with_one_author(tmp_path, "Cohen, David")

        # NLI-strict mode (F4) calls ``mazal.match_person`` first and, on
        # a hit, skips the VIAF SRU lookup entirely. To exercise step 9
        # (cross-source conflict) we need BOTH viaf_uri AND mazal_id set,
        # which only happens when NLI-strict misses Path 1 and the
        # fallback ``_match_against_authorities`` runs. Returning None
        # on the first call (Path 1) and the real ID on the second call
        # (fallback) lets both branches resolve.
        mazal_mock = self._make_mazal_mock(
            match_id="987007111111111",
            preferred_name_lat="Cohen, David",
        )
        mazal_mock.match_person.side_effect = [None, "987007111111111"]
        viaf_mock = self._make_viaf_mock("https://viaf.org/viaf/100222222")
        # The Wikidata triangulation says VIAF=Q999999 but Mazal NLI=Q888888 —
        # two real-world entities, not one.
        wd_mock = self._make_wikidata_mock(
            find_qid_by_viaf="Q999999",
            find_qid_by_mazal="Q888888",
        )

        with (
            patch(
                "converter.authority.mazal_matcher.MazalMatcher",
                return_value=mazal_mock,
            ),
            patch(
                "converter.authority.viaf_matcher.VIAFMatcher",
                return_value=viaf_mock,
            ),
            patch(
                "converter.authority.wikidata_matcher.WikidataMatcher",
                return_value=wd_mock,
            ),
        ):
            worker = AuthorityWorker(
                input_path=marc_path,
                output_dir=tmp_path,
                ner_path=None,
                enable_viaf=True,
                enable_kima=False,
            )
            with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
                worker.start()

        records = json.loads(Path(blocker.args[0]).read_text(encoding="utf-8"))
        marc_matches = records[0].get("marc_authority_matches", [])
        assert len(marc_matches) == 1
        match = marc_matches[0]

        assert match.get("confidence") == "low", (
            f"cross-source conflict must drop confidence to low, got "
            f"{match.get('confidence')!r}"
        )
        # Either the True-only flag is set OR the guard_flags list contains it.
        flagged = (
            match.get("cross_source_conflict") is True
            or "cross_source_conflict" in (match.get("guard_flags") or [])
        )
        assert flagged, (
            "cross_source_conflict must surface either via the boolean field "
            "or the guard_flags list"
        )

    def test_wikidata_disabled_falls_back_cleanly(
        self, qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting MHM_DISABLE_WIKIDATA_CROSSCHECK=1 returns the legacy 2-source flow.

        The matcher's public methods short-circuit (return None) when the
        kill switch is set — proving the rollback path is clean. No
        ``wikidata_qid`` is populated; the 2-source ladder produces its
        verdict as it did before today's changes.
        """
        from mhm_pipeline.controller.workers import AuthorityWorker

        monkeypatch.setenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", "1")

        marc_path = self._marc_with_one_author(tmp_path, "Levi, Joseph")

        mazal_mock = self._make_mazal_mock(
            match_id="987007222222222",
            preferred_name_lat="Levi, Joseph",
        )
        viaf_mock = self._make_viaf_mock("https://viaf.org/viaf/100333333")

        # Use a REAL WikidataMatcher (not a mock) — its public methods
        # honour the env-var kill switch and return None without HTTP.
        from converter.authority.wikidata_matcher import WikidataMatcher

        real_wd = WikidataMatcher()
        # Wrap each public method with a spy so we can assert call count.
        real_wd.match_person = MagicMock(side_effect=real_wd.match_person)  # type: ignore[method-assign]

        with (
            patch(
                "converter.authority.mazal_matcher.MazalMatcher",
                return_value=mazal_mock,
            ),
            patch(
                "converter.authority.viaf_matcher.VIAFMatcher",
                return_value=viaf_mock,
            ),
            patch(
                "converter.authority.wikidata_matcher.WikidataMatcher",
                return_value=real_wd,
            ),
        ):
            worker = AuthorityWorker(
                input_path=marc_path,
                output_dir=tmp_path,
                ner_path=None,
                enable_viaf=True,
                enable_kima=False,
            )
            with qtbot.waitSignal(worker.finished, timeout=30_000) as blocker:  # type: ignore[attr-defined]
                worker.start()

        records = json.loads(Path(blocker.args[0]).read_text(encoding="utf-8"))
        marc_matches = records[0].get("marc_authority_matches", [])
        assert len(marc_matches) == 1, "Worker must complete with a match"
        match = marc_matches[0]

        # Step 5 (Hebrew label fallback) only fires when Mazal+VIAF are
        # both empty. Since Mazal+VIAF resolved here, match_person must
        # NOT have been called.
        assert real_wd.match_person.call_count == 0, (
            "WikidataMatcher.match_person must not be called when Mazal+VIAF "
            "already resolved (matcher honours kill switch + step ordering)"
        )

        # No wikidata_qid populated — clean 2-source rollback.
        assert not match.get("wikidata_qid")

        # 2-source ladder: Mazal + VIAF + Latin preferred name → "high".
        # (sources=2, has_preferred_name_lat=True → high)
        assert match.get("confidence") in ("high", "medium")

    def test_authority_editor_round_trip_with_wikidata(self, tmp_path: Path) -> None:
        """flatten → unflatten must preserve the wikidata_qid field.

        Locks the round-trip schema invariant so the GUI's edit-then-save
        cannot drop the new 4-source field.
        """
        from mhm_pipeline.gui.widgets.authority_editor import (
            flatten_authority_records,
            unflatten_rows_into_records,
        )

        original_records = [
            {
                "_control_number": "990000000000000001",
                "marc_authority_matches": [
                    {
                        "name": "Maimonides, Moses",
                        "role": "author",
                        "field": "100/110/111",
                        "confidence": "high",
                        "mazal_id": "987007388484005171",
                        "viaf_uri": "https://viaf.org/viaf/100184235",
                        "preferred_name_lat": "Maimonides, Moses",
                        "wikidata_qid": "Q127398",
                    }
                ],
                "entities": [],
            }
        ]

        rows = flatten_authority_records(original_records)
        assert len(rows) == 1, f"Expected 1 flat row, got {len(rows)}"
        row = rows[0]
        assert row["wikidata_qid"] == "Q127398", (
            "flatten must preserve wikidata_qid on flat rows"
        )

        # Mark approved (the GUI's save path drops un-approved rows).
        row["approved"] = True

        unflattened = unflatten_rows_into_records(rows, original_records)
        assert len(unflattened) == 1
        marc_matches = unflattened[0].get("marc_authority_matches") or []
        assert len(marc_matches) == 1
        assert marc_matches[0].get("wikidata_qid") == "Q127398", (
            "unflatten must restore wikidata_qid on the source dict — "
            "round-trip schema invariant"
        )


# ── Stage 2 utility: Mazal Index Builder ─────────────────────────────────────


class TestMazalIndexWorker:
    def test_emits_error_when_no_xml_files(self, qtbot: object, tmp_path: Path) -> None:
        """Empty directory → error signal, no crash."""
        from mhm_pipeline.controller.workers import MazalIndexWorker

        worker = MazalIndexWorker(xml_dir=tmp_path, db_path=tmp_path / "test.db")
        with qtbot.waitSignal(worker.error, timeout=10_000) as blocker:  # type: ignore[attr-defined]
            worker.start()

        assert "NLIAUT" in blocker.args[0]

    @pytest.mark.slow_models
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

    @pytest.mark.slow_models
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

    @pytest.mark.slow_models
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

    # Loads ~700 MB .pt checkpoints; hangs in multi-test pytest sessions.
    # Opt in with `pytest -m slow_models`.
    pytestmark = pytest.mark.slow_models

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

    # Loads the MARC 500 ~700 MB .pt checkpoint; hangs in multi-test pytest sessions.
    # Opt in with `pytest -m slow_models`.
    pytestmark = pytest.mark.slow_models

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
        """Pipeline has 6 stages — Wikidata Preview merged into Wikidata Studio."""
        from mhm_pipeline.gui.main_window import MainWindow, _STAGE_LABELS
        from mhm_pipeline.controller.pipeline_controller import PipelineController
        from mhm_pipeline.settings.settings_manager import SettingsManager

        settings = SettingsManager()
        controller = PipelineController(settings)
        window = MainWindow(settings, controller)

        assert len(window._panels) == 6, (
            f"Expected 6 stage panels; got {len(window._panels)}"
        )
        assert len(_STAGE_LABELS) == 6, (
            f"Expected 6 stage labels; got {len(_STAGE_LABELS)}"
        )
        assert _STAGE_LABELS[-1] == "Wikidata Studio", (
            f"Final stage must be 'Wikidata Studio'; got {_STAGE_LABELS[-1]!r}"
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

    # Loads all 4 NER / classifier checkpoints (~3 GB total);
    # hangs in multi-test pytest sessions. Opt in with `pytest -m slow_models`.
    pytestmark = pytest.mark.slow_models

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

# ── ExtractionEditor feature tests (approval, auto-rules, source-view, dropdowns) ──


class TestExtractionEditor:
    """End-to-end tests for the revamped ExtractionEditor surface.

    Covers:
      * Model columns (Record · Entity · Type · Role · Conf · Model Conf · Source · Approved · Actions)
      * Approved column behaviour (checkbox + flag + green wash)
      * Auto-approve rule builder — single + multi-condition, AND/OR, IN/NOT IN
      * View-source lookup (offset-correct + substring fallback)
      * Type + Role delegates return QComboBox editors
      * Dark-mode type colours are readable (dark bg + light fg)
    """

    @pytest.fixture
    def _qapp(self) -> object:
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

        return QApplication.instance() or QApplication(sys.argv)

    @pytest.fixture
    def _sample_records(self) -> list[dict]:
        """Small synthetic fixture — two records covering all 4 NER sources."""
        return [
            {
                "_control_number": "990000415290205171",
                "text": 'קובץ גדול של שו"ת חכמים מאיטליה. ר\' שלמה הלוי (דף 47א). '
                        'כתב יד יצחק בן אהרן. מאת הרופא אליהו מונטאלטו.',
                "entities": [
                    {
                        "person": "שלמה הלוי",
                        "start": 31,
                        "end": 41,
                        "role": "AUTHOR",
                        "confidence": 0.93,
                        "source": "person_ner",
                    },
                    {
                        "person": "יצחק בן אהרן",
                        "start": 60,
                        "end": 72,
                        "role": "TRANSCRIBER",
                        "confidence": 0.62,
                        "source": "person_ner",
                    },
                    {
                        "text": "אליהו מונטאלטו",
                        "start": 78,
                        "end": 92,
                        "type": "OWNER",
                        "confidence": 0.85,
                        "source": "provenance_ner",
                    },
                    {
                        "text": "דף 47",
                        "start": 43,
                        "end": 49,
                        "type": "FOLIO",
                        "confidence": 0.99,
                        "source": "contents_ner",
                    },
                ],
                "ml_colophon_sentences": [],
            },
            {
                "_control_number": "990000908210205171",
                "text": 'ציון בעלים: "שלמה בכ"ר אליא משה" (דף 1א). כתוב על קלף.',
                "entities": [
                    {
                        "text": "שלמה בכ\"ר אליא משה",
                        "start": 13,
                        "end": 31,
                        "type": "OWNER",
                        "confidence": 0.77,
                        "source": "provenance_ner",
                    },
                ],
                "ml_colophon_sentences": [],
            },
        ]

    def test_editor_loads_and_exposes_new_columns(self, _qapp: object, _sample_records: list[dict]) -> None:
        """Model should expose all 9 columns and load every entity row."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor, COL_APPROVED, COL_ROLE, COL_ACTIONS, MODEL_CONF,
        )

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        assert editor._model.columnCount() == 9
        assert editor._model.rowCount() == 5
        # Required new columns exist at expected indices
        assert COL_ROLE == 3
        assert MODEL_CONF == 5
        assert COL_APPROVED == 7
        assert COL_ACTIONS == 8

    def test_approved_column_is_user_checkable(self, _qapp: object, _sample_records: list[dict]) -> None:
        """The Approved column should be a user-checkable cell, not editable text."""
        from PyQt6.QtCore import Qt  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor, COL_APPROVED,
        )

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        idx = editor._model.index(0, COL_APPROVED)
        flags = editor._model.flags(idx)
        assert flags & Qt.ItemFlag.ItemIsUserCheckable
        # Toggle via setData + CheckStateRole
        editor._model.setData(idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        assert editor._model._entities[0]["approved"] is True
        editor._model.setData(idx, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        assert editor._model._entities[0]["approved"] is False

    def test_auto_approve_single_rule_confidence(self, _qapp: object, _sample_records: list[dict]) -> None:
        """Rule: confidence >= 0.80 should approve the matching subset."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor, evaluate_rules,
        )

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        rules = [{"field": "confidence", "op": ">=", "value": 0.80}]
        matched = [i for i, e in enumerate(editor._model._entities)
                   if evaluate_rules(e, rules, "AND")]
        editor._model.set_approved_bulk(matched, True)
        approved = sum(1 for e in editor._model._entities if e.get("approved"))
        # 0.93 + 0.85 + 0.99 ≥ 0.80  → 3 matches in our fixture
        assert approved == 3

    def test_auto_approve_multi_condition_and(self, _qapp: object, _sample_records: list[dict]) -> None:
        """Rule: confidence > 0.70 AND source = person_ner → only high-conf persons."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor, evaluate_rules,
        )

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        rules = [
            {"field": "confidence", "op": ">", "value": 0.70},
            {"field": "source", "op": "=", "value": "person_ner"},
        ]
        matched = [i for i, e in enumerate(editor._model._entities)
                   if evaluate_rules(e, rules, "AND")]
        assert len(matched) == 1  # שלמה הלוי (0.93), not יצחק (0.62)
        ent = editor._model._entities[matched[0]]
        assert ent["source"] == "person_ner"
        assert ent["confidence"] > 0.70

    def test_auto_approve_role_not_in_list(self, _qapp: object, _sample_records: list[dict]) -> None:
        """Rule: role NOT IN [OWNER, AUTHOR] excludes the named roles."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor, evaluate_rule,
        )

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        rule = {"field": "role", "op": "not in", "value": ["OWNER", "AUTHOR"]}
        hits = [e for e in editor._model._entities if evaluate_rule(e, rule)]
        roles = {e.get("role", "") for e in hits}
        assert "AUTHOR" not in roles
        assert "OWNER" not in roles
        assert "TRANSCRIBER" in roles  # יצחק בן אהרן

    def test_auto_approve_or_combinator(self, _qapp: object, _sample_records: list[dict]) -> None:
        """OR combinator admits any-matching entity."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor, evaluate_rules,
        )

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        rules = [
            {"field": "type", "op": "=", "value": "FOLIO"},
            {"field": "confidence", "op": ">=", "value": 0.90},
        ]
        or_matches = [
            i for i, e in enumerate(editor._model._entities)
            if evaluate_rules(e, rules, "OR")
        ]
        # FOLIO row + any 0.90+ row (שלמה הלוי 0.93, דף 47 0.99)  → at least 2
        assert len(or_matches) >= 2

    def test_view_source_lookup_returns_full_text_and_offsets(
        self, _qapp: object, _sample_records: list[dict],
    ) -> None:
        """source_text_for() resolves to the record's text + entity offsets."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor,
        )

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        full, et, s, e = editor._model.source_text_for(0)  # שלמה הלוי row
        assert full, "Expected non-empty source text for first row"
        assert et == "שלמה הלוי"
        assert 0 <= s < e <= len(full)
        assert full[s:e] == et, (
            f"View-source offsets don't extract entity text: "
            f"full[{s}:{e}]={full[s:e]!r} vs entity={et!r}"
        )

    def test_view_source_substring_fallback_when_offsets_invalid(
        self, _qapp: object, _sample_records: list[dict],
    ) -> None:
        """If start/end are stale after an edit, fall back to substring search."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor,
        )

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        # Corrupt the offsets on row 0 but keep the text intact
        editor._model._entities[0]["start"] = 9999
        editor._model._entities[0]["end"] = 99999
        full, et, s, e = editor._model.source_text_for(0)
        assert full[s:e] == et, "Substring fallback should recover a valid slice"

    def test_type_and_role_delegates_return_qcombobox(
        self, _qapp: object, _sample_records: list[dict],
    ) -> None:
        """Type and Role cells must edit via QComboBox dropdowns, not QLineEdit."""
        from PyQt6.QtWidgets import QComboBox  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            COL_ROLE, COL_TYPE, EntityRoleDelegate, EntityTypeDelegate, ExtractionEditor,
        )

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        type_del = editor._table.itemDelegateForColumn(COL_TYPE)
        role_del = editor._table.itemDelegateForColumn(COL_ROLE)
        assert isinstance(type_del, EntityTypeDelegate)
        assert isinstance(role_del, EntityRoleDelegate)

        idx = editor._proxy.index(0, COL_TYPE)
        editor_widget = type_del.createEditor(editor._table, None, idx)
        assert isinstance(editor_widget, QComboBox), (
            "Type column editor must be a QComboBox (dropdown), not free text"
        )
        idx = editor._proxy.index(0, COL_ROLE)
        editor_widget = role_del.createEditor(editor._table, None, idx)
        assert isinstance(editor_widget, QComboBox)

    def test_dark_mode_type_colors_are_readable(self, _qapp: object) -> None:
        """Dark mode must NOT produce white text on a bright background."""
        from mhm_pipeline.gui.widgets.extraction_editor import _type_colors  # noqa: PLC0415
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        # Force dark mode via the theme cache
        theme._dark = True  # type: ignore[attr-defined]
        try:
            colors = _type_colors()
        finally:
            theme._dark = None  # type: ignore[attr-defined]

        # For every type the background should be DARK and the foreground LIGHT
        def _luminance(hex_color: str) -> float:
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255

        for entity_type, (bg, fg) in colors.items():
            bg_lum = _luminance(bg)
            fg_lum = _luminance(fg)
            assert bg_lum < 0.4, (
                f"[{entity_type}] Dark-mode background '{bg}' has luminance "
                f"{bg_lum:.2f} — too bright, would cause white-on-bright issue"
            )
            assert fg_lum > 0.6, (
                f"[{entity_type}] Dark-mode foreground '{fg}' has luminance "
                f"{fg_lum:.2f} — too dark against the dark background"
            )

    def test_save_preserves_approved_flag(
        self, _qapp: object, _sample_records: list[dict], tmp_path: object,
        monkeypatch: object,
    ) -> None:
        """to_records() must round-trip the 'approved' flag."""
        from pathlib import Path as _Path  # noqa: PLC0415

        from PyQt6.QtWidgets import QMessageBox  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.extraction_editor import ExtractionEditor  # noqa: PLC0415

        # _on_save() pops two confirmation modals — auto-confirm them.
        monkeypatch.setattr(  # type: ignore[attr-defined]
            QMessageBox, "question",
            staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes),
        )
        monkeypatch.setattr(  # type: ignore[attr-defined]
            QMessageBox, "information", staticmethod(lambda *a, **kw: None),
        )
        out_path = _Path(tmp_path) / "edited.json"  # type: ignore[arg-type]
        editor = ExtractionEditor()
        editor.load_records(_sample_records, out_path)
        # Approve row 0 manually
        editor._model._entities[0]["approved"] = True
        editor._on_save()
        assert out_path.exists()
        saved = json.loads(out_path.read_text())
        # Find the person_ner entity in the saved file and check approval
        found = False
        for rec in saved:
            for ent in rec.get("entities", []):
                if ent.get("person") == "שלמה הלוי":
                    assert ent.get("approved") is True
                    found = True
        assert found, "Approved entity missing from saved JSON"

    def test_edit_triggers_allow_single_selected_click(
        self, _qapp: object, _sample_records: list[dict],
    ) -> None:
        """Dropdown cells should open on SelectedClicked (not require double-click)."""
        from PyQt6.QtWidgets import QAbstractItemView  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.extraction_editor import ExtractionEditor  # noqa: PLC0415

        editor = ExtractionEditor()
        editor.load_records(_sample_records, None)
        triggers = editor._table.editTriggers()
        assert triggers & QAbstractItemView.EditTrigger.SelectedClicked, (
            "Type/Role dropdowns won't open on single-selected click"
        )
        assert triggers & QAbstractItemView.EditTrigger.DoubleClicked


# ── New-flow tests: dynamic options · save-filters · toggle switches ──────────


class TestExtractionEditorFlow:
    """E2E tests for the approve-before-flow behaviour added later:

      * Auto-approve dropdowns show only types/roles/sources actually
        present in the loaded session.
      * Save drops unapproved entities from the output file.
      * Rule-row value widget swaps between QComboBox and multi-select
        depending on the operator.
      * ToggleSwitch replaces QCheckBox in Configure Models flow.
    """

    @pytest.fixture
    def _qapp(self) -> object:
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
        return QApplication.instance() or QApplication(sys.argv)

    @pytest.fixture
    def _records(self) -> list[dict]:
        return [
            {
                "_control_number": "R1",
                "text": "ר' שלמה הלוי",
                "entities": [
                    {"person": "שלמה הלוי", "start": 3, "end": 12, "role": "AUTHOR",
                     "confidence": 0.95, "source": "person_ner"},
                    {"text": "אליהו", "start": 3, "end": 8, "type": "OWNER",
                     "confidence": 0.88, "source": "provenance_ner"},
                ],
            },
            {
                "_control_number": "R2",
                "text": "דף 12",
                "entities": [
                    {"text": "דף 12", "start": 0, "end": 5, "type": "FOLIO",
                     "confidence": 0.99, "source": "contents_ner"},
                ],
            },
        ]

    # ── Dynamic options ──────────────────────────────────────────────────

    def test_auto_approve_options_limited_to_loaded_values(
        self, _qapp: object, _records: list[dict],
    ) -> None:
        """AutoApproveDialog should only offer types/roles/sources present in data."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            AutoApproveDialog, ExtractionEditor, _RuleRow,
        )

        editor = ExtractionEditor()
        editor.load_records(_records, None)
        options = {
            "type": editor.get_all_types(),
            "role": editor.get_all_roles(),
            "source": editor.get_all_sources(),
        }
        # Only OWNER + FOLIO + (person entities default to PERSON)
        assert set(options["type"]) == {"PERSON", "OWNER", "FOLIO"}
        assert set(options["role"]) == {"AUTHOR"}
        assert set(options["source"]) == {"person_ner", "provenance_ner", "contents_ner"}

        dlg = AutoApproveDialog(options_for=options)
        # Grab the auto-created rule row and ask it to switch to "type = …"
        row = dlg._rule_widgets[0]
        row.field_combo.setCurrentText("type")
        row.op_combo.setCurrentText("=")
        row._on_field_or_op_changed()
        single = row.value_enum_single
        assert not single.isHidden()
        items = [single.itemText(i) for i in range(single.count())]
        assert set(items) == {"PERSON", "OWNER", "FOLIO"}
        # Must NOT include types that don't appear in the loaded data
        assert "COLLECTION" not in items
        assert "WORK_AUTHOR" not in items

    def test_rule_row_switches_widget_for_in_operator(
        self, _qapp: object, _records: list[dict],
    ) -> None:
        """`in`/`not in` should show a multi-select combo, not a free-text field."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor, _CheckableMultiCombo, AutoApproveDialog,
        )

        editor = ExtractionEditor()
        editor.load_records(_records, None)
        options = {
            "type": editor.get_all_types(),
            "role": editor.get_all_roles(),
            "source": editor.get_all_sources(),
        }
        dlg = AutoApproveDialog(options_for=options)
        row = dlg._rule_widgets[0]
        row.field_combo.setCurrentText("source")
        row.op_combo.setCurrentText("not in")
        row._on_field_or_op_changed()
        multi = row.value_enum_multi
        assert isinstance(multi, _CheckableMultiCombo)
        assert not multi.isHidden(), "Multi-select must be visible for in/not in"
        # No free-text fallback when enum options are present
        assert row.value_text.isHidden()
        assert row.value_enum_single.isHidden()

    def test_rule_row_switches_to_combobox_for_equality(
        self, _qapp: object, _records: list[dict],
    ) -> None:
        """`=`/`≠` should show a single-value QComboBox."""
        from PyQt6.QtWidgets import QComboBox  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            ExtractionEditor, AutoApproveDialog,
        )

        editor = ExtractionEditor()
        editor.load_records(_records, None)
        options = {
            "type": editor.get_all_types(),
            "role": editor.get_all_roles(),
            "source": editor.get_all_sources(),
        }
        dlg = AutoApproveDialog(options_for=options)
        row = dlg._rule_widgets[0]
        row.field_combo.setCurrentText("role")
        row.op_combo.setCurrentText("=")
        row._on_field_or_op_changed()
        assert isinstance(row.value_enum_single, QComboBox)
        assert not row.value_enum_single.isHidden()
        assert row.value_enum_multi.isHidden()
        assert row.value_text.isHidden()

    # ── Save drops unapproved ────────────────────────────────────────────

    def test_save_drops_unapproved_entities(
        self, _qapp: object, _records: list[dict], tmp_path: object,
    ) -> None:
        """to_approved_records() keeps only entities with approved=True."""
        from pathlib import Path as _Path  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.extraction_editor import ExtractionEditor  # noqa: PLC0415

        out = _Path(tmp_path) / "approved.json"  # type: ignore[arg-type]
        editor = ExtractionEditor()
        editor.load_records(_records, out)
        # Approve only the first entity (שלמה הלוי, person_ner)
        editor._model._entities[0]["approved"] = True
        approved_records = editor._model.to_approved_records()
        # Count approved entities across records
        total_saved = sum(len(r.get("entities", [])) for r in approved_records)
        assert total_saved == 1, (
            f"Expected exactly 1 approved entity saved, got {total_saved}"
        )
        # The approved entity is the person one
        found_person = False
        for rec in approved_records:
            for ent in rec.get("entities", []):
                if ent.get("source") == "person_ner":
                    assert ent.get("approved") is True
                    found_person = True
        assert found_person, "Approved person entity not in output"

    def test_save_empty_when_nothing_approved(
        self, _qapp: object, _records: list[dict],
    ) -> None:
        """With zero approvals, to_approved_records writes empty entity lists."""
        from mhm_pipeline.gui.widgets.extraction_editor import ExtractionEditor  # noqa: PLC0415

        editor = ExtractionEditor()
        editor.load_records(_records, None)
        approved = editor._model.to_approved_records()
        # Same number of records (control-number skeletons preserved), empty entities
        assert all(len(r.get("entities", [])) == 0 for r in approved)

    # ── Toggle switch widget ─────────────────────────────────────────────

    def test_toggle_switch_is_qabstractbutton_with_checkable_api(self, _qapp: object) -> None:
        """ToggleSwitch must be a drop-in replacement for QCheckBox."""
        from PyQt6.QtWidgets import QAbstractButton  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.toggle_switch import ToggleSwitch  # noqa: PLC0415

        sw = ToggleSwitch()
        assert isinstance(sw, QAbstractButton)
        assert sw.isCheckable()
        assert sw.isChecked() is False
        sw.setChecked(True)
        assert sw.isChecked() is True
        # Toggle signal fires with the new state
        states: list[bool] = []
        sw.toggled.connect(states.append)
        sw.setChecked(False)
        assert states == [False]

    def test_toggle_switch_bidirectional_binding_to_checkbox(self, _qapp: object) -> None:
        """The Configure Models dialog binds switch ↔ hidden QCheckBox both ways."""
        from PyQt6.QtWidgets import QCheckBox  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.toggle_switch import ToggleSwitch  # noqa: PLC0415

        source = QCheckBox()
        source.setChecked(False)
        sw = ToggleSwitch()
        sw.setChecked(source.isChecked())
        sw.toggled.connect(lambda c: source.setChecked(c))
        source.toggled.connect(sw.setChecked)

        # Flip the switch — source should follow
        sw.setChecked(True)
        assert source.isChecked() is True
        # Flip the source — switch should follow
        source.setChecked(False)
        assert sw.isChecked() is False

    # ── Auto-open review on Load Results / NER finish ────────────────────

    def test_store_results_auto_review_calls_edit_dialog(
        self, _qapp: object, _records: list[dict],
    ) -> None:
        """With auto_review=True, the panel schedules the edit dialog."""
        from PyQt6.QtCore import QTimer  # noqa: PLC0415

        from mhm_pipeline.gui.panels.ner_panel import NerPanel  # noqa: PLC0415

        panel = NerPanel()
        calls: list[bool] = []
        panel._on_edit_entities_popup = lambda: calls.append(True)  # type: ignore[method-assign]
        panel._store_results(_records, None, auto_review=True)
        # The timer is single-shot 100ms — spin the event loop briefly
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
        loop_end = QTimer()
        loop_end.setSingleShot(True)
        waited = False
        def _stop() -> None:
            nonlocal waited
            waited = True
        loop_end.timeout.connect(_stop)
        loop_end.start(250)
        while not waited:
            QApplication.processEvents()
        assert calls, "auto_review=True should open the edit dialog via QTimer"


# ── AuthorityEditor tests (Stage 2 approve-flow) ──────────────────────────


class TestAuthorityEditor:
    """E2E tests for AuthorityEditor — Stage 2 equivalent of the NER editor.

    Covers:
      * flatten_authority_records handles all 3 shapes (marc_authority_matches,
        entity-level IDs, kima_places).
      * unflatten_rows_into_records drops unapproved rows (safe downstream).
      * Approval CheckStateRole round-trips.
      * Rule evaluation including confidence_band + has_external_id.
      * Per-row edit writes back to the model.
    """

    @pytest.fixture
    def _qapp(self) -> object:
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
        return QApplication.instance() or QApplication(sys.argv)

    @pytest.fixture
    def _records(self) -> list[dict]:
        return [
            {
                "_control_number": "R1",
                "marc_authority_matches": [
                    {
                        "name": "Maimonides",
                        "role": "author",
                        "field": "marc_100",
                        "mazal_id": "M001",
                        "viaf_uri": "",
                        "confidence": 0.95,
                        "preferred_name_lat": "Moses ben Maimon",
                        "dates": "1138-1204",
                        "gnd_id": "118577166",
                    },
                ],
                "entities": [
                    {
                        "person": "שלמה הלוי", "role": "AUTHOR",
                        "confidence": 0.88, "source": "person_ner",
                        "viaf_uri": "https://viaf.org/viaf/12345",
                    },
                    # This entity should NOT become a row (no auth IDs)
                    {"person": "anon", "source": "person_ner"},
                ],
                "kima_places": {
                    "Jerusalem": "http://www.wikidata.org/entity/Q1754",
                },
            },
            {
                "_control_number": "R2",
                "marc_authority_matches": [
                    {
                        "name": "Rashi",
                        "field": "marc_700",
                        "mazal_id": "",
                        "viaf_uri": "https://viaf.org/viaf/99999",
                        "confidence": 0.70,
                    },
                ],
            },
        ]

    def test_flatten_produces_one_row_per_match(self, _qapp, _records) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: PLC0415
            flatten_authority_records,
        )
        rows = flatten_authority_records(_records)
        # R1: 1 marc match + 1 entity (with viaf) + 1 KIMA place = 3
        # R2: 1 marc match = 1
        assert len(rows) == 4, f"Expected 4 rows, got {len(rows)}: {rows}"
        kinds = {r["_origin_kind"] for r in rows}
        assert kinds == {"marc", "entity", "kima"}

    def test_flatten_skips_entities_without_authority_id(self, _qapp, _records) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: PLC0415
            flatten_authority_records,
        )
        rows = flatten_authority_records(_records)
        anon_rows = [r for r in rows if r["entity_text"] == "anon"]
        assert not anon_rows, "Anonymous entity without VIAF/Mazal should not appear"

    def test_unflatten_drops_unapproved(self, _qapp, _records) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: PLC0415
            flatten_authority_records, unflatten_rows_into_records,
        )
        rows = flatten_authority_records(_records)
        # Approve only the marc match in R1
        for r in rows:
            if r["_origin_kind"] == "marc" and r["_control_number"] == "R1":
                r["approved"] = True
        out = unflatten_rows_into_records(rows, _records)
        r1 = next(r for r in out if r["_control_number"] == "R1")
        r2 = next(r for r in out if r["_control_number"] == "R2")
        assert len(r1["marc_authority_matches"]) == 1
        assert r1["marc_authority_matches"][0]["mazal_id"] == "M001"
        assert len(r2["marc_authority_matches"]) == 0, "R2 match was unapproved → dropped"
        assert r1["kima_places"] == {}, "Kima place was unapproved → dropped"

    def test_approved_checkbox_roundtrip(self, _qapp, _records) -> None:
        from PyQt6.QtCore import Qt  # noqa: PLC0415
        from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: PLC0415
            AuthorityEditor, COL_APPROVED,
        )
        editor = AuthorityEditor()
        editor.load_records(_records, None)
        idx = editor._model.index(0, COL_APPROVED)
        assert editor._model.flags(idx) & Qt.ItemFlag.ItemIsUserCheckable
        editor._model.setData(idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        assert editor._model._rows[0]["approved"] is True

    def test_auto_approve_confidence_rule(self, _qapp, _records) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: PLC0415
            AuthorityEditor, evaluate_auth_rules,
        )
        editor = AuthorityEditor()
        editor.load_records(_records, None)
        rules = [{"field": "confidence", "op": ">=", "value": 0.85}]
        matched = [i for i, r in enumerate(editor._model._rows)
                   if evaluate_auth_rules(r, rules, "AND")]
        # Maimonides (0.95), שלמה הלוי (0.88), Jerusalem (1.0) — Rashi (0.70) out
        assert len(matched) >= 2

    def test_auto_approve_confidence_band_rule(self, _qapp, _records) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: PLC0415
            AuthorityEditor, evaluate_auth_rules,
        )
        editor = AuthorityEditor()
        editor.load_records(_records, None)
        rules = [{"field": "confidence_band", "op": "=", "value": "high"}]
        matched = [i for i, r in enumerate(editor._model._rows)
                   if evaluate_auth_rules(r, rules, "AND")]
        # "high" band is conf ≥ 0.90 — Maimonides (0.95) + Jerusalem (1.0)
        assert len(matched) == 2

    def test_auto_approve_has_external_id(self, _qapp, _records) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: PLC0415
            AuthorityEditor, evaluate_auth_rules,
        )
        editor = AuthorityEditor()
        editor.load_records(_records, None)
        rules = [{"field": "has_external_id", "op": "=", "value": "true"}]
        matched = [i for i, r in enumerate(editor._model._rows)
                   if evaluate_auth_rules(r, rules, "AND")]
        # Every one of our 4 rows has a matched_id
        assert len(matched) == 4

    def test_auto_approve_source_not_in(self, _qapp, _records) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: PLC0415
            AuthorityEditor, evaluate_auth_rules,
        )
        editor = AuthorityEditor()
        editor.load_records(_records, None)
        rules = [{"field": "source", "op": "not in", "value": ["kima"]}]
        matched = [i for i, r in enumerate(editor._model._rows)
                   if evaluate_auth_rules(r, rules, "AND")]
        # 4 rows - 1 KIMA = 3
        assert len(matched) == 3

    def test_save_drops_unapproved_end_to_end(
        self, _qapp, _records, tmp_path: object,
    ) -> None:
        from pathlib import Path as _Path  # noqa: PLC0415
        from mhm_pipeline.gui.widgets.authority_editor import AuthorityEditor  # noqa: PLC0415

        out = _Path(tmp_path) / "authority_approved.json"  # type: ignore[arg-type]
        editor = AuthorityEditor()
        editor.load_records(_records, out)
        # Approve the first row only (Maimonides marc match)
        editor._model._rows[0]["approved"] = True
        approved_records = editor._model.to_approved_records()
        total_matches = sum(
            len(r.get("marc_authority_matches", [])) for r in approved_records
        )
        assert total_matches == 1
        total_places = sum(len(r.get("kima_places", {})) for r in approved_records)
        assert total_places == 0, "Unapproved KIMA place was not dropped"


# ── Wikidata Studio / QPEntityBrowser tests ─────────────────────────────


class TestWikidataStudio:
    """E2E tests for the merged Wikidata Studio panel + QPEntityBrowser.

    Uses a lightweight fake WikidataItem so tests don't need the full
    item_builder machinery.
    """

    @pytest.fixture
    def _qapp(self) -> object:
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
        return QApplication.instance() or QApplication(sys.argv)

    def _fake_items(self) -> list[object]:
        from dataclasses import dataclass, field  # noqa: PLC0415

        @dataclass
        class Stmt:
            property_id: str
            value: object
            value_type: str = "string"
            qualifiers: list = field(default_factory=list)
            references: list = field(default_factory=list)

        @dataclass
        class Item:
            labels: dict
            descriptions: dict = field(default_factory=dict)
            aliases: dict = field(default_factory=dict)
            statements: list = field(default_factory=list)
            existing_qid: str = ""
            entity_type: str = "person"
            local_id: str = ""

        return [
            Item(labels={"en": "Maimonides"},
                 descriptions={"en": "Jewish philosopher"},
                 statements=[Stmt("P214", "100216431", "external-id")],
                 entity_type="person",
                 local_id="PERSON:maimonides"),
            Item(labels={"en": "Rashi"},
                 statements=[Stmt("P214", "99999", "external-id")],
                 entity_type="person",
                 local_id="PERSON:rashi"),
            Item(labels={"he": "גינת אגוז", "en": "Tree of Walnut"},
                 statements=[Stmt("P1476", "Tree of Walnut", "monolingualtext")],
                 entity_type="work",
                 local_id="WORK:ginat-egoz"),
            Item(labels={"he": "כתב יד"},
                 statements=[Stmt("P217", "Vatican Ms. 101", "string"),
                             Stmt("P195", "Q999999", "item")],
                 entity_type="manuscript",
                 local_id="MS:990001234"),
        ]

    def test_qp_browser_loads_items(self, _qapp) -> None:
        from mhm_pipeline.gui.widgets.qp_entity_browser import QPEntityBrowser  # noqa: PLC0415

        b = QPEntityBrowser()
        b.load_items(self._fake_items())
        assert b._model.rowCount() == 4
        assert set(b.get_all_types()) == {"person", "work", "manuscript"}

    def test_status_blocks_approval_for_existing_other(self, _qapp) -> None:
        from PyQt6.QtCore import Qt  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.qp_entity_browser import (  # noqa: PLC0415
            COL_APPROVED, QPEntityBrowser, _STATUS_OTHER, _STATUS_NEW,
        )

        b = QPEntityBrowser()
        b.load_items(self._fake_items())
        # Mark row 0 as existing-other → approval must be refused
        b.update_status(b._model._rows[0]["local_id"], _STATUS_OTHER, qid="Q42",
                        reason="community item")
        idx = b._model.index(0, COL_APPROVED)
        ok = b._model.setData(idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        assert ok is False, "Must refuse approval of existing-other items"
        assert b._model._rows[0]["approved"] is False

    def test_approved_items_excludes_unapproved_and_other(self, _qapp) -> None:
        from mhm_pipeline.gui.widgets.qp_entity_browser import (  # noqa: PLC0415
            QPEntityBrowser, _STATUS_NEW, _STATUS_OTHER,
        )

        b = QPEntityBrowser()
        b.load_items(self._fake_items())
        b.update_status(b._model._rows[0]["local_id"], _STATUS_NEW)
        b.update_status(b._model._rows[1]["local_id"], _STATUS_OTHER, qid="Q1")
        # Approve first two; second must remain rejected by safety guard
        b._model._rows[0]["approved"] = True
        # Bypass the safety guard in the model for test determinism:
        # set_approved_bulk does honour the guard, but direct dict write
        # models a malicious caller — we verify approved_items() still
        # excludes "existing-other" via the entity_type check.
        b._model._rows[1]["approved"] = True
        approved = b.approved_items()
        assert len(approved) == 1
        assert approved[0].local_id == "PERSON:maimonides"

    def test_studio_panel_instantiates(self, _qapp) -> None:
        from mhm_pipeline.gui.panels.wikidata_studio_panel import (  # noqa: PLC0415
            WikidataStudioPanel,
        )

        panel = WikidataStudioPanel()
        assert panel.stage_progress is not None
        assert panel.log_viewer is not None
        # The upload_requested signal should be defined
        assert hasattr(panel, "upload_requested")


# ── DynamicProgressBar + connect_progress_signals helper ─────────────────────


try:
    import pymarc as _pymarc  # noqa: F401, PLC0415

    _PYMARC_AVAILABLE = True
except ImportError:
    _PYMARC_AVAILABLE = False


class TestDynamicProgressBar:
    """Integration tests for DynamicProgressBar + connect_progress_signals.

    Covers:
      * Full lifecycle wiring via the DRY helper using a QObject stub
      * Real MarcParseWorker emits the new substep signal during run
      * Indeterminate-mode 100ms debounce is cancelled by a quick recovery
    """

    @pytest.fixture
    def _qapp(self) -> object:
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

        return QApplication.instance() or QApplication(sys.argv)

    # ── Test 1 ────────────────────────────────────────────────────────

    def test_widget_handles_full_lifecycle_with_synthetic_signals(
        self, _qapp: object
    ) -> None:
        from PyQt6.QtCore import QObject, pyqtSignal  # noqa: PLC0415

        from mhm_pipeline.gui.widgets.dynamic_progress_bar import (  # noqa: PLC0415
            DynamicProgressBar,
            connect_progress_signals,
        )

        class _StubWorker(QObject):
            progress = pyqtSignal(int)
            substep = pyqtSignal(str)
            finished = pyqtSignal()
            error = pyqtSignal(str)

        bar = DynamicProgressBar()
        worker = _StubWorker()
        connect_progress_signals(bar, worker)

        # 1. substep emitted before any progress
        worker.substep.emit("Reading file…")
        assert "Reading" in bar._substep.text() or bar._substep.text() != ""

        # 2. set total via first progress, then advance
        bar.set_total(100)
        worker.progress.emit(0)
        worker.progress.emit(50)
        assert bar._bar.value() > 0
        history_after_progress = len(bar._history)
        assert history_after_progress > 0

        # 3. substep change must NOT clear ETA history
        worker.substep.emit("Parsing records…")
        assert "Parsing" in bar._substep.text() or bar._substep.text() != ""
        assert len(bar._history) == history_after_progress

        # 4. finish via progress=100
        worker.progress.emit(100)

        # 5. finished() with no args
        worker.finished.emit()
        assert bar._substep.text() == "Done"
        assert bar._success is True
        assert bar._finished is True

    # ── Test 2 ────────────────────────────────────────────────────────

    @pytest.mark.skipif(not _PYMARC_AVAILABLE, reason="pymarc not installed")
    @pytest.mark.skipif(not TSV_FILE.exists(), reason="TSV fixture missing")
    def test_real_marc_parse_worker_emits_substep_signal(
        self, qtbot: object, tmp_path: Path
    ) -> None:
        from mhm_pipeline.controller.workers import MarcParseWorker  # noqa: PLC0415

        worker = MarcParseWorker(TSV_FILE, tmp_path, "cpu", start=0, end=2)

        substep_messages: list[str] = []
        progress_values: list[int] = []
        worker.substep.connect(substep_messages.append)  # type: ignore[attr-defined]
        worker.progress.connect(progress_values.append)  # type: ignore[attr-defined]

        # Run inline (no QThread) — keeps the test single-threaded and
        # avoids the conftest QThread drain.
        worker.run()

        assert substep_messages, "substep signal never fired"
        assert all(isinstance(m, str) and m for m in substep_messages), (
            f"empty substep string emitted: {substep_messages!r}"
        )
        assert progress_values, "progress signal never fired"
        assert progress_values[-1] == 100

    # ── Test 3 ────────────────────────────────────────────────────────

    def test_indeterminate_mode_debounces_flicker(
        self, qtbot: object, _qapp: object
    ) -> None:
        from mhm_pipeline.gui.widgets.dynamic_progress_bar import (  # noqa: PLC0415
            DynamicProgressBar,
        )

        bar = DynamicProgressBar()
        bar.set_total(100)
        bar.set_progress(50)

        # Brief flip to 0 — should schedule indeterminate but NOT activate it.
        bar.set_total(0)
        # Recover before the 100ms debounce timer fires.
        bar.set_total(50)

        # The debounce timer must have been cancelled — bar still determinate.
        assert bar._bar.maximum() > 0, (
            f"determinate mode lost despite debounce; max={bar._bar.maximum()}"
        )

        # Now flip to 0 and let the debounce fire.
        bar.set_total(0)
        qtbot.wait(150)  # type: ignore[attr-defined]
        assert bar._bar.maximum() == 0, (
            f"indeterminate mode never activated; max={bar._bar.maximum()}"
        )
