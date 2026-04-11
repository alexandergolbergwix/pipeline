# MHM Pipeline — Claude Instructions

## Before Any Planning or Implementation

**Always read these two documents first:**

- `ProjectDefinitionDocument.tex` — defines the pipeline's research context, all six stages, MARC field mappings, data inventory, component interfaces, and technical requirements.
- `SystemDesignDocument.tex` — defines the chosen framework (PyQt6), distribution strategy (uv + native installers), application architecture, module structure, GUI design, and clean code standards.

Do not propose or implement anything that contradicts or ignores these documents without first flagging the conflict to the user.

## When to Update the Design Documents

Update `SystemDesignDocument.tex` whenever:

- The application architecture changes (new layers, new components, removed components)
- The GUI design changes (new screens, renamed tabs, new workflows)
- The distribution or installer strategy changes
- The module/package structure changes (`src/mhm_pipeline/` layout, entry points)
- New cross-platform considerations are identified
- The clean code toolchain changes (e.g., replacing ruff, switching to a different test runner)

Update `ProjectDefinitionDocument.tex` whenever:

- A new pipeline stage is added or an existing stage is modified
- MARC field mappings change
- A new external API or authority source is integrated
- Hardware or software requirements change
- The data inventory changes (new model files, new data files)

**Rule:** A code change that alters the system design is not complete until the relevant `.tex` document is also updated. Treat the documents as the source of truth for architecture decisions.

## Project Overview

MHM (Mapping Hebrew Manuscripts) is an end-to-end MARC-to-RDF conversion pipeline:

1. **Stage 1** — MARC Input Parsing (`UnifiedReader` + `field_handlers.py`)
2. **Stage 2** — NER Extraction (3 models: Person + Provenance + Contents)
3. **Stage 3** — Authority Resolution (Mazal/NLI, VIAF, KIMA)
4. **Stage 4** — RDF Graph Construction (`MarcToRdfMapper`, HMO ontology)
5. **Stage 5** — SHACL Validation (`pyshacl`)
6. **Stage 6** — Wikidata Upload (API via WikibaseIntegrator + QuickStatements dry-run)

Key paths:
- GUI entry point: `src/mhm_pipeline/app.py`
- Main window: `src/mhm_pipeline/gui/main_window.py`
- NER inference (persons): `ner/inference_pipeline.py` (`JointNERPipeline`, model: `alexgoldberg/hebrew-manuscript-joint-ner-v2`)
- NER inference (provenance + contents): `ner/ner_inference_pipeline.py` (`NERInferencePipeline`, supports shared DictaBERT base)
- NER models: `ner/provenance_ner_model.pt` (95.91% F1 v2 multi-entity, OWNER/DATE/COLLECTION), `ner/contents_ner_model.pt` (99.99% F1, WORK/FOLIO/WORK_AUTHOR)
- Wikidata property mapping: `converter/wikidata/property_mapping.py` (50 genre QIDs, 30 LCSH subject QIDs, 13 Bible book QIDs, 14 Talmud tractate QIDs, Hebrew century date parsing)
- NER training: `ner/train_ner_model_kfold.py` (generic DictaBERT + token-classification head, 5-fold CV)
- Editable entity results: `src/mhm_pipeline/gui/widgets/extraction_editor.py` (`ExtractionEditor`, `EditableEntityModel`)
- RDF mapper: `converter/transformer/mapper.py` (`MarcToRdfMapper`)
- Mazal authority DB: `converter/authority/mazal_index.db`
- KIMA authority DB: `data/kima/kima_index.db` (built from TSVs in `data/kima/`)
- KIMA data: `data/kima/` — three TSV files (places, Hebrew variants, Maagarim)
- Ontology: `ontology/hebrew-manuscripts.ttl`
- SHACL shapes: `ontology/shacl-shapes.ttl`

## Claude Code Skills (Slash Commands)

Project-specific slash commands are stored in `.claude/commands/`. Use them with `/skill-name` in the chat:

| Command | Description |
|---|---|
| `/run-tests` | Run the full test suite (`tests/`) |
| `/run-e2e` | Run only the e2e integration tests |
| `/check-coverage` | Measure ontology class/property coverage over 200 TSV records |
| `/launch-app` | Launch the PyQt6 GUI (opens a new Terminal window) |
| `/update-docs` | Check and update `SystemDesignDocument.tex` / `ProjectDefinitionDocument.tex` |

## Code Standards

This project is open source (GPL). Follow these rules on every change:

- Use `pyproject.toml` as the single source of dependency and tool configuration
- All Python code must have type annotations; never use `Any`
- Format and lint with **ruff** before committing
- Type-check with **mypy** (strict mode)
- Test files use `.spec.py` extension under `tests/`
- Use `pathlib.Path` for all file paths — never `os.path` string concatenation
- GPU device selection must always fall through: MPS → CUDA → CPU
- Never hardcode absolute paths; use `platformdirs` for app data directories
- Prefer pure functions over deeply nested if statements — use predicate functions like `should_handle()`, `is_something()`, `has_data()` to make logic explicit and testable

---

## Learned Rules — Avoid Known Pitfalls

These rules were derived from real errors hit during development. Follow them exactly to avoid repeating them.

### 1. Always create README.md before running uv sync

`pyproject.toml` contains `readme = "README.md"`. If the file does not exist, `uv sync` and `uv build` will fail with `OSError: Readme file does not exist`. Always ensure `README.md` exists at the repo root before running any uv command.

### 2. Never import torch or transformers at module top level

`torch` and `transformers` are optional and may not be installed (e.g. during GUI-only testing). Any module that uses them must import lazily inside the function body:

```python
# WRONG — breaks when torch is not installed
import torch

# CORRECT — lazy import inside the function
def get_device() -> str:
    try:
        import torch  # noqa: PLC0415
        ...
    except ImportError:
        return "cpu"
```

This applies to all files in `src/mhm_pipeline/platform_/`, `controller/workers.py`, and any file that imports from `ner/`.

### 3. Always specify --python 3.12 explicitly with uv

Running `uv venv` or `uv sync` without `--python 3.12` will pick the newest available Python (currently 3.14), creating a venv that is incompatible with pinned dependencies. Always use:

```bash
uv venv --python 3.12
uv sync --python 3.12
```

### 4. Run uv lock before uv sync --frozen

`uv sync --frozen` requires `uv.lock` to exist. If it does not exist (e.g. after a fresh clone or after editing `pyproject.toml`), run `uv lock` first:

```bash
uv lock
uv sync --frozen
```

### 5. Always set PYTHONPATH=src:. when running the app from the repo root

The project uses a `src/` layout. Without `PYTHONPATH=src:.` the `mhm_pipeline` package is not importable:

```bash
# WRONG
python -m mhm_pipeline.app

# CORRECT
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app
```

### 6. Use Read tool on background task output files — never block with TaskOutput

`TaskOutput` with a large timeout causes "not responding" in the UI. Background tasks write their output to a file path returned in the task result. Use the `Read` tool on that path to check progress non-blockingly:

```
# WRONG — blocks and causes "not responding"
TaskOutput(task_id=..., block=True, timeout=240000)

# CORRECT — non-blocking check
Read(file_path="/private/tmp/.../tasks/<id>.output")
```

### 7. Set first_run_done=True when testing to skip the setup wizard

On first launch, `app.py` shows a `QWizard` for model download. In a terminal test this wizard may open and close silently, causing `sys.exit(0)` before the main window appears. Skip it by setting the flag once:

```bash
PYTHONPATH=src:. .venv/bin/python -c "
from mhm_pipeline.settings.settings_manager import SettingsManager
SettingsManager().first_run_done = True
"
```

### 8. Launch the GUI with & to keep it running from a terminal

`app.exec()` blocks until the window is closed. When launched synchronously from a Claude tool call, the process exits immediately after the window closes. Use `&` to background it:

```bash
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app &
```

To test window creation without keeping it open:

```python
from PyQt6.QtCore import QTimer
QTimer.singleShot(1500, app.quit)
sys.exit(app.exec())
```

### 9. Never run two concurrent uv installs into the same venv

Running `uv sync` and `uv pip install` simultaneously into the same `.venv` causes partial installs and version conflicts. Always wait for one uv operation to complete before starting another. Check with `ps aux | grep uv` before starting a new install.

### 10. The correct launch command (always use this as the reference)

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app
```

For a smoke test without the event loop blocking:

```bash
PYTHONPATH=src:. .venv/bin/python -c "
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from mhm_pipeline.settings.settings_manager import SettingsManager
from mhm_pipeline.controller.pipeline_controller import PipelineController
from mhm_pipeline.gui.main_window import MainWindow
app = QApplication(sys.argv)
window = MainWindow(SettingsManager(), PipelineController(SettingsManager()))
window.show()
print('visible:', window.isVisible(), '| size:', window.size())
QTimer.singleShot(1500, app.quit)
sys.exit(app.exec())
"
```

### 11. VIAF API requires Accept header — never use recordSchema param

The VIAF SRU API no longer returns JSON via `recordSchema=info:srw/schema/1/JSON`. It now requires the `Accept: application/json` HTTP header. Without it, the API returns an HTML page and `resp.json()` fails silently. The JSON response structure is also namespaced: `records.record[].recordData.ns2:VIAFCluster.ns2:viafID`. Rate limit: max 2 requests per second (0.5s between requests).

### 12. Always verify KIMA index DB exists before running authority matching

`data/kima/kima_index.db` must be built from TSV source files before KIMA place matching works. The matcher silently returns `None` (only logs at DEBUG level) when the DB is missing — it does NOT raise an error. After a fresh clone or if KIMA returns zero results, rebuild:

```bash
PYTHONPATH=src:. .venv/bin/python -c "
from converter.authority.kima_index import build_kima_index
build_kima_index('data/kima', 'data/kima/kima_index.db', verbose=True)
"
```

### 13. AuthorityWorker input_path is MARC extract, not NER results

`AuthorityWorker` takes the MARC extract (stage 0 output) as `input_path` and NER results (stage 1 output) as optional `ner_path`. NER entities are merged into MARC records by `_control_number` before authority matching. This ensures MARC name fields (100/110/111/700/710/711) are always matched, even without running NER.

```python
# WRONG — old API
AuthorityWorker(input_path=ner_results, marc_path=marc_extract, ...)

# CORRECT — current API
AuthorityWorker(input_path=marc_extract, ner_path=ner_results, ...)
```

### 14. Wikidata upload: OAuth 2.0 format, batch mode, and no SPARQL reconciliation

The `WikidataUploader` supports three authentication methods. The token format determines which method is used:

- **Bot password:** `Username@BotName:password`
- **OAuth 2.0:** `consumer_key|consumer_secret` (2 pipe-separated parts)
- **OAuth 1.0a:** `consumer_key|consumer_secret|access_token|access_secret` (4 pipe-separated parts)

SPARQL reconciliation has been removed from the upload pipeline — it was too slow and unreliable. Instead, items with `existing_qid` from authority matching (VIAF/NLI IDs) are updated; items without are created as new entities.

Rate limiting: 1.5s between edits (~40 edits/minute), with batch mode pausing 30s every 45 items. Batch mode is ON by default for live uploads. WikibaseIntegrator backoff is capped at 30s (not the default 3600s).

```python
# WRONG — old token kwarg (removed)
WikidataUploader(token="bearer-token-string")

# CORRECT — OAuth 2.0
WikidataUploader(token="consumer_key|consumer_secret", batch_mode=True)

# CORRECT — Bot password
WikidataUploader(token="User@Bot:password", batch_mode=True)
```

### 15. WikidataPanel entity_status signal must be null-safe

The `entity_status` signal emits `(str, str, str, str)`. The callback wraps every argument with `str(... or "")` because `None` values cause SIGABRT when passed through Qt signal marshalling. The panel uses `add_entity()` + `set_status()` instead of the removed `update_entity()` method.

### 16. Always call worker.wait() before dropping QThread reference

Dropping a `QThread` reference while the thread is still running causes SIGABRT from Qt's destructor. Both `_on_worker_finished` and `_on_worker_error` in `PipelineController` must call `worker.wait()` before setting `self._current_worker = None`.

```python
# WRONG — GC crash
def _on_worker_finished(self, stage_index, output_path):
    self._current_worker = None  # QThread still running → SIGABRT

# CORRECT — wait for thread to stop
def _on_worker_finished(self, stage_index, output_path):
    if self._current_worker is not None:
        self._current_worker.wait()
    self._current_worker = None
```

### 17. NER model files and F1 scores (current)

The pipeline uses three NER models. Keep these F1 scores current:

| Model | File | F1 | Entity types |
|---|---|---|---|
| Person NER | `alexgoldberg/hebrew-manuscript-joint-ner-v2` (HuggingFace) | 85.70% | PERSON (with roles) |
| Provenance NER v2 | `ner/provenance_ner_model.pt` (704 MB) | 95.91% (best fold 96.17%) | OWNER, DATE, COLLECTION |
| Contents NER | `ner/contents_ner_model.pt` (704 MB) | 99.99% | WORK, FOLIO, WORK_AUTHOR |

Provenance v2 was trained on 12,100 samples (28.4% multi-entity augmented) with `max_length=128`. The v1 model (93.96% F1, `max_length=64`) is superseded.

### 18. Wikidata property coverage (100 richest manuscripts, v1.9)

Per-property coverage from `WikidataItemBuilder` on 100 manuscripts:

| Property | Claims | MS Coverage | Notes |
|---|---|---|---|
| P50 (author) | 729 | 100% | avg 7.3/MS |
| P571 (inception) | — | 96% | Hebrew century parsing: מאה ט"ז → 1550 |
| P6216 (copyright) | — | 100% | Public domain for pre-1900 works |
| P136 (genre) | — | 53% | 100% of MSS with genre data; 50 QID mappings |
| P921 (main subject) | 91 | 46% | 30 LCSH + 13 Bible + 14 Talmud QID mappings |
| P1071 (location) | — | 79% | KIMA place authority |
| P127 (owned by) | 53 | 43% | Provenance NER |
| P11603 (transcribed by) | 20 | 18% | NER + role classification |
| P17 (country) | — | 100% | Israel (hardcoded for NLI) |
| P131 (located in) | — | 100% | Jerusalem (hardcoded for NLI) |
| Avg statements/MS | ~22.9 | — | |

Person entity properties (v1.9):

| Property | Coverage | Notes |
|---|---|---|
| P31 (instance of) | 100% | Q5 (human) or Q43229 (organization) |
| P106 (occupation) | ~80% | From role mapping |
| P8189 (NLI J9U ID) | ~65% | From Mazal authority match |
| P214 (VIAF ID) | ~35% | From VIAF name matching |
| P21 (sex/gender) | 100% | Q6581097 (male) for non-orgs |
| P1343 (described by) | 100% | Q118384267 (Ktiv) |
| P1412 (language) | 100% | Q9288 (Hebrew) for non-orgs |
| P1559 (native name) | 100% | Hebrew name for non-orgs |
| P227 (GND) | ~20% | VIAF cluster harvesting |
| P244 (LCCN) | ~20% | VIAF cluster harvesting |
| P213 (ISNI) | ~15% | VIAF cluster harvesting |
| P268 (BnF) | ~10% | VIAF cluster harvesting |
| Avg statements/person | ~6.5 | Was 4.2 in v1.8 |

### 19. Genre and subject QID mappings live in property_mapping.py

All genre and subject term to Wikidata QID mappings are centralized in `converter/wikidata/property_mapping.py`:

- `GENRE_TO_QID` — 50 entries (10 HMO ontology types + 40 MARC genre/form strings)
- `SUBJECT_TO_QID` — 30 LCSH subject headings
- `BIBLE_BOOK_TO_QID` — 13 Bible books
- `TALMUD_TRACTATE_TO_QID` — 14 Talmud Bavli tractates

When adding new QID mappings, add them to the appropriate dict in this file. Do not hardcode QIDs in `item_builder.py`.

### 20. Hebrew century date parsing in date_to_wikidata()

`date_to_wikidata()` in `property_mapping.py` handles Hebrew century strings (e.g., `מאה ט"ז` = 16th century = 1550). The `_HEBREW_ORDINAL_TO_INT` dict maps Hebrew ordinals to century numbers. CSV double-quote escaping (`""`) is cleaned before parsing. Coverage went from 22% to 96% of manuscripts after implementing this.

### 21. Wikidata value types must match property constraints

Wikidata properties have strict value type constraints. Common pitfalls:

- `P8189` (NLI J9U ID) and `P214` (VIAF ID) require `external-id`, not `string`
- `P5816` (state of conservation) requires `item` QIDs, not free-text strings
- `P527` (has parts) requires `item` QIDs, not work title strings
- `P195` (collection) requires `item` QIDs, not collection name strings

When a property expects an `item` but only a string is available, skip the claim rather than uploading an invalid type.
