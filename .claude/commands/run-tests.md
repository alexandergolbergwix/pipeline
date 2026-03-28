Run tests for the MHM pipeline. Automatically scope to only the stages whose files were modified.

## Stage → test class mapping

| Stage | Core files | Test class |
|-------|-----------|------------|
| 0 — MARC Parse | `converter/parser/`, `converter/transformer/field_handlers.py`, `workers.py MarcParseWorker` | `TestMarcParseWorker` |
| 1 — NER | `ner/`, `workers.py NerWorker`, `ner_panel.py` | `TestNerWorker` |
| 2 — Authority | `converter/authority/`, `workers.py AuthorityWorker`, `authority_panel.py` | `TestAuthorityWorker`, `TestMazalIndexWorker`, `TestKimaIndexWorker` |
| 3 — RDF Build | `converter/transformer/mapper.py`, `workers.py RdfBuildWorker`, `rdf_panel.py` | `TestRdfBuildWorker` |
| 4 — SHACL | `converter/validation/`, `workers.py ShaclValidateWorker`, `validate_panel.py` | `TestShaclValidateWorker` |
| 5 — Wikidata | `workers.py WikidataUploadWorker`, `wikidata_panel.py` | `TestWikidataUploadWorker` |
| Controller | `pipeline_controller.py`, `settings_manager.py` | `TestPipelineControllerChain` |

## Procedure

1. Identify which files were modified in this session (or use the argument if provided).
2. Map each changed file to one or more test classes using the table above.
3. If only a subset of stages was touched, run only those classes:
   ```bash
   cd /Users/alexandergo/Documents/Doctorat/pipeline
   PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q --tb=short \
     -k "TestAuthorityWorker or TestMazalIndexWorker" 2>&1 | tail -40
   ```
4. If changes span multiple stages or the argument is "all", run the full suite:
   ```bash
   cd /Users/alexandergo/Documents/Doctorat/pipeline
   PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -60
   ```
5. Report: how many tests passed, failed, skipped; show failure output and diagnose the root cause.

## After any code change — always run at minimum the affected stage's tests

If you modified any file under `converter/authority/` or `src/mhm_pipeline/controller/workers.py AuthorityWorker`:
→ run `TestAuthorityWorker` and `TestMazalIndexWorker`

If you modified `ner/inference_pipeline.py` or `NerWorker`:
→ run `TestNerWorker`

Never mark a task complete if its stage tests are failing.
