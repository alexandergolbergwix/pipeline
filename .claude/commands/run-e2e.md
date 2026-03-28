Run only the end-to-end integration tests for the MHM pipeline using the 17th-century TSV fixture.

Execute:
```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python -m pytest tests/integration/test_pipeline_e2e.py -v --tb=short 2>&1 | tail -60
```

Report pass/fail counts and flag any regressions. The expected baseline is all 36 tests passing (one `TestMazalIndexWorker::test_builds_index_from_xml` is skipped unless NLI XML files are present).
