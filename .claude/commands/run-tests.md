Run tests for the MHM pipeline.

## Stage → Test Class Mapping

| Stage | Core files | Test class |
|-------|-----------|------------|
| Pre-flight | Dependencies | `TestVenvImports` — verifies pymarc, rdflib, pyshacl, PyQt6 in venv |
| Pre-flight | Threading | `test_stage_0_worker_runs_in_qthread_without_crash` — catches QThread segfaults |
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
2. **QThread test** — Runs `MarcParseWorker` in actual QThread to catch segfaults (like `dictiter_iternextitem` crash)

The crash reports showed `SIGABRT` in `dictiter_iternextitem` from `MarcParseWorker` thread. The new `test_stage_0_worker_runs_in_qthread_without_crash` test runs the worker in a real QThread to catch this class of threading bugs.

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

## After Any Code Change

Always run the affected stage's tests before marking complete:

```bash
# Example: Modified authority matching
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -v \
  -k "TestAuthorityWorker" 2>&1

# Example: Modified field_handlers (affects all stages)
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -60
```

## Expected Baseline

- Full suite: 40+ tests pass
- Unit tests: Fast (< 1 min)
- Integration tests: Slower (2-5 min) due to file I/O
