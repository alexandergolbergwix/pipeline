Run the end-to-end integration tests for the MHM pipeline.

## Test Coverage

The e2e test suite covers all six pipeline stages plus controller chaining:

| Test Class | Stage | Coverage |
|------------|-------|----------|
| `TestVenvImports` | Pre-flight | Verifies pymarc, rdflib, pyshacl, PyQt6 installed in venv |
| `TestGuiWidgetContracts` | GUI Widgets | Verifies all panels expose `set_progress` — catches SIGABRT crashes |
| `TestMarcParseWorker` | Stage 0 | MARC/TSV parsing + QThread crash test (`test_stage_0_worker_runs_in_qthread`) |
| `TestNerWorker` | Stage 1 | Mock NER inference + QThread crash test (`test_ner_worker_runs_in_qthread`) |
| `TestAuthorityWorker` | Stage 2 | Authority matching: MARC extract as primary input, NER entities merged, names (100/110/111/700/710/711) |
| `TestMazalIndexWorker` | Stage 2 | Mazal index building from XML (skipped if no XML) |
| `TestKimaIndexWorker` | Stage 2 | KIMA index building from TSV (skipped if no TSV) |
| `TestRdfBuildWorker` | Stage 3 | RDF graph construction |
| `TestShaclValidateWorker` | Stage 4 | SHACL validation |
| `TestWikidataUploadWorker` | Stage 5 | Wikidata upload stub |
| `TestPipelineControllerChain` | Controller | Stage chaining 0 → 3 → 4 |
| `TestFullGuiProgressChain` | Full GUI | Worker in QThread with full MainWindow — catches signal path SIGABRT |

### Critical: TestVenvImports

**Purpose:** Catch "tests pass but app crashes" issues before they happen.

The app uses `.venv/bin/python` which only sees packages installed in the venv. Tests use `PYTHONPATH=src:.` which finds packages via the filesystem. If you forget `uv sync`, tests pass but the app crashes.

These tests fail fast with a clear message: `"pymarc not installed in venv: ... Run: uv sync"`

**Also catches:** QThread segfaults (e.g., `dictiter_iternextitem` crash in MarcParseWorker). The `test_stage_0_worker_runs_in_qthread_without_crash` runs the worker in an actual QThread to catch threading issues that don't appear when calling `run()` directly.

### Fixed: JSON Serialization Crash

A `SIGABRT` crash was occurring in `escape_unicode` → `list_extend` during `json.dumps()` in worker threads. The fix adds `copy.deepcopy()` before JSON serialization in:
- `MarcParseWorker` (Stage 0)
- `NerWorker` (Stage 1)
- `AuthorityWorker` (Stage 2)

This prevents thread-safety issues when data is being modified while JSON is iterating over it.

### Fixed: StageProgressWidget Missing set_progress Crash

`MainWindow._on_stage_progress` called `panel.stage_progress.set_progress(pct)` but `StageProgressWidget` had no `set_progress` method. The unhandled `AttributeError` inside a Qt slot triggered `pyqt6_err_print()` → `QMessageLogger::fatal()` → `abort()` → SIGABRT. Now caught by `TestGuiWidgetContracts` and `TestFullGuiProgressChain`.

### Fixed: VIAF API Format Change

VIAF no longer returns JSON via `recordSchema` query param. Requires `Accept: application/json` header. JSON path also changed: `records` → `records.record`, `viafID` → `ns2:VIAFCluster.ns2:viafID`. Rate limit: 2 req/s (0.5s between requests).

### Fixed: KIMA Index Missing + Authority Data Flow

- KIMA index DB (`data/kima/kima_index.db`) must be built from TSVs before KIMA matching works. Matcher silently returns `None` when DB is missing.
- AuthorityWorker now takes MARC extract (stage 0) as `input_path` and NER results (stage 1) as optional `ner_path`. NER entities are merged into records by `_control_number` before matching.

## Key Tests

- `test_tsv_first_500_records_parse_with_authority_fields` - Verifies 500 records parse with all MARC name fields
- `test_marc_name_fields_100_110_111_700_710_711_matched` - Verifies all 6 MARC name fields are authority-matched
- `test_tsv_extracts_expected_record_count` - Verifies full 897-record TSV parsing

## Procedure

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python -m pytest tests/integration/test_pipeline_e2e.py -v --tb=short 2>&1 | tail -80
```

## Expected Results

- **55+ tests should pass** (6 VenvImports + 4 GuiWidgetContracts + 9 MarcParseWorker + 4 NerWorker + 5 AuthorityWorker + 1 FullGuiProgressChain + others)
- `test_builds_index_from_xml` skipped unless NLI XML files present
- `test_builds_index_from_tsvs` skipped unless KIMA TSV files present

## Troubleshooting

If tests fail:
1. Check Python syntax: `python3 -m py_compile <modified_file>`
2. Verify imports resolve: `PYTHONPATH=src:. python3 -c "from <module> import <class>"`
3. Check for missing dependencies in `.venv`
4. If KIMA tests fail: ensure `data/kima/kima_index.db` exists (rebuild from TSVs if needed)
5. If Authority tests fail with `unexpected keyword argument 'marc_path'`: update to use `ner_path=` instead
