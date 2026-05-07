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

### 11. VIAF API requires Accept header — never use recordSchema param or /viaf.json

The VIAF SRU API no longer returns JSON via `recordSchema=info:srw/schema/1/JSON`. It now requires the `Accept: application/json` HTTP header. Without it, the API returns an HTML page and `resp.json()` fails silently. The SRU JSON response structure is namespaced: `records.record[].recordData.ns2:VIAFCluster.ns2:viafID`. Rate limit: max 2 requests per second (0.5s between requests).

**Cluster JSON endpoint** (for harvesting identifiers): The old `/viaf/{id}/viaf.json` endpoint was removed. Use `https://viaf.org/viaf/{id}` with `Accept: application/json` header instead. The response is wrapped in `ns1:VIAFCluster` (not bare keys). Sources are at `ns1:sources.ns1:source[]` with `content` field (not `#text`), format `PREFIX|ID` (e.g., `DNB|118576488`, `LC|n 78096039`, `ISNI|0000000123750072`). ISNI comes from the sources array, not a separate `ISNIs` field.

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
| P1574 (exemplar of) | 4,162 | 100% | Auto-created work items (3,970) |
| P7535 (notes+prov) | 701 | 100% | MARC 500 notes + 561 provenance text |
| P2635 (CU count) | 99 | 99% | Codicological units count |
| P1684 (inscription) | 41 | 41% | Colophon text + scribal interventions |
| P7153 (sig. place) | 82 | 82% | Related places via KIMA |
| Avg statements/MS | 73.6 | — | v2.0 (was 22.9 in v1.9) |

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
| Avg statements/person | 7.5 | Was 4.2 in v1.8 |

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

### 22. VIAF cluster harvesting adds P227/P244/P213/P268 to persons

`VIAFMatcher.get_cluster_identifiers(viaf_id)` fetches the full VIAF cluster JSON and extracts cross-referenced authority identifiers. These flow through `AuthorityWorker._match_marc_person_entry()` into `match_info["gnd_id"]`, `match_info["lc_id"]`, `match_info["isni"]`, `match_info["bnf_id"]`, then into `WikidataItemBuilder._get_or_create_person()` as external-id claims. The method also extracts J9U (NLI) IDs from the cluster.

Person entities also get hardcoded properties: P1412 (Hebrew, Q9288), P1559 (native name in Hebrew), P21 (male, Q6581097), P1343 (Ktiv, Q118384267). Manuscripts get P17 (Israel, Q801) and P131 (Jerusalem, Q1218). All hardcoded properties skip organizations (detected by keyword in name).

### 23. Wikidata safety guards — NEVER bypass (added 2026-04-13)

On 2026-04-12 a cleanup script merged 902+ unrelated Wikidata entities (people, bands, organizations) because the pipeline trusted a single shared identifier (e.g., ISNI). Several community members filed complaints (Pallor, Kolja21, Epìdosis). The following guards now exist and **must not** be bypassed without explicit user request:

1. **Reconciler cross-identifier verification** — `WikidataReconciler._candidate_conflicts()` in `converter/wikidata/reconciler.py`. When a candidate matches by one identifier (VIAF/NLI/LCCN/GND/ISNI), the reconciler fetches all other identifiers on the candidate and rejects the match if any conflict. The candidate is treated as a different real-world entity and a new item is created instead.

2. **Uploader identity-conflict guard** — `WikidataUploader._would_create_identity_conflict()` in `converter/wikidata/uploader.py`. Refuses to add a value to P569/P570/P19/P20/P227/P214/P8189/P213/P244/P31/P21 on an existing item if that item already has a different value for that property. P569/P570 compare on date prefix (first 11 chars) to ignore precision differences.

3. **Uploader label-overwrite guard** — `_build_wbi_item()` in `converter/wikidata/uploader.py` only sets a label/alias on an existing item when the language slot is empty. Never overwrites an existing label.

4. **Creator-author check** — `_is_our_item()` in `converter/wikidata/uploader.py` and `is_our_item()` in `scripts/merge_duplicates.py` and `scripts/fix_wikidata_items.py`. Verifies first revision author == authenticated user before any modification. Refuses to touch items not created by us, regardless of QID range.

5. **Pre-merge metadata conflict check** — `_has_conflict()` in `scripts/merge_duplicates.py`. Before any `wbmergeitems` call, fetches both source and target claims for P569/P570/P19/P20/P227/P214/P8189/P213/P244 and refuses the merge if any of those properties has different values on the two items.

Tests: `tests/unit/test_safety_guards.py` (19 tests) verify these guards. Do NOT delete or weaken these tests — they are the regression barrier.

### 24. Wikidata revert scripts — TWO-LAYER editor check (added 2026-04-13)

Every script in `scripts/` that issues `action=edit&undo=<my_revid>` MUST go through `scripts/lib/wikidata_safety.is_safe_to_revert()`. That helper enforces both checks and may NEVER be bypassed:

1. **Creator check** — first revision author of the item ≠ authenticated user. Otherwise the item is ours; nothing to revert.
2. **Latest-editor check** — most recent revision of the item == authenticated user. Otherwise someone else (e.g., Epìdosis re-applying a merge that was actually correct) has touched the item since our edit, and undoing our older revision would silently override their correction.

The Epìdosis incident: on 2026-04-13 Epìdosis re-applied four merges I had wrongly reverted (Q109877110, Q479063, Q159933, Q55902460), commenting "Already checked, correct merge". A naive re-run of the revert script would have undone those corrections. The latest-editor check makes that impossible.

Use `RetryingSession` from the same module for all HTTP — it survives transient DNS / TCP outages with exponential backoff (six attempts, capped at 30 s). See `scripts/revert_my_modifications.py` for the canonical pattern.

### 25. Wikidata bulk operations — MORATORIUM until pipeline bugs are fixed (added 2026-04-15)

After community feedback from Geagea (Wikidata sysop) on 2026-04-14, the MHM Pipeline is under a self-imposed moratorium on automated Wikidata operations. NO bulk uploads, merges, or edits to Wikidata are permitted until ALL of the following are true:

1. **Bug #1 (reconciler false negatives) — FIXED**: The reconciler in `converter/wikidata/reconciler.py` checks all five identifier types (P244 LCCN, P227 GND, P213 ISNI, P214 VIAF, P8189 J9U) before creating any new person item. Most of the duplicates Geagea flagged were existing Wikidata items the reconciler missed. Fix is verified by `tests/unit/test_safety_guards.py::TestReconcilerVerification`.

2. **Bug #2 (P8189 type confusion) — FIXED**: The item builder in `converter/wikidata/item_builder.py` only attaches P8189 (NLI J9U ID) when ALL three are true: the source NLI ID has prefix `9870…` (authority record, not bibliographic `990…`), the target item is `P31=Q5` (human), and the Mazal entity_type is `person`. Never on manuscripts (Q87167) or works.

3. **Bug #3 (Hebrew label form) — FIXED**: Hebrew labels on person items use natural order (`Given Surname`), not the MARC inverted form (`Surname, Given`). The inverted form is preserved in P1559 (native name) for searchability.

4. **Bug #4 (institutional holders mis-mapped to P50) — FIXED**: MARC 710 (added entry — corporate name) is mapped to P195 (collection) or P127 (owned by), never to P50 (author). The MHM mapper only assigns P50 from MARC 100 (main entry — personal name) or 700 (added entry — personal name) where the contributor role is verified as author/scribe.

5. **Manual experience requirement**: I have made at least 20 manual (non-scripted) edits on Wikidata to learn the system's conventions, as Geagea explicitly requested.

6. **Community announcement**: Before any bulk operation resumes, a notice is posted on [Wikidata:Project chat](https://www.wikidata.org/wiki/Wikidata:Project_chat) describing the planned operation, the corpus size, and the safety guards. Wait at least 48 hours for community feedback before running.

7. **Test batch**: First run after the moratorium is at most 10 items, manually reviewed by me before scaling up. If the community raises any concern within 48 hours, halt and address before continuing.

8. **Bot flag granted** (added 2026-04-15 web audit): A Wikidata bot flag has been issued via the standard RfP process at [Wikidata:Requests for permissions/Bot](https://www.wikidata.org/wiki/Wikidata:Requests_for_permissions/Bot). Edit summaries are passed on every WBI write (enforced by `tests/unit/test_safety_guards.py::TestEditSummaryPassed`).

9. **Pipeline data-quality fixes verified** (added 2026-04-15 web audit): All eight fixes from the 2026-04-15 web audit (century date encoding, P21 omission, edit summaries, P1412 derivation, work-description disambiguation, work-item reconciliation against Wikidata, MARC 710 institutional re-routing, P8189 prefix restriction) have unit tests in `tests/unit/test_safety_guards.py` and the tests pass.

This rule has no expiry. It is lifted only when conditions 1–9 are jointly met. The `WikidataUploader` refuses to run against production Wikidata if a `MORATORIUM_LIFTED=true` environment variable is not set; this enforces the moratorium at the code level (see `_check_moratorium_for_live`).

Related community talk threads:
- User talk:Alexander Goldberg IL § "Please stop your edits" (Geagea, 2026-04-14)
- User talk:Alexander Goldberg IL § "Wrong merge" (Pallor, Kolja21, Epìdosis, 2026-04-12 → 2026-04-14)
- Property talk:P8189/Duplicates/humans

### 26. Pipeline data-quality fixes from web audit (added 2026-04-15)

A thorough web-research audit on 2026-04-15 identified ten Wikidata best-practice violations beyond those Geagea explicitly named. Eight of them were fixed in commit (this commit). Each fix has a unit test in `tests/unit/test_safety_guards.py`.

| Fix | File | Wikidata policy / source |
|---|---|---|
| #1 Century dates encode the START of the century, not the midpoint | `converter/wikidata/property_mapping.py:date_to_wikidata` | [Help:Dates](https://www.wikidata.org/wiki/Help:Dates), [Phabricator T73459](https://phabricator.wikimedia.org/T73459) |
| #2 Work-item reconciliation by Hebrew label + author before creating | `converter/wikidata/reconciler.py:reconcile_work_by_label_and_author` + `item_builder.py:_get_or_create_work` | [WikiProject Duplicates](https://www.wikidata.org/wiki/Wikidata:WikiProject_Duplicates) |
| #4 P21 (gender) NOT blanket-set to male; omit when source has no gender data | `converter/wikidata/item_builder.py:_get_or_create_person` | [UW iSchool 2023 P21 study](https://ischool.uw.edu/capstone/projects/2023/p21-problem-proposing-more-ethical-best-practice-sex-and-gender-wikidata) |
| #5 `maxlag=5` already set in WBI config | `converter/wikidata/uploader.py:_init_wbi` | [Wikidata:Bots](https://www.wikidata.org/wiki/Wikidata:Bots) |
| #6 Descriptive `summary=` parameter on every WBI write | `converter/wikidata/uploader.py:upload_item` | [Wikidata:Bots](https://www.wikidata.org/wiki/Wikidata:Bots) |
| #7 P1412 (language) derived from manuscript MARC 008/041, not blanket Hebrew | `converter/wikidata/item_builder.py:_get_or_create_person` | [Sourcing requirements for bots RfC](https://www.wikidata.org/wiki/Wikidata:Requests_for_comment/Sourcing_requirements_for_bots) |
| #8 Disambiguating work descriptions (include author + century) | `converter/wikidata/item_builder.py:_build_work_description` | Wikidata description-uniqueness convention |

Two audit items remain deferred (out of scope for this commit):

- **VIAF cluster cross-validation (#9)**: harvested IDs cross-checked at upload time by `_candidate_conflicts()`; live VIAF re-validation per cluster would add latency. Re-evaluate after the test-batch run.
- **Stop-on-revert mechanism (audit miscellaneous)**: needs a separate watchlist polling layer; the two-layer creator/latest-editor check on revert scripts already covers the live case.

Tests: `tests/unit/test_safety_guards.py` now has 91 tests across all guards (was 19 → 34 after Geagea-fix batch → 53 after web-audit batch → 84 after deeper-audit batch → 91 after Geagea P3959/kovetz batch). Do NOT delete or weaken these tests — they are the regression barrier protecting against repeat incidents.

### 27. Geagea P3959 + "קובץ." label complaints (added 2026-04-15)

On 2026-04-15 Geagea (Wikidata sysop) flagged two further problems:

1. **P3959 (NNL item ID) misuse**: more than 100 of my person items had `P3959` (a BIBLIOGRAPHIC catalog identifier with prefix `99…`) instead of `P8189` (the AUTHORITY-record identifier with prefix `9870…`). Geagea cleaned them via `#temporary_batch_1776243998556`. Only **two** of my items still carry P3959 (Q139159451, Q139328025). Investigation showed the **current pipeline source code does not emit P3959** anywhere — the bad batch came from a one-off script that pre-dated the current safety guards.

   Code-level enforcement: `tests/unit/test_safety_guards.py::TestP3959NotEmittedByPipeline` recursively grep-scans the entire `converter/` and `src/` trees for any non-comment occurrence of the literal `P3959` and fails the test suite if one is reintroduced.

2. **Generic "קובץ." Hebrew labels**: 94 manuscript items had Hebrew labels `קובץ.` (= "compilation"), `קובץ בקבלה.` ("compilation on Kabbalah"), or similar generic catalog placeholders that NLI catalogers use when an anthology has no overarching real title. Emitted by the pipeline because MARC 245 was taken verbatim.

   Pipeline fix: `_is_placeholder_title()` in `converter/wikidata/item_builder.py` now detects these placeholder strings. `_set_labels()` routes them to a Hebrew alias (preserving searchability) and emits a synthetic shelfmark-based Hebrew label (`כתב יד עברי, ספרייה לאומית, <shelfmark>`) instead of the placeholder.

   Cleanup: the 94 already-uploaded items will be cleaned by `scripts/cleanup_generic_kovetz_labels.py` once the moratorium is lifted (the script refuses to run unless `MORATORIUM_LIFTED=true`). Likewise `scripts/fix_p3959_residual.py` for the remaining two P3959 items. Both scripts use the standard 3-rule `is_safe_to_revert()` guard so they cannot touch items I did not create or items where the community has since edited.

Tests added (7): `TestKovetzPlaceholderTitleFilter` (6) + `TestP3959NotEmittedByPipeline` (1). Total now 91.

### 28. Third audit pipeline fixes (added 2026-04-15)

A third deeper web-research + code audit (2026-04-15) found 17 additional issues. All fixed in one commit. Tests now total 130.

| Fix | Description | File | Wikidata policy |
|---|---|---|---|
| #1 | P217 (inventory number) gets required P195 (collection) qualifier | `item_builder.py` | [Property:P217](https://www.wikidata.org/wiki/Property:P217) |
| #2 | P7153 (significant place) gets required P3831 (object has role) qualifier | `item_builder.py` | [Property:P7153](https://www.wikidata.org/wiki/Property:P7153) |
| #3 | P887 (based on heuristic) moved from statement qualifier to reference block | `item_builder.py` | [Property:P887](https://www.wikidata.org/wiki/Property:P887) |
| #4 | Notability gate: person items require at least one external ID (VIAF/NLI/LCCN/GND/ISNI/BnF) | `item_builder.py` | [Wikidata:Notability](https://www.wikidata.org/wiki/Wikidata:Notability) |
| #5 | Anonymous/unknown person names filtered — never create items | `item_builder.py` | [Wikidata:Notability](https://www.wikidata.org/wiki/Wikidata:Notability) |
| #6 | Work items get English label (shelfmark-based fallback when title is Hebrew) | `item_builder.py` | [Help:Label](https://www.wikidata.org/wiki/Help:Label) |
| #7 | P407 (language of work) derived from manuscript MARC 008/041, not hardcoded Hebrew | `item_builder.py` | [WikiProject Books](https://www.wikidata.org/wiki/Wikidata:WikiProject_Books) |
| #8 | P2093 (author name string) fallback for persons skipped by notability gate | `item_builder.py` | [Property:P2093](https://www.wikidata.org/wiki/Property:P2093) |
| #9 | LCCN/ISNI format verified against live property constraint pages | `property_mapping.py` | [P244](https://www.wikidata.org/wiki/Property:P244), [P213](https://www.wikidata.org/wiki/Property:P213) |
| #10 | P1343=Ktiv removed as main statement (catalog ≠ descriptive publication) | `item_builder.py` | [Property:P1343](https://www.wikidata.org/wiki/Property:P1343) |
| #11 | P6216 (public domain) gets P1001=Q801 jurisdiction qualifier (Israel) | `item_builder.py` | [Property:P6216](https://www.wikidata.org/wiki/Property:P6216) |
| #12 | Century P571 dates get P1319/P1326 start/end bounds as qualifiers | `item_builder.py`, `property_mapping.py` | [Help:Dates](https://www.wikidata.org/wiki/Help:Dates) |
| #13 | Pre-1582 dates use Julian calendar model (Q1985786) | `property_mapping.py` | [Help:Dates](https://www.wikidata.org/wiki/Help:Dates) |
| #14 | Descriptions capped at 250 characters (`_cap_description()`) | `item_builder.py` | [Help:Description](https://www.wikidata.org/wiki/Help:Description) |
| #15 | TRANSLATOR → P655 (translator), COMMENTATOR → P9046 (commentary by), not P50 | `property_mapping.py` | [Property:P50](https://www.wikidata.org/wiki/Property:P50) |
| #16 | MAXLAG raised from 5 to 10 seconds | `uploader.py` | [Wikidata:Bots](https://www.wikidata.org/wiki/Wikidata:Bots) |
| #17 | Edit summary truncated at 497 chars (API 500-char limit) | `uploader.py` | [Wikidata:Bots](https://www.wikidata.org/wiki/Wikidata:Bots) |

Tests added (39): `TestP217HasP195Qualifier`, `TestP7153HasP3831Qualifier`, `TestP887InReferenceNotQualifier`, `TestNotabilityGate`, `TestAnonymousPersonFilter`, `TestWorkItemEnglishLabel`, `TestWorkP407DerivedFromManuscript`, `TestP2093Fallback`, `TestP1343NotAsStatement`, `TestP6216HasJurisdictionQualifier`, `TestCenturyDateBounds`, `TestCalendarModel`, `TestDescriptionLengthCap`, `TestTranslatorCommentatorProperties`, `TestMaxlag`, `TestEditSummaryTruncation`. Total now **130**.

### 29. VIAF nameType cross-validation (added 2026-04-15)

After the 2026-04-15 Wikidata talk report (three library items — Q138937383, Q139185337, Q139169280 — received person-type VIAF IDs), an investigation found three code gaps that together caused the incident:

1. **`VIAFMatcher._query_api()`** never read the `ns2:nameType` field from the SRU response, so Corporate or Geographic clusters surfaced by `local.personalNames` were returned as if they were valid person matches.
2. **`item_builder.py` P214 assignment** had no `is_org` guard — even if the pipeline detected the holder as an organisation, the VIAF ID was still attached.
3. **`VIAFMatcher.get_cluster_identifiers()`** did not extract `nameType`, so callers could not validate the cluster type independently.

**Fixes applied (commit after 571d2e9):**

| Fix | File | Description |
|---|---|---|
| nameType SRU filter | `converter/authority/viaf_matcher.py:_query_api` | Reads `ns2:nameType`; rejects cluster if `nameType != expected_name_type`. Absent nameType is accepted (backward compatibility with older API responses). |
| match_person type guard | `converter/authority/viaf_matcher.py:match_person` | Passes `expected_name_type="Personal"` to `_search()` → `_query_api()`. |
| match_place type guard | `converter/authority/viaf_matcher.py:match_place` | Passes `expected_name_type="Geographic"`. |
| name_type in cluster dict | `converter/authority/viaf_matcher.py:get_cluster_identifiers` | Extracts `ns1:nameType` and stores it as `ids["name_type"]` for callers. |
| P214 is_org guard | `converter/wikidata/item_builder.py` | `if viaf_id and not is_org:` — P214 is never attached to organisation items. |

Tests added (9): `TestVIAFNameTypeGuard` (9 tests — `test_match_person_rejects_corporate_cluster`, `test_match_person_accepts_personal_cluster`, `test_match_place_rejects_personal_cluster`, `test_match_place_accepts_geographic_cluster`, `test_missing_name_type_not_rejected`, `test_get_cluster_identifiers_returns_name_type`, `test_p214_guarded_by_not_is_org_in_source`, `test_match_person_passes_expected_name_type_personal`, `test_match_place_passes_expected_name_type_geographic`). Total now **139**.

### 30. Fourth audit pipeline fixes (added 2026-04-16)

A follow-up web audit (2026-04-16) found three more issues discovered through community feedback:

| Fix | Description | File | Wikidata policy |
|---|---|---|---|
| #1 | P7153 P3831 qualifier: replace Q1616923 (Heydeck disambiguation page) with Q1773840 (provenance concept) | `item_builder.py` | [Property:P3831](https://www.wikidata.org/wiki/Property:P3831), [Q1773840](https://www.wikidata.org/wiki/Q1773840) |
| #2 | Organization/meeting contributors skip VIAF person-name search in `_match_against_authorities()` | `workers.py` | VIAF nameType cross-validation |
| #3 | P2093 fallback adds P3831 role qualifier (scribe=Q916292, translator=Q333634, commentator=Q106313281); owner role suppressed (P127 has no string fallback — covered by P7535 provenance text) | `item_builder.py` | [Property:P2093](https://www.wikidata.org/wiki/Property:P2093), [Property:P3831](https://www.wikidata.org/wiki/Property:P3831) |

Tests added (8): `TestP7153RoleQIDIsProvenance` (2), `TestOrgTypeSkipsVIAFPersonSearch` (3), `TestP2093RoleQualifier` (3). Total now **147**.

### 31. QuickStatements output QA fixes (added 2026-04-19)

After running 6 NER-article manuscripts through the pipeline and auditing the QuickStatements output, 6 bugs were found and fixed.

| Fix | Description | File | Source |
|---|---|---|---|
| #1 | Empty CREATE blocks suppressed for notability-filtered persons (no labels/statements) | `quickstatements.py:export_item` | QS output audit |
| #2a | P2093 fallback block guarded by `not _is_institutional_name(name)` — institutions never get P2093 | `item_builder.py:~1523` | QS output audit |
| #2b | `_INSTITUTIONAL_KEYWORDS` extended with "bodleian", "palatina" | `item_builder.py:148` | QS output audit |
| #3 | Person name cleaning strips surrounding quotes: `.strip('"\')` before `rstrip(",;:")` | `item_builder.py:1602` | QS output audit |
| #4 | `_ROLE_TO_LABEL["OWNER"]` changed from "manuscript owner" to "owner" (was producing "Hebrew manuscript manuscript owner") | `item_builder.py:292` | QS output audit |
| #5 | Manuscript Hebrew/English labels strip trailing MARC ISBD periods: `title.rstrip(". ")` | `item_builder.py:1022` | QS output audit |
| #6 | QuickStatements exporter now exports `stmt.qualifiers` before references on each statement line | `quickstatements.py:export_item` | QS output audit |

Tests added (16): `TestEmptyItemNotExported` (2), `TestInstitutionalP2093Suppressed` (3), `TestPersonNameCleaning` (3), `TestOwnerDescription` (2), `TestManuscriptTitleCleaning` (3), `TestQualifierExport` (3). Total now **163**.

### 32. Second-round QS output fixes (added 2026-04-19)

Re-running 6 NER manuscripts after the first fix round revealed 3 more bugs, then a third round revealed 2 more.

**Round 2 (163 → 172 tests):**

| Fix | Description | File | Source |
|---|---|---|---|
| #A | MARC 500 source filenames (`*.mrc`, `*.txt`) filtered from P7535 via `_SOURCE_FILENAME_RE` | `item_builder.py` | QS output audit |
| #B | Arabic/non-ASCII date strings stripped from English descriptions via `_ascii_dates()` | `item_builder.py` | QS output audit |
| #C | P1932 (object named as) qualifier strips trailing MARC commas/colons in both `_add_person_statement` and `_add_provenance_claims` | `item_builder.py` | QS output audit |

Tests added (9): `TestMrcFilenameNotInNotes` (3), `TestAsciiOnlyDescription` (3), `TestP1932TrailingPunctuationStripped` (3). Total now **172**.

**Round 3 (172 → 175 tests):**

| Fix | Description | File | Source |
|---|---|---|---|
| #D | P1476 title statement strips trailing ISBD period at source: `title.rstrip(". ")` in `build_manuscript_item` | `item_builder.py:490` | QS output audit |
| #E | Variant title aliases strip trailing periods: `str(vt).strip().rstrip(". ")` in `_set_labels` | `item_builder.py:1070` | QS output audit |

Tests added (3): `TestTitleTrailingPeriodStripped` (3). Total now **175**.

### 33. Expert-requested certainty qualifiers (added 2026-04-20)

Domain experts M. Lavee and E. Baumgarten (University of Haifa, Oct 2025 review) requested a formal certainty/confidence mechanism on Wikidata claims and `possibly_realises` semantics for uncertain work identification.

| Fix | Description | File |
|---|---|---|
| #A | `Q_PRESUMABLY = "Q18122778"` and `Q_POSSIBLY = "Q21857942"` added | `property_mapping.py` |
| #B | P50/P11603/P127 statements for local (unconfirmed) persons add `P1480: Q18122778` qualifier | `item_builder.py` |
| #C | P1574 statements for unreconciled local work items add `P1480: Q18122778` qualifier (implements `possibly_realises`) | `item_builder.py` |

Confirmed-QID person statements (resolved via VIAF/NLI) get no P1480 — they are authority-confirmed.

Tests added (3): `TestUncertainAttributionP1480` (3). Total now **178**.

### 34. Distant-supervision genre classifier for P136 coverage (added 2026-04-20)

P136 (genre) coverage was 69% — 31% of manuscripts have no MARC 655 genre/form headings. A DictaBERT-based multi-label classifier trained via distant supervision fills this gap.

**Architecture:**
- Base: `dicta-il/dictabert` warm-started from provenance NER checkpoint (domain-adapted on 12,100 Hebrew manuscript samples)
- Bottom 10 of 12 BERT layers frozen; top 2 layers + head fine-tuned with differential LRs (2e-6 encoder, 2e-5 head)
- Head: Dropout(0.3) → Linear(768 → 9) → sigmoid (8 genre classes + NOTA)
- Loss: Focal loss (γ=2.0) with per-class pos_weight = n_neg/n_pos
- Training data: 25,421 records from 123k-record NLI catalog, filtered by whole-token Hebrew keyword matching in MARC 500 notes; pre-extracted to `data/tsvs/genre_samples.tsv`
- Classes with < 100 examples dropped; "Literature (Miscellaneous)" excluded (too generic)
- 1,629 NOTA examples (genres outside top-8) provide explicit abstention signal
- Metric: micro-F1 at per-fold tuned threshold (scan 0.20–0.80, step 0.05)
- Strategy: 5-fold stratified CV, 30 epochs, patience=5; best-fold checkpoint saved
- **Achieved micro-F1: 0.88** on 8-class held-out val set

**Files:**
- `ner/train_genre_classifier.py` — training script (run once to produce model)
- `scripts/extract_genre_samples.py` — one-time extraction of 26k matched records from 123k TSV
- `converter/authority/genre_classifier.py` — inference wrapper (GenreClassifier class)
- `data/tsvs/genre_samples.tsv` — pre-extracted training data (fast reload)
- `ner/genre_classifier_model.pt` — trained checkpoint (generated; not committed to git)

**Inference — sliding window for long texts:**
The model was trained on short 3-sentence context windows (max_length=64 tokens). At inference, the input (title + 3 full MARC 500 notes) may be longer. `GenreClassifier.predict()` handles this with a sliding window:
1. Tokenize full text without truncation
2. If ≤ 64 tokens: single inference call (normal case)
3. If > 64 tokens: split into overlapping 64-token windows (stride=32), score each independently, **average sigmoid probabilities across windows**, then threshold
The `max_length` is stored in the checkpoint and loaded automatically.

**Integration in `item_builder.py`:**
- After the MARC 655 genre loop, if `genres` is empty, `_get_genre_classifier()` is called
- Lazy singleton: loaded once, skipped silently if model file absent (graceful degradation)
- Inferred genres get `P1480=Q_PRESUMABLY` qualifier + `P887=Q2539` (machine learning) reference
- MARC-sourced genres are unchanged — no qualifier added
- `genre_str == "other"` (NOTA prediction) → skip, no P136 claim written

**To retrain:**
```bash
# Step 1 (one-time): extract training samples from 123k TSV
PYTHONPATH=src:. .venv/bin/python scripts/extract_genre_samples.py

# Step 2: train
PYTHONPATH=src:. .venv/bin/python ner/train_genre_classifier.py \
  --exclude-genres "Literature (Miscellaneous, in manuscript)" \
  --min-class-size 100 --top-k 8 --focal-gamma 2.0 \
  --freeze-layers 10 --batch-size 64 --max-length 64
```

**Expected coverage:** 69% → ~85% for P136 after training.

Tests added (3): `TestGenreClassifierIntegration` (3). Total now **181**.

### 35. MARC 500 sentence classifier for P1684 + P127/P11603 coverage (added 2026-04-20)

P1684 (inscription/colophon) and P127/P11603 (owned by/transcribed by) are under-covered because MARC 500 general notes mix colophon and provenance sentences with unrelated codicological content. A sentence-level multi-label classifier routes each sentence to the appropriate downstream processor.

**Architecture:** Single model with two independent sigmoid heads — COLOPHON (head 0) and PROVENANCE (head 1). Reuses `GenreClassificationModel` with `num_genres=2`. Trained with per-class focal loss and per-class threshold tuning.

**Files:**
- `ner/marc500_sentence_model.py` — thin re-export of GenreClassificationModel for app bundle
- `scripts/extract_marc500_sentences.py` — extraction script → `data/tsvs/marc500_sentences.tsv`
- `ner/train_marc500_classifier.py` — training script (5-fold CV, per-class thresholds)
- `converter/authority/marc500_classifier.py` — inference wrapper (`classify_sentence`, `is_colophon`, `is_provenance`)
- `ner/marc500_classifier_model.pt` — trained checkpoint (generated; not committed to git)

**Integration in `workers.py`:**
- Module-level lazy singleton `_MARC500_CLASSIFIER` with graceful degradation (model absent → skipped)
- `NerWorker.run()` routes each MARC 500 sentence: COLOPHON sentences → `ml_colophon_sentences` list; PROVENANCE sentences → provenance NER pipeline (same as MARC 561)
- `AuthorityWorker._merge_ner_into_records()` appends `ml_colophon_sentences` to `record["colophon_text"]`
- `item_builder.py` already reads `record["colophon_text"]` for P1684 — no changes needed

**To train:**
```bash
# Step 1 (one-time): extract sentences
PYTHONPATH=src:. .venv/bin/python scripts/extract_marc500_sentences.py

# Step 2: train (~1h on M4 Pro)
PYTHONPATH=src:. .venv/bin/python ner/train_marc500_classifier.py
```

**Checkpoint format:**
```python
{
    "model_state_dict": ...,
    "label2id": {"COLOPHON": 0, "PROVENANCE": 1},
    "task": "marc500_sentence_classification",
    "threshold": {"COLOPHON": float, "PROVENANCE": float},
    "num_classes": 2,
    "max_length": 64,
}
```

**Expected coverage impact:** P1684: 41% → ~55%. P127/P11603: 43%/18% → ~50%/25%. Graceful degradation when model absent.

Tests added (8): `TestMarc500ModelRealInference` (8). Total now **189**.

### 36. Centralized GUI design system in `theme.py` (added 2026-04-22)

All GUI colors, spacing, border radii, and font sizes are centralized in `src/mhm_pipeline/gui/theme.py`. No widget may hardcode a hex color, px spacing, or font-size value.

**Design tokens (module-level constants):**

| Token group | Constants | Description |
|---|---|---|
| Spacing | `SPACE_XS=4` … `SPACE_2XL=32` | Layout margins and gaps (px) |
| Border radius | `RADIUS_SM=4`, `RADIUS_MD=6`, `RADIUS_LG=8` | Corner rounding (px) |
| Font sizes | `FONT_XS=10` … `FONT_XL=16` | Text sizes (px) |

**Color accessor functions:**

| Function | Returns |
|---|---|
| `theme.ui(key)` | UI chrome colors: `text`, `subtext`, `border`, `panel_bg`, `button_bg`, `highlight`, `warning`, etc. |
| `theme.node_color(type)` | Graph node `(bg, border)` by semantic type |
| `theme.entity_color(type)` | NER entity `(bg, text)` colors |
| `theme.role_color(role)` | NER role `(bg, text)` colors |
| `theme.severity(level)` | SHACL severity `(bg, accent)` |
| `theme.confidence_bg(level)` | Authority confidence background |
| `theme.source_bg(source)` | Wikidata Preview source badge background |
| `theme.source_label(source)` | Wikidata Preview source display label |
| `theme.status_hex(status)` | Upload status color |
| `theme.field_color(tag)` | MARC field `(bg, text)` |

**Stylesheet helpers:**

| Function | Returns |
|---|---|
| `theme.button_style()` | Primary QPushButton QSS |
| `theme.success_btn_style()` | Green "continue/save" button QSS |
| `theme.warning_btn_style()` | Amber action button QSS |
| `theme.frame_style()` | Bordered QFrame QSS |
| `theme.info_banner_style()` | Info banner QFrame QSS (amber border, transparent bg) |
| `theme.warning_banner_style()` | Warning banner QFrame QSS (amber tinted bg) |
| `theme.warning_text_color()` | Foreground color string for warning content |

**App-level integration:**
`theme.apply_stylesheet(app)` is called in `app.py` after `QApplication` creation. It sets `app.setStyleSheet(theme.generate_app_stylesheet())` which covers scrollbars and splitter handles globally.

All dark/light variants are resolved at call time via `theme.is_dark()`. Call `theme.invalidate_cache()` after a palette change to refresh the cached dark-mode flag.

**Rule: NEVER hardcode** `#rrggbb` hex colors, spacing in px, border-radius in px, or font-size in px directly in `setStyleSheet()` calls or layout configs. Always reference a `theme.*` token or function.

### 37. Every QDialog must use the liquid-glass backdrop (added 2026-04-24)

Every popup, modal, sheet, or detail view in the MHM Pipeline GUI must render against the same `GraphBackdrop` particle/gradient surface the main window uses. Dialogs rendered on a flat dark fill (Qt default) break visual continuity and feel like a different app — the user explicitly flagged this on 2026-04-24 for both `ClaimsEditDialog` and `AutoApproveDialog`.

**Mandatory pattern** — two equivalent ways, pick whichever fits the dialog:

1. **Inherit `GlassDialog`** (preferred for new dialogs):

   ```python
   from mhm_pipeline.gui.widgets.glass_dialog import GlassDialog

   class MyDialog(GlassDialog):
       def __init__(self, parent=None) -> None:
           super().__init__(parent)
           layout = QVBoxLayout(self.glass_content)  # NOT self
           layout.addWidget(QLabel("Hello"))
   ```

2. **Install backdrop on a bare `QDialog`** (for existing dialogs you do not want to reparent):

   ```python
   from mhm_pipeline.gui.widgets.glass_dialog import install_glass_backdrop

   class LegacyDialog(QDialog):
       def __init__(self, parent=None) -> None:
           super().__init__(parent)
           content = install_glass_backdrop(self)  # returns translucent content widget
           layout = QVBoxLayout(content)
           ...
   ```

**Companion helpers** also live in `src/mhm_pipeline/gui/widgets/glass_dialog.py`:

| Helper | Purpose |
|---|---|
| `install_glass_backdrop(dialog)` | Insert `GraphBackdrop` + translucent content child; idempotent |
| `GlassDialog` | Base class — subclasses must use `self.glass_content`, never call `setLayout(self)` |
| `glass_table_style(theme)` | Translucent QTableView QSS so backdrop reads through |
| `glass_tab_style(theme)` | Translucent QTabWidget QSS |
| `glass_panel_style(theme)` | Liquid-glass card QSS for grouped sections (use `QFrame#glassPanel`) |

**Rule: NEVER instantiate a bare `QDialog`** without calling `install_glass_backdrop` or inheriting `GlassDialog`. This includes third-party subclasses (`QWizardPage` is exempt because `QWizard` handles the backdrop at the wizard level). The `apply_stylesheet` global rule already covers the window-gradient fallback for dialogs that slip through, but the particle/node lens only appears when the backdrop is explicitly installed.

**Tables and tabs inside dialogs** must apply `glass_table_style()` / `glass_tab_style()` so the backdrop isn't occluded by a solid fill — the default Qt painting is opaque and cancels the effect. Use `widget.viewport().setAutoFillBackground(False)` on QTableView for an extra-clean result.

### 38. Never modify Wikidata items not created by the authenticated user (added 2026-04-24)

> User directive, verbatim (2026-04-24):
> > "please ensure 100 times that we will not modify entities (pre-existing in wikidata) that are not created by me (by the user using its creds). The app only allowed to create new entities (if they're not duplicates of existing entities) and the app can modify existing entities that created by me (by the user using its creds). we should check those using wikidata api and sparkql queries"

The 2026-04-12 mass-edit incident (Geagea / Pallor / Kolja21 / Epìdosis talk threads) happened because a single-point-of-failure guard let `action=edit` go through to items the pipeline had never created. Rule 38 replaces that single guard with a **four-stage defense chain backed by three independent verification channels**.

**Four in-code gates** (all in `converter/wikidata/uploader.py`):

| # | Location | Method | Fires when |
|---|---|---|---|
| 1 | `upload_item()` entry | `_is_our_item()` | Before any work begins on an existing-QID item |
| 2 | `_build_wbi_item()` entry | `_assert_modifiable(qid, stage='_build_wbi_item')` | Even if called from a test or a new upload path that bypasses `upload_item` |
| 3 | per-statement loop | `_would_create_identity_conflict()` | Before adding P569/P570/P19/P20/P214/P8189/P213/P244/P227 to an existing item |
| 4 | immediately before `wbi_item.write(...)` | `_assert_modifiable(qid, stage='pre_write')` | Last-ditch catch — if the item's creator changed between gate 1 and here, this still blocks the write |

`_assert_modifiable` raises `UnauthorisedModificationError`, which `upload_item` catches and converts into a `skipped` result. No silent pass-through.

**Three independent verification channels** inside `_is_our_item()`:

1. **MediaWiki API — `action=query&prop=revisions&rvdir=newer&rvlimit=1&titles=<QID>`.** Authoritative "who authored the first revision" lookup.
2. **MediaWiki API — `action=query&list=usercontribs&ucuser=<me>&uctitle=<QID>&uctype=new`.** Cross-check: did the authenticated user have a **page-creation** contribution on this QID? Independent from channel 1 — different API path, different internal data store.
3. **SPARQL endpoint — `ASK WHERE { wd:<QID> ?p ?o . }`.** Confirms the item still exists and has not been deleted / redirected / blanked since we reconciled it; modifying a vanished QID targets ambiguous content.

Decision table:

| auth_user | rev.user | contribs | sparql | returns |
|:---:|:---:|:---:|:---:|:---:|
| unknown | * | * | * | **False** |
| known | unknown | * | * | **False** |
| known | other | * | * | **False** |
| known | self | **False** | * | **False** |
| known | self | None | ok | True |
| known | self | ok | **False** | **False** |
| known | self | ok | None / ok | True |

A `None` from a cross-check channel means "network/endpoint failure"; it does not unlock the gate — the primary revisions answer still must agree.

**Removed**: the previous `P1343=Q118384267` (Ktiv) marker fallback. Community-created items can legitimately cite Ktiv as a bibliographic source, which made the fallback dangerous. `_is_our_item` no longer consults any marker.

**Structural regression tests** (`tests/unit/test_safety_guards.py::TestRule38ModificationBlockedForNonOurItems`, 18 tests):

- `test_is_our_item_fails_closed_when_auth_user_unknown`
- `test_is_our_item_fails_closed_when_creator_unknown`
- `test_is_our_item_rejects_other_creator`
- `test_is_our_item_accepts_self`
- `test_is_our_item_refused_if_contribs_disagrees`
- `test_is_our_item_accepts_if_contribs_endpoint_down`
- `test_is_our_item_refused_if_sparql_says_deleted`
- `test_is_our_item_accepts_if_sparql_endpoint_down`
- `test_contribs_api_request_shape`
- `test_sparql_existence_request_shape`
- `test_assert_modifiable_raises_for_other_item`
- `test_assert_modifiable_no_op_for_new_item_creation`
- `test_upload_item_skips_other_item_at_entry`
- `test_build_wbi_item_raises_for_other_item`
- `test_upload_item_gate4_fires_if_earlier_guards_bypassed`
- `test_only_one_write_call_site_exists_in_uploader` *(structural)*
- `test_pre_write_guard_is_adjacent_to_write_call` *(structural)*
- `test_no_kludge_fallback_to_p1343_marker` *(structural)*

The three structural tests are the regression barrier: if a future refactor introduces a second `wbi_item.write(...)` call, separates the pre-write guard from the write, or re-introduces marker-based fallback, the test suite fails immediately.

**Related rules** already in force:

- Rule 23 — reconciler cross-identifier verification, uploader identity-conflict guard, pre-merge metadata conflict check, label-overwrite guard.
- Rule 24 — two-layer creator+latest-editor check for revert scripts.
- Rule 25 — moratorium gate: live uploads refused unless `MORATORIUM_LIFTED=true`.

Rule 38 is the *creation-path* counterpart of Rule 24 (revert-path). Together they close the loop: the pipeline can only CREATE new items, or MODIFY items whose first revision it authored — never anything in between.

### 39. All long-running stages use DynamicProgressBar with substep + percentage + ETA (added 2026-05-06)

Every stage panel in the GUI must use a single `DynamicProgressBar` instance from `src/mhm_pipeline/gui/widgets/dynamic_progress_bar.py` for any operation that may take more than ~3 seconds. Hand-rolled `QProgressBar`s, ad-hoc percentage labels, and per-panel "Stage X complete" footers are forbidden — they drift visually and force the user to read three different progress conventions.

**The widget surface, in two lines per panel:**

```python
self.progress = DynamicProgressBar()
connect_progress_signals(self.progress, worker, success_label="Stage 3 complete")
```

`connect_progress_signals` in the same module wires four worker signals to the bar:
| Worker signal | Bar slot | Meaning |
|---|---|---|
| `progress(int)` | `set_progress` | Tick count; the bar derives % and ETA from the last 10 ticks |
| `substep(str)` | `set_substep` | Human-readable line ("Matching VIAF: Maimonides…"); never resets ETA |
| `finished(...)` | `finish(success=True)` | Snap to 100% and show success label |
| `error(str)` | `finish(success=False)` | Switch chunk to red and show failure label |

`StageWorker` (base in `controller/workers.py`) declares `substep = pyqtSignal(str)`; subclasses emit it at clear boundaries (e.g. `AuthorityWorker` emits "Stage 3.1 — Mazal lookup (i/n)" through "Stage 3.5 — KIMA place match"). Adding a new worker means emitting `substep` at each meaningful sub-phase — never relying on raw progress ticks alone, because users can't tell from a percentage what's actually happening.

**Indeterminate mode** is debounced 100ms — a worker can briefly toggle `total=0` while computing and the bar will not flicker.

**Tests**: `tests/integration/test_pipeline_e2e.py::TestDynamicProgressBar` (3 tests) + `TestFullGuiProgressChain` (panel-level synthetic-signal smoke). Anything that adds a stage or panel must add a corresponding integration assertion that `progress.substep` emits at least once.

### 40. Stage 3 authority output — schema invariants and matcher canonical-QID preference (added 2026-05-06)

The 2026-05-06 audit on a 68-record Stage 3 output uncovered six issues. Five are now structurally enforced; the sixth (Rashi-class canonical-QID gap) is bounded by the existing uploader guards.

**Schema invariants every Stage 3 record must satisfy:**

| Field | Type | Notes |
|---|---|---|
| `entities` | `list` (may be empty) | `AuthorityWorker._merge_ner_into_records` `setdefault("entities", [])` so consumers never need `.get(..., [])`. |
| `marc_authority_matches[].source` | one of `"mazal"`, `"viaf"`, `"wikidata"`, `"cross_source"`, `"marc_only"` | **Never** the literal `"MARC"` — that was the previous placeholder. Derived at the end of `_match_marc_person_entry` from the IDs that survived the verdict. `cross_source` means 2+ identifier sources agreed. |
| `marc_authority_matches[].source_count` | `int` 0–3 | New field. Number of agreeing identifier sources (mazal + viaf + wikidata). Use this for filtering rules and confidence audits — `source` alone collapses 2 vs 3 sources into the same `"cross_source"` bucket. |
| `marc_authority_matches[].sources` | `list[str]` (only when `source_count >= 2`) | Records which identifiers agreed. |
| `kima_places.<name>` | Wikidata URI string only | `KimaMatcher` no longer falls back to a VIAF URI when the row lacks a Wikidata ID. The fallback used to leak `https://viaf.org/viaf/...` into a slot typed for Wikidata, breaking `P1071` claims downstream. |

**Matcher canonical-QID preference:**

`WikidataMatcher._mode_label_search` sorts candidates by QID number ascending before verification (lower QID = older = more canonical). Combined with the LIMIT raised from 2 to 10, this stops SPARQL's arbitrary ordering from picking pipeline-created duplicates (e.g. `Q139094451` for Rashi) over canonical entities (`Q189564`).

`_match_marc_person_entry` adds a Step 4a "canonical preference" probe: when `find_qid_by_*` returns a QID `≥ Q138_000_000` (pipeline-created range), an additional Hebrew-label search runs and the lowest QID wins. Improves canonical hit rate by ~21% (14 → 11 pipeline-range duplicates on the audit corpus).

**Step 4b VIAF backfill:**

`WikidataMatcher.find_viaf_by_qid(qid)` reads `wdt:P214` off a known QID. After NLI strict mode resolves a Mazal hit and triangulates to a Wikidata QID, this backfills the VIAF cluster ID — closing the Mazal-72%/VIAF-13% gap to Mazal-72%/VIAF-49% on the audit corpus. The follow-on VIAF cluster fetch then enriches GND/LCCN/ISNI/BnF identifiers that were previously unreachable.

**Bounded residual — Rashi-class duplicates:**

When a pipeline-created Q139xxx item is the only Wikidata entity carrying a given NLI ID (`P8189`), the matcher legitimately returns it — the canonical entity (e.g. `Q189564` for Rashi) lacks the authority claim entirely, and its Hebrew label is the abbreviated form (`רש״י`) rather than the full MARC heading (`שלמה בן יצחק`). This is a Wikidata data gap, not a matcher bug. Bounded by:

- **Rule 23** uploader identity-conflict guard (refuses to attach conflicting authority IDs).
- **Rule 25** moratorium on bulk uploads — no live operations until conditions 1–9 are met.
- **Rule 38** four-stage uploader gate — creator check + pre-write guard, structurally enforced.

When the pipeline next encounters this NLI ID it updates the existing Q139094451 rather than creating fresh duplicates. Resolving the gap entirely requires either (a) adding the full-name Hebrew alias to canonical Wikidata items, (b) building a MARC-heading → Wikidata-label dictionary, or (c) merging duplicates manually with `wbmergeitems` — all out of scope for the matcher itself.

**Tests** (added 2026-05-06): `test_wikidata_matcher.py` grew from 8 to 13 — `test_label_search_prefers_lowest_qid_when_multiple_candidates`, `test_label_search_skips_failing_lower_qid_falls_through_to_next`, `test_find_viaf_by_qid_single_value`, `test_find_viaf_by_qid_multiple_abstain`, `test_find_viaf_by_qid_caches`. Total now **504** unit + **87** integration.

### 41. Stage 2 NER schema invariants and post-filters (added 2026-05-07)

Stage 2 (`NerWorker`) emits a per-record JSON with the following invariants. Each consumer (`AuthorityWorker._merge_ner_into_records`, `WikidataItemBuilder`, the GUI editors) relies on them.

**Channels:**

| Channel | Type | Carries |
|---|---|---|
| `record["entities"]` | `list[dict]` | Real NER spans only — `source` ∈ {`person_ner`, `provenance_ner`, `contents_ner`}. Classifier outputs MUST NOT appear here. |
| `record["ml_colophon_sentences"]` | `list[str]` | MARC-500 sentences classified as colophons. Feeds P1684 (inscription). |
| `record["ml_genres"]` | `list[{"label": str, "confidence": float}]` | Genre classifier predictions for the P136 fallback. |
| `record["catalog_references"]` | `list[str]` | Catalog citations (`"מ' גסטר."`) routed out of COLLECTION; lands in P7535 notes, never in P195. |
| `record["provenance_inscriptions"]` | `list[str]` | OWNER spans longer than 80 characters (full bills of sale); land in P7535, never in P127 / P2093. |

**Entity-shape rules:**

* Every entity has `source` set to one of the three real NER sources. `colophon_ml` / `genre_ml` source values are forbidden.
* `start` and `end` are integers indexing into `record["text"]` (the global concatenation of every NER input) such that `record["text"][start:end] == entity_payload`, OR they are `None` when the entity payload was not locatable in the global text. Never `start=0, end=0` as a placeholder.
* Person entities carry `confidence` (the keyword-classifier 0.60 / 0.85 signal that Stage 3 guards key on per Rule 23) AND `model_confidence` (the real softmax probability averaged across the entity's tokens). Do not collapse the two — they have different semantics and different consumers.
* Provenance entities flagged `from_marc500: True` came from MARC 500 sentences that the sentence classifier routed through the provenance NER pipeline (rather than from MARC 561). They also carry `marc500_confidence`.

**Post-filters** (`converter/authority/ner_post_filters.py`). Applied once per record after every NER model emits its spans. Adding a new NER mistake-class to filter goes here — never in the worker inline:

* `filter_work_author_folio` — re-types folio-shaped strings (digits + Hebrew side letter) from WORK_AUTHOR to FOLIO; stamps `retyped_from`.
* `filter_collection_citations` — disambiguates COLLECTION strings via two curated frozensets of surnames. Catalog citations land in `catalog_references`; institution-eligible surnames need an institution marker (`אוסף`, `Library`, `ms`, …) in the surrounding text to stay as COLLECTION.
* `filter_owner_length` — caps OWNER text at `OWNER_MAX_LENGTH = 80` characters; longer text moves to `provenance_inscriptions`.
* `filter_person_hallucinations` — drops person spans matching topic-keyword denylists, ALL-CAPS ASCII fragments, MARC uncertainty markers, or insufficient Hebrew letter count.

Adding a new false-positive class is a one-line denylist extension followed by a unit test. The two surname allowlists in B2 and the two topic denylists in B4 are documented inline in `ner_post_filters.py` with rationale + how to add an entry.

**Tests**: `tests/unit/test_safety_guards.py::TestNerPostFilters` (17 tests), `TestNerEntitySchemaCleanliness` (4), `TestMarc500ProvenanceRouting` (6), `TestNerOffsetRebasing` (5), `TestPersonNerModelConfidence` (2), `TestRoleToLabelIncludesTranscriber` (3). The wiring tests in `test_entity_normalize.py` (4) guard the normaliser invocation. Total: **545 unit tests passing**.
