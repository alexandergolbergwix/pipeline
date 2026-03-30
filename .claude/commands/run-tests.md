Run tests for the MHM pipeline.

## Stage → Test Class Mapping

| Stage | Core files | Test class |
|-------|-----------|------------|
| Pre-flight | Dependencies | `TestVenvImports` — verifies pymarc, rdflib, pyshacl, PyQt6 in venv |
| Pre-flight | Threading | `test_stage_0_worker_runs_in_qthread_without_crash` — catches QThread segfaults |
| GUI Widgets | `gui/widgets/`, `gui/panels/` | `TestGuiWidgetContracts` — verifies all panel widgets expose `set_progress` |
| Full GUI Chain | `main_window.py`, `workers.py`, `pipeline_controller.py` | `TestFullGuiProgressChain` — runs worker in QThread with full MainWindow |
| 0 — MARC Parse | `converter/parser/`, `converter/transformer/field_handlers.py`, `workers.py MarcParseWorker` | `TestMarcParseWorker` |
| 1 — NER | `ner/`, `workers.py NerWorker`, `ner_panel.py` | `TestNerWorker` |
| 2 — Authority | `converter/authority/`, `workers.py AuthorityWorker`, `authority_panel.py` | `TestAuthorityWorker`, `TestMazalIndexWorker`, `TestKimaIndexWorker` |
| 3 — RDF Build | `converter/transformer/mapper.py`, `workers.py RdfBuildWorker`, `rdf_panel.py` | `TestRdfBuildWorker` |
| 4 — SHACL | `converter/validation/`, `workers.py ShaclValidateWorker`, `validate_panel.py` | `TestShaclValidateWorker` |
| 5 — Wikidata | `workers.py WikidataUploadWorker`, `wikidata_panel.py` | `TestWikidataUploadWorker` |
| Controller | `pipeline_controller.py`, `settings_manager.py` | `TestPipelineControllerChain` |

### Critical: TestVenvImports

**Purpose:** Catch "tests pass but app crashes" issues before they happen.

1. **Dependency tests** — Verify packages are installed in `.venv`, not just found via `PYTHONPATH`
2. **QThread tests** — Run workers in actual QThreads to catch segfaults:
   - `test_stage_0_worker_runs_in_qthread_without_crash` — Catches `dictiter_iternextitem` crash
   - `test_ner_worker_runs_in_qthread_without_crash` — Catches PyTorch/threading issues

### Fixed: JSON Serialization Crash

A `SIGABRT` crash was occurring in `escape_unicode` → `list_extend` during `json.dumps()` in worker threads. The fix adds `copy.deepcopy()` before JSON serialization in all workers to prevent thread-safety issues.

### Fixed: StageProgressWidget Missing set_progress Crash

A `SIGABRT` crash occurred when `MainWindow._on_stage_progress` called `panel.stage_progress.set_progress(pct)` but `StageProgressWidget` had no `set_progress` method. The unhandled `AttributeError` inside a Qt slot caused `pyqt6_err_print()` → `QMessageLogger::fatal()` → `abort()`. This killed the app on every Stage 1 run. Fixed by adding `set_progress()` to `StageProgressWidget`. Guarded by `TestGuiWidgetContracts` and `TestFullGuiProgressChain`.

### Fixed: VIAF API Returns HTML Instead of JSON

The VIAF SRU API changed — it no longer returns JSON via the `recordSchema` query param. It now requires an `Accept: application/json` HTTP header. The JSON structure also changed: `records` → `records.record`, `recordData.viafID` → `recordData.ns2:VIAFCluster.ns2:viafID`. Fixed in `converter/authority/viaf_matcher.py`. Rate limit set to 2 req/s (0.5s between requests).

### Fixed: KIMA Index Never Built

The KIMA matcher silently returned `None` for every lookup because `data/kima/kima_index.db` was never compiled from the TSV source files. The matcher's `is_available` check returned `False`, but only logged at DEBUG level. Fixed by building the index. If the DB is missing after a fresh clone, rebuild it via the "Rebuild KIMA Index" button in the Authority panel or run:
```bash
PYTHONPATH=src:. .venv/bin/python -c "
from converter.authority.kima_index import build_kima_index
build_kima_index('data/kima', 'data/kima/kima_index.db', verbose=True)
"
```

### Fixed: Authority Stage Data Flow

AuthorityWorker now takes MARC extract (stage 0) as primary input and NER results (stage 1) as optional enrichment. Previously NER was primary and MARC was optional, which meant MARC name fields (100/110/111/700/710/711) were only matched when the user manually selected the MARC file. Now MARC names are always matched, and NER entities are merged in by `_control_number` when available.

## Quick Commands

Run all tests:
```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -60
```

Run specific stage tests:
```bash
# Stage 2 (Authority) only
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q --tb=short \
  -k "TestAuthorityWorker or TestMazalIndexWorker or TestKimaIndexWorker" 2>&1 | tail -40

# Stage 0-1 (Parse + NER)
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q --tb=short \
  -k "TestMarcParseWorker or TestNerWorker" 2>&1 | tail -40
```

## Pre-Test Checklist

Before running tests after code changes:

1. **Syntax check** (catches 80% of errors):
   ```bash
   python3 -m py_compile <modified_file>
   ```

2. **Import check** (catches missing imports):
   ```bash
   PYTHONPATH=src:. python3 -c "from mhm_pipeline.controller.workers import AuthorityWorker"
   ```

3. **For GUI changes** - reinstall app first:
   ```bash
   # See /reinstall-app skill
   ```

## Common Failures and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError` | Missing import or wrong path | Add `from __future__ import annotations` and check imports |
| `AttributeError: 'NoneType'` | Widget not initialized | Check widget creation in `__init__` |
| `QLabel` import missing | Import error in panel | Add `QLabel` to Qt imports |
| Test timeout | Worker taking too long | Check for infinite loops or missing mock |
| Import loop | Circular import | Move import inside function (lazy import) |
| VIAF returns 0 matches | API returns HTML, not JSON | Ensure `Accept: application/json` header in `viaf_matcher.py` |
| KIMA returns 0 matches | `kima_index.db` missing | Rebuild: "Rebuild KIMA Index" button or `build_kima_index()` |
| Authority ignores MARC names | `input_path` is NER, not MARC | `input_path` must be stage 0 output; NER goes to `ner_path` |

## After Any Code Change

Always run the affected stage's tests before marking complete:

```bash
# Example: Modified authority matching
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -v \
  -k "TestAuthorityWorker" 2>&1

# Example: Modified field_handlers (affects all stages)
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -60
```

## After Any Widget or Panel Change

Always run the GUI widget contract tests to catch missing-method SIGABRT crashes:

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -v \
  -k "TestGuiWidgetContracts or TestFullGuiProgressChain" 2>&1
```

## Expected Baseline

- Full suite: 50+ tests pass
- Unit tests: Fast (< 1 min)
- Integration tests: Slower (2-5 min) due to file I/O
