Run the end-to-end integration tests for the MHM pipeline.

## Test Coverage

The e2e test suite covers all six pipeline stages plus controller chaining:

| Test Class | Stage | Coverage |
|------------|-------|----------|
| `TestVenvImports` | Pre-flight | Verifies pymarc, rdflib, pyshacl, PyQt6 installed in venv |
| `TestMarcParseWorker` | Stage 0 | MARC/TSV parsing, 897 records from 17th_century_samples.tsv |
| `TestNerWorker` | Stage 1 | Mock NER inference (no model download) |
| `TestAuthorityWorker` | Stage 2 | Authority matching on NER + MARC names (100, 110, 111, 700, 710, 711) |
| `TestMazalIndexWorker` | Stage 2 | Mazal index building from XML (skipped if no XML) |
| `TestKimaIndexWorker` | Stage 2 | KIMA index building from TSV (skipped if no TSV) |
| `TestRdfBuildWorker` | Stage 3 | RDF graph construction |
| `TestShaclValidateWorker` | Stage 4 | SHACL validation |
| `TestWikidataUploadWorker` | Stage 5 | Wikidata upload stub |
| `TestPipelineControllerChain` | Controller | Stage chaining 0 → 3 → 4 |

### Critical: TestVenvImports

**Purpose:** Catch "tests pass but app crashes" issues before they happen.

The app uses `.venv/bin/python` which only sees packages installed in the venv. Tests use `PYTHONPATH=src:.` which finds packages via the filesystem. If you forget `uv sync`, tests pass but the app crashes.

These tests fail fast with a clear message: `"pymarc not installed in venv: ... Run: uv sync"`

**Also catches:** QThread segfaults (e.g., `dictiter_iternextitem` crash in MarcParseWorker). The `test_stage_0_worker_runs_in_qthread_without_crash` runs the worker in an actual QThread to catch threading issues that don't appear when calling `run()` directly.

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

- 40+ tests should pass
- `test_builds_index_from_xml` skipped unless NLI XML files present
- `test_builds_index_from_tsvs` skipped unless KIMA TSV files present

## Troubleshooting

If tests fail:
1. Check Python syntax: `python3 -m py_compile <modified_file>`
2. Verify imports resolve: `PYTHONPATH=src:. python3 -c "from <module> import <class>"`
3. Check for missing dependencies in `.venv`
