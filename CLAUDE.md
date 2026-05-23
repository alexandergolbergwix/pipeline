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
| `/generate-presentation-audio` | Generate Hebrew TTS audio from the Bar-Ilan speaker notes with Gemini or local macOS speech |

## Presentation Audio / Gemini TTS Rule

When asked to create text-to-speech audio for the Bar-Ilan presentation, use
`docs/presentations/generate_hebrew_speaker_audio.py` instead of writing a new
TTS script. The script extracts the Hebrew speaker notes from
`docs/presentations/bar-ilan-phd-pipeline-speaker-notes-he.tex`, keeps one
Gemini request per slide, supports parallel generation with `--parallel`, and
combines the resulting WAV files in slide order.

Never print or store API keys. Prefer the script's hidden prompt for the Gemini
API key; only use `API_KEY` if it is already set in the shell. Start Gemini TTS
with `--parallel 4` unless the user asks for a different concurrency. If Gemini
fails because Hebrew is unsupported or rate-limited, explain that limitation and
offer the local macOS `Carmit` fallback.

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

### 35. MARC 500 sentence classifier — RETIRED 2026-05-23

**Status:** REMOVED from the app, bundle, paper, slides, and docs.
**Reason:** The 2026-05-23 eval-agent run at confidence ≥ 0.90 on the
68-record test_subset corpus showed only 6 % strict / 16 % lenient
precision — 76 % of its high-confidence predictions were judged
``fail`` by Gemini. Even at lower thresholds the classifier head
fired on non-colophon sentences (title statements, ownership
inscriptions, codicological observations) with near-1.0 confidence.

**What was removed:**
- ``converter/authority/marc500_classifier.py``
- ``ner/train_marc500_classifier.py``
- ``ner/marc500_sentence_model.py``
- ``scripts/extract_marc500_sentences.py``
- ``_MARC500_CLASSIFIER`` singleton + ``_split_marc500_sentences`` +
  the entire COLOPHON/PROVENANCE routing loop in ``NerWorker.run``
- ``ml_colophon_sentences`` channel from per-record results
- The ``_merge_ner_into_records`` augmentation of ``colophon_text``
- ``Colophon ML`` toggle from the NER panel models dialog
- ``colophon_ml`` virtual source from ``ExtractionEditor``
- ``Marc500ColophonEvaluator`` + rubric + REGISTRY entry in the
  eval-agent project
- ``TestMarc500ProvenanceRouting`` + ``TestMarc500ModelRealInference``
  test classes

**What stays:**
- ``ner/marc500_classifier_model.pt`` on disk locally (gitignored —
  too large for the repo anyway). Kept in case the model is ever
  revisited; the runtime no longer loads or surfaces it.
- ``record["colophon_text"]`` populated VERBATIM from MARC (no ML
  augmentation). P1684 emission from ``item_builder.py`` is
  unchanged — it just sees a smaller set of records with usable
  colophon text.

**Impact on P1684 coverage:** drops from the 2026-04-20 claim of
~55 % back to the MARC-only baseline of ~41 %. Acceptable trade —
the 14-point lift bought ~94 % wrong inscriptions on the items it
fired on.

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
| `record["ml_genres"]` | `list[{"label": str, "confidence": float}]` | Genre classifier predictions for the P136 fallback. |
| `record["catalog_references"]` | `list[str]` | Catalog citations (`"מ' גסטר."`) routed out of COLLECTION; lands in P7535 notes, never in P195. |
| `record["provenance_inscriptions"]` | `list[str]` | OWNER spans longer than 80 characters (full bills of sale); land in P7535, never in P127 / P2093. |

NOTE: ``ml_colophon_sentences`` was removed 2026-05-23 with the MARC500
colophon classifier (Rule 35). ``record["colophon_text"]`` now comes
verbatim from MARC — no ML augmentation.

**Entity-shape rules:**

* Every entity has `source` set to one of the three real NER sources. `genre_ml` (the classifier virtual source) is allowed only in the GUI editor's synthetic-row layer, not in `entities`. The legacy `colophon_ml` virtual source was removed 2026-05-23.
* `start` and `end` are integers indexing into `record["text"]` (the global concatenation of every NER input) such that `record["text"][start:end] == entity_payload`, OR they are `None` when the entity payload was not locatable in the global text. Never `start=0, end=0` as a placeholder.
* Person entities carry `confidence` (the keyword-classifier 0.60 / 0.85 signal that Stage 3 guards key on per Rule 23) AND `model_confidence` (the real softmax probability averaged across the entity's tokens). Do not collapse the two — they have different semantics and different consumers.

**Post-filters** (`converter/authority/ner_post_filters.py`). Applied once per record after every NER model emits its spans. Adding a new NER mistake-class to filter goes here — never in the worker inline:

* `filter_work_author_folio` — re-types folio-shaped strings (digits + Hebrew side letter) from WORK_AUTHOR to FOLIO; stamps `retyped_from`.
* `filter_collection_citations` — disambiguates COLLECTION strings via two curated frozensets of surnames. Catalog citations land in `catalog_references`; institution-eligible surnames need an institution marker (`אוסף`, `Library`, `ms`, …) in the surrounding text to stay as COLLECTION.
* `filter_owner_length` — caps OWNER text at `OWNER_MAX_LENGTH = 80` characters; longer text moves to `provenance_inscriptions`.
* `filter_person_hallucinations` — drops person spans matching topic-keyword denylists, ALL-CAPS ASCII fragments, MARC uncertainty markers, or insufficient Hebrew letter count.

Adding a new false-positive class is a one-line denylist extension followed by a unit test. The two surname allowlists in B2 and the two topic denylists in B4 are documented inline in `ner_post_filters.py` with rationale + how to add an entry.

**Tests**: `tests/unit/test_safety_guards.py::TestNerPostFilters` (17 tests), `TestNerEntitySchemaCleanliness` (3), `TestNerOffsetRebasing` (5), `TestPersonNerModelConfidence` (2), `TestRoleToLabelIncludesTranscriber` (3). The wiring tests in `test_entity_normalize.py` (4) guard the normaliser invocation. ``TestMarc500ProvenanceRouting`` was removed 2026-05-23 with the classifier (Rule 35). Total: **827 unit tests passing**.

### 42. HMO-faithful Wikidata projection — Phase 1 enrichment (added 2026-05-17)

Phase 1 of the three-phase plan (see `plans/smooth-humming-feather.md`) that
lifts the Wikidata projection's HMO accessibility from ~25% to ~82%. This
phase covers the Wikidata-internal mechanisms only — no community property
proposals, no live writes (Rule 25 moratorium remains in force), and no
weakening of Rules 23, 24, 26, or 38.

| Change | File | Invariant |
|---|---|---|
| #1 Multi-P31 per manuscript (illuminated + codex + composite + palimpsest + base Q87167) | `converter/wikidata/item_builder.py:_determine_instance_type` | Returns `list[str]` most-specific-first; base `Q_MANUSCRIPT` always last; deduped |
| #2 P31 emission loop: specific QIDs get `rank="preferred"` when ≥ 2 emitted; base stays `"normal"` | `item_builder.py:build_manuscript_item` | Driven by record flags `has_decoration`, `is_multi_volume`, `is_anthology`, `is_composite`, `is_palimpsest` |
| #3 `WikidataStatement.rank ∈ {"preferred","normal","deprecated"}` plumbed through QS exporter and WBI uploader | `item_builder.py`, `quickstatements.py`, `uploader.py` | Default `"normal"`. QS v2 has no native rank syntax — exporter emits a `/* RANK: x (set via WBI; not expressible in QS v2) */` comment line preceding the statement. WBI uses `WikibaseRank` enum on every `datatypes.*` call |
| #4 `value_type ∈ {"somevalue","novalue"}` with `value=None` for known-anonymous authors | `item_builder.py`, `quickstatements.py`, `uploader.py` | QS emits literal `somevalue`/`novalue` tokens (no quotes). WBI sets `mainsnak.snaktype` and clears `datavalue`. Anonymous-author branch emits `P50 somevalue + P3831 (Q_AUTHOR_OCCUPATION) + P2093 (name string) + P5102=Q_HYPOTHESIS` on the **manuscript**; Rule 28 still blocks the person-item creation |
| #5 P2888 (exact match) to HMO IRI on every manuscript | `item_builder.py`, `hmo_crosswalk.py:_records_from_rdf`, `_load_authority_records` | One P2888 url claim per manuscript when `record["hmo_iri"]` is set. RDF path reads `str(ms_uri)`. Sidecar-fallback synthesizes from `HMO_NS_TEMPLATE` + control number with a `logger.warning` |
| #6 P31 leaves `_IDENTITY_PROPS`, joins `_MULTI_VALUE_IDENTITY_PROPS`; P569/P570/P19/P20/P227/P214/P8189/P213/P244/P21 unchanged | `uploader.py:_would_create_identity_conflict` | Rule 23 still strict for the other ten identity properties |
| #7 Genre classifier predictions carry both `P1480=Q_PRESUMABLY` and `P5102=Q_HYPOTHESIS` qualifiers; `P887` stays in reference position | `item_builder.py:_add_title_and_genres` | MARC-sourced genres carry neither qualifier. Rule 28 #3 regress: `P887` must NEVER appear in a `qualifiers=[...]` block (structural test grep-guard) |
| #8 Optional `P7416` (folios) qualifier on `P1684` (inscription) when upstream surfaces `colophon_folio` or `intervention.folio` | `item_builder.py` | Gate behind presence; never invent folio data |

**New constants** in `property_mapping.py`:
`P_NATURE_OF_STATEMENT=P5102`, `P_APPLIES_TO_PART=P518`,
`P_STATEMENT_SUPPORTED_BY=P3680`, `P_REASON_DEPRECATED_RANK=P2241`,
`P_EXACT_MATCH=P2888`, `Q_COMPOSITE_MANUSCRIPT=Q33308141`,
`Q_PALIMPSEST=Q179808`, `Q_HYPOTHESIS=Q41719`, `Q_DUBIOUS=Q104378399`,
`HMO_NS_TEMPLATE` for sidecar IRI synthesis.

**Safety invariants preserved:**

- Rule 23 identity guards (the remaining ten properties) — `TestUploaderIdentityConflict` plus the new `TestP31MultiValueGuardRelaxed::test_p214_conflict_still_blocks`.
- Rule 28 #3 — `TestP887EmittedAtClaimLevelForGenreClassifier::test_p887_never_appears_as_qualifier` (structural grep).
- Rule 31 #6 — `TestRankSlotOrderingInQs::test_qualifiers_precede_references`. Rank comment lines do NOT count as qualifiers/references — they sit on their own line preceding the statement.
- Rule 38 four-stage uploader guard — untouched.

**Audit-response defense-in-depth on the P31 multi-value relaxation (2026-05-17)**: even though P31 is now multi-value, the conflict guard refuses any P31 value outside a closed manuscript-class allowlist (`Q87167`, `Q213924`, `Q48498`, `Q33308141`, `Q179808` + a small set of book/codex synonyms). This blocks two failure modes that recurred on the talk-page incidents:

1. **Wrong class on existing item** — refusing to add `P31=Q87167` (manuscript) when the existing item already carries `P31=Q5` (human) or `P31=Q43229` (org). This is the structural barrier against the Geagea/Kolja21/Epìdosis wrong-type complaints (J9U on humans, manuscript class on band, etc.).

2. **Pipeline bug emitting wrong P31** — refusing any non-manuscript P31 reaching the uploader, even if upstream code emits one by mistake. Pipeline emission is already disciplined (`_determine_instance_type` returns from a closed set), but this is the final structural guard.

Verified on the 68-MS test corpus: manuscript items emit only `Q87167`, `Q213924`, `Q48498` — no incoherent classes. Person items (Q5) and work items (Q47461344) come from separate builder paths (`_get_or_create_person`, `_get_or_create_work`) that have never used the manuscript allowlist.

**Smoke result** on the 68-MS test corpus (`/Users/alexandergo/Desktop/test_sub2/output.ttl`): 193 items / 68 manuscripts, 98 P31 lines (multi-P31 firing on 30 manuscripts, ~44%), 68 P2888 lines (every manuscript bridged to its HMO IRI), preferred-rank comments on every multi-P31 emission. No regressions in 491 safety-guard tests.

Tests added (31): `TestMultiP31Emission` (4), `TestP31MultiValueGuardRelaxed` (7 — base 3 + 3 audit-response + 1 positive), `TestQualifierStackOnInscription` (3), `TestStatementRankSerialization` (3), `TestSomevalueNovalueSerialization` (3), `TestP2888EmitsHmoIri` (3), `TestP887EmittedAtClaimLevelForGenreClassifier` (3), `TestRankSlotOrderingInQs` (3), `TestAnonymousAuthorSomevalueEncoding` (3). Total: 460 → 491 safety-guard tests.

**Audit checklist against the recurring talk-page incidents** — none recur because:

| Incident class (talk page) | Phase 1 surface | Why it cannot recur |
|---|---|---|
| Duplicate persons from missing LCCN/GND/ISNI check (Dcflyer, MSGJ) | None — Phase 1 doesn't touch person reconciliation | Rule 26 cross-identifier check unchanged; anonymous-author somevalue branch lands on the manuscript, never as a new person item |
| Wrong mass-merges with too-broad filters (Jcb, Pallor) | None — Phase 1 doesn't merge | Rule 24 two-layer revert check + merge_duplicates conflict check unchanged |
| Two lawyers merged with different identifiers (Kolja21) | None | Rule 23 identity guard strict for the ten properties |
| Empty items with no labels/statements (Q139095809) | None — `build_manuscript_item` always sets labels and P31 | `TestEmptyItemNotExported` still active |
| Wrong language tag on P1559 (Latin labelled Hebrew) | None — Phase 1 doesn't touch P1559 | Rule 27 fixes unchanged |
| Institution mis-typed as human (Q139231608) | None — multi-P31 only emits manuscript classes from a closed set; person builder path unchanged | New `TestP31MultiValueGuardRelaxed::test_p31_refuses_manuscript_on_non_manuscript_item` + `test_p31_refuses_non_manuscript_value` |
| Low-notability persons (Q139231258) | None — anonymous-author branch goes on the manuscript, never creates a person item | `TestAnonymousAuthorSomevalueEncoding::test_rule_28_anonymous_filter_still_blocks_person_item` |
| P3959 instead of P8189 (Geagea) | None | `TestP3959NotEmittedByPipeline` structural grep passing |
| "קובץ." generic placeholder labels (Geagea) | None — Phase 1 doesn't touch labels | Rule 27 `_is_placeholder_title` filter unchanged |
| Wrong VIAF on org items (Q138937383) | None — Phase 1 doesn't touch VIAF assignment | Rule 30 nameType cross-validation unchanged |
| Misattributed Hebrew name forms (Q139230386) | None — Phase 1 doesn't touch name extraction | Existing label hygiene unchanged |
| P244/LCCN duplicates (Mcampany) | None — reconciler unchanged | Rule 26 active |
| Bulk operations during moratorium | None — Phase 1 produces richer dry-run output only | Rule 25 `_check_moratorium_for_live` unchanged; live uploads still refused without `MORATORIUM_LIFTED=true` |

### 44. HMO bridge — Phase 2 (added 2026-05-17)

Phase 2 of the three-phase plan (see `plans/smooth-humming-feather.md`).
Adds the **bridge layer** between Wikidata and the HMO scholarly graph:
P973 (described at URL) direct link, an extended projection-coverage
report, and a static SKOS crosswalk TTL for external consumers.

| Change | File | Invariant |
|---|---|---|
| #1 P973 (described at URL) emitted on every manuscript with a control number, pointing at `https://mhm-hmo.wikibase.cloud/wiki/MS_<cn>` | `converter/wikidata/item_builder.py:build_manuscript_item` | P2888 (academic permalink, switches to w3id.org once perma-id PR #6081 merges) and P973 (live direct link) coexist with distinct semantics. Both gated on `control_number` being present |
| #2 `STRATEGY_BY_LOCAL_NAME` in `projection_coverage.py` extended with 23 new entries covering the previously-unmapped HMO classes (AnthologyPosition, SubjectType, E52_Time-Span, CanonicalReference, BiblicalReference, TalmudicReference, MishnaicReference, HalachicReference, F27_Work_Creation, E56_Language, E57_Material, Decoration, CodicologicalHierarchy, HandChange, Marginalia, MarginalAddition, TextCorrection, TypeScriptType, HebrewScriptType, ModeScriptType, ConditionType, ParticipationRole, AnthologyStructure, CanonicalHierarchyType) | `converter/wikidata/projection_coverage.py` | After Phase 2 the test corpus has ≤3 `unknown` projection statuses (schema/owl-metadata classes only). 23 added strategies are tracked structurally |
| #3 `WikidataUploadWorker` already writes `wikidata_projection_coverage.json` alongside the other Stage 6 outputs (prior session work; not new in Phase 2) | `src/mhm_pipeline/controller/workers.py:2377-2378` | One JSON file per Stage 6 run; `substep("Writing HMO projection reports")` emits per Rule 39 |
| #4 Static SKOS crosswalk TTL at `ontology/hmo-wikidata-crosswalk.ttl` (143 triples, 39 SKOS-match assertions: 13 exact + 9 close + 17 related) | `ontology/hmo-wikidata-crosswalk.ttl` | Documents 4 Tier-D HMO-only classes (ParadigmBridge, PhilologicalView, TextTradition, TransmissionWitness) explicitly so external consumers understand the design choice |

**Bridge semantics**:

- `P2888 (exact match)` — academic permalink. Today `https://mhm-hmo.wikibase.cloud/wiki/MS_<cn>`; switches to `https://w3id.org/mhm/manuscript/<cn>` once perma-id/w3id.org PR #6081 merges and the redirect goes live.
- `P973 (described at URL)` — live direct link to the wikibase.cloud browse page. Stays as the direct URL even after the P2888 swap; lets a human reader click through immediately.
- Both URLs currently coincide; the two-property split prepares the projection for the permalink switch without further pipeline churn.

**Coverage report shape** (`wikidata_projection_coverage.json`):

- `classes[]` — one entry per HMO class in the input graph with `class_uri`, `class_local_name`, `hmo_node_count`, `projection_status ∈ {direct_wikidata_item, summarized_in_wikidata, hmo_or_wikibase_only, unknown}`, `wikidata_representation`, `wikidata_properties`, `projected_item_count`, `notes`.
- Top-level: `rdf_class_count`, `wikidata_item_count`, `wikidata_item_counts_by_type`, `strategy_source`, `ttl_path`.
- Verified on the 68-MS test corpus: 39 RDF classes total → 3 `direct_wikidata_item` + 36 `summarized_in_wikidata` + 4 `hmo_or_wikibase_only` after Phase 2 strategy additions, with `unknown` reduced from 19 to ≤3.

**Tests added (10)**: `TestP973ToWikibaseCloud` (3 — emission, absence without control number, coexistence with P2888); `TestProjectionCoverageReport` (4 — shape, Phase-2 strategies cover corpus, no-`unknown` invariant, worker wiring); `TestHmoWikidataCrosswalkTtl` (3 — TTL parses, F4→Q87167 exact match present, Tier-D classes documented). Total: 491 → 502 safety-guard tests.

**Safety invariants preserved**: Rules 23, 24, 25, 26, 28, 38, 42 all unchanged. P973 emission is gated identically to P2888 (control number must be present), inherits the same NLI reference block, and writes to items the Rule-38 four-stage guard already protects.

### 45. IIIF manifest generation and Wikibase Cloud writer — Phase 3 (added 2026-05-17)

Phase 3 of the three-phase plan (see `plans/smooth-humming-feather.md`). The
final piece that lifts HMO reachability from Wikidata to ~82%: every
manuscript gets a hosted IIIF manifest carrying the folio-granular HMO
structure (Codicological_Unit Ranges, ScribalIntervention/Colophon/Marginalia
AnnotationCollections, seeAlso to the canonical HMO graph). The manifests
are published to the project-owned Wikibase Cloud and referenced via
Wikidata P6108 (IIIF manifest URL).

| Change | File | Invariant |
|---|---|---|
| #1 New: `IiifManifestBuilder` builds IIIF Presentation API 3.0 manifests from the HMO graph | `converter/wikidata/iiif_manifest_builder.py` | Pure function (no I/O, no network). One Canvas per parsed folio; placeholder Canvas when no folio data. One Range per Codicological_Unit covering its Canvas span. One AnnotationPage per intervention class (Colophon / ScribalIntervention / Marginalia / MarginalAddition). `seeAlso` points at the w3id.org permalink + the HMO RDF graph IRI |
| #2 New: `IiifManifestUploader` glues builder → writer with dry-run support | `converter/wikidata/iiif_uploader.py` | Page title pattern `IIIF:MS_<cn>/manifest.json`. Edit summary includes canvas/range/annotation counts |
| #3 New: `WikibaseCloudWriter` extends `cloud_client.py` with an authenticated MediaWiki API surface | `converter/wikibase/cloud_client.py` | (a) `assert=bot` + `bot=1` on every edit, (b) idempotent — read-and-SHA-256-compare before writing, skip if identical, (c) 6-attempt exponential-backoff retry capped at 30s, (d) CSRF token cached and refreshed on `badtoken`, (e) password redacted from `__repr__`. Bot credentials injected via constructor, never hardcoded. Separate class from read-only `WikibaseCloudClient` to keep the surface unambiguous |
| #4 SettingsManager extended with `WIKIBASE_CLOUD_{URL,BOT_USERNAME,BOT_NAME,BOT_PASSWORD}` keys plus a `wikibase_cloud_credentials` property | `src/mhm_pipeline/settings/settings_manager.py` | Password lives in OS keychain via QSettings native backend (macOS Keychain / Windows Credential Manager) — same pattern as `wikidata_token`. Plaintext disk storage forbidden |
| #5 Stage 6.5 wired into `WikidataUploadWorker._write_iiif_manifests` | `src/mhm_pipeline/controller/workers.py` | (a) ALWAYS writes manifests to `iiif_manifests/MS_<cn>.json` (review surface), (b) ONLY uploads when bot credentials present AND not dry-run, (c) writes `iiif_upload_report.json` with per-manifest status, (d) emits `substep("Stage 6.5 — Generating IIIF manifests")` per Rule 39, (e) graceful failure: a failed manifest does not stop the worker |
| #6 P6108 URL precedence in `item_builder.py` | `converter/wikidata/item_builder.py:870-879` | `record["iiif_manifest_published_url"]` (Stage 6.5 result) takes precedence over `record["iiif_manifest_url"]` (MARC-derived). When upload fails or is skipped, MARC URL is the fallback |

**Trust-boundary distinction**: Rule 25 moratorium and Rule 38 four-stage
guard apply only to `wikidata.org` writes. `mhm-hmo.wikibase.cloud` is a
separate project-owned Wikibase; the `WikibaseCloudWriter` is its
authenticated surface. Even so, the writer enforces:

- `assert=bot` — refuses if the session is not bot-flagged
- idempotency — the same content cannot be re-written more than once
- reversibility — all writes are page edits (never page deletes); the
  page-history UI on wikibase.cloud is the audit/rollback path

**Tests added (50 across Phase 3 unit + integration files)**:

- `tests/unit/test_iiif_phase3.py` (22 tests):
  - `TestIiifManifestBuilder` (8): IIIF 3.0 context, canvas count from
    folio range, Range per CU, colophon annotation, intervention
    annotation, placeholder canvas fallback, seeAlso URIs, builder
    purity (no network)
  - `TestWikibaseCloudWriter` (7): password redaction, two-step login,
    CSRF token caching, idempotency on unchanged content, `assert=bot`
    on every edit POST, retry on transient 503, structural credential
    secrecy
  - `TestIiifManifestUploader` (4): dry-run short-circuit, routing to
    writer, edit-summary shape, raw-URL pattern
  - `TestP6108Precedence` (3): published URL beats MARC, MARC URL used
    when published absent, no P6108 when neither present

- `tests/integration/test_rules_end_to_end.py` (28 tests, all rules):
  - Rule 23: manuscript P31s in allowlist, no manuscript P31 on persons,
    `_IDENTITY_PROPS` retains the ten strict properties
  - Rule 25: moratorium refuses live, lift flag allows live, test-mode
    bypass
  - Rule 28: no anonymous person items, no role-descriptor person items,
    P50 somevalue carries P3831 + P2093 when present
  - Rule 31: no empty CREATE blocks, no MARC filenames in P7535,
    qualifiers precede references on every statement line
  - Rule 38: `_is_our_item` called at upload entry, `_assert_modifiable`
    called at ≥2 sites, identity-conflict guard strict for ten props
  - Rule 39: `StageWorker.substep` exists, `WikidataUploadWorker` emits
    ≥4 substeps including Stage 6.5
  - Rule 42: multi-P31 firing on corpus, P2888 uses project URI, rank
    comments present for non-normal ranks, no synthetic HMO IRI in QS
  - Rule 44: P973 ≥ manuscript count, projection coverage has ≤3
    unknowns, crosswalk TTL has ≥30 SKOS matches
  - Rule 45: one IIIF manifest per manuscript, seeAlso has w3id.org +
    HMO TTL, writer redacts password, P6108 precedence
  - Cross-cutting: `build_items_from_hmo_ttl` + IIIF builder make zero
    HTTP calls

Total: 502 → 552 tests (28 integration + 22 unit + 0 regressions).

**Operator workflow** (once bot credentials are configured):

1. Visit `https://mhm-hmo.wikibase.cloud/wiki/Special:BotPasswords`
2. Create a bot password with the `edit` grant (only — no creation needed
   for IIIF pages because the IIIF namespace uses standard editing)
3. Open MHM Pipeline → Settings → enter the bot username, bot name, and
   bot password (stored in OS keychain)
4. Run Stage 6 with dry-run UNCHECKED → manifests upload and Wikidata
   QS export carries the live `?action=raw&ctype=application/json` URLs
   in P6108

**Rollback procedure**:

- For an individual manifest: visit the page history on
  `mhm-hmo.wikibase.cloud`, click "Restore this version" on the prior
  good revision.
- For a Wikidata P6108 claim pointing at a now-removed manifest: the
  next Stage 6 dry-run produces a corrected QS file (or falls back to
  the MARC-derived URL when `iiif_manifest_published_url` is absent).

#### P6108 coexistence (added 2026-05-18)

The original Rule 45 specification treated the published-on-wikibase.cloud
manifest URL as a *replacement* for NLI's MARC-derived IIIF manifest URL:
`item_builder.py` used a single `or` precedence and emitted only one
P6108 statement. Audit on 2026-05-18 found this was wrong:

- **NLI's manifest** (`iiif_manifest_url`, from MARC 856) is image-rich:
  it hosts the actual high-resolution Canvas image bodies.
- **Our manifest** (`iiif_manifest_published_url`, on
  `mhm-hmo.wikibase.cloud`) is metadata-rich but image-poor: its Canvases
  are placeholders that carry the HMO scholarly overlay
  (Codicological_Unit Ranges, ScribalIntervention / Colophon / Marginalia
  AnnotationCollections, seeAlso to the HMO graph node).

The two manifests have **different responsibilities** — images vs.
scholarly overlay — and a Wikidata consumer that clicks P6108 must be
able to reach both. The fix replaces the single-value precedence with a
multi-value emission pattern:

| URLs present | P6108 statements emitted | Ranks |
|---|---|---|
| NLI only | one P6108 → NLI URL | `normal` |
| ours only | one P6108 → our URL | `normal` |
| BOTH | two P6108 statements | NLI = `preferred`, ours = `normal` |

The `preferred` rank on NLI's manifest ensures image-only IIIF consumers
(typical viewers) pick the right one by default; our overlay manifest
stays at `normal` rank and remains discoverable for consumers that
follow every P6108 value. The QuickStatements exporter at
`converter/wikidata/quickstatements.py:165-171` already emits a
`/* RANK: preferred */` comment line for non-normal ranks (Rule 42 /
Phase 1 mechanism); no exporter changes were required.

To complete the coupling, generated manifests now declare a IIIF 3.0
`partOf` reference pointing at NLI's manifest URL when that URL is
present on the manuscript's `hm:DigitalAccess` node. This signals to
IIIF consumers that ours is a **companion overlay** of NLI's manifest,
not an independent or competing one. The traversal lives in
`IiifManifestBuilder._nli_iiif_url(ms_uri)` which walks
`manuscript hm:has_digital_access → da hm:iiif_manifest_url`; absent NLI
URL → no `partOf` key emitted (legacy shape preserved).

Files touched:

- `converter/wikidata/item_builder.py` ~870-895 — single `or` precedence
  replaced with the multi-value pattern documented above.
- `converter/wikidata/iiif_manifest_builder.py` — new `_nli_iiif_url`
  method; `build_for_manuscript` emits `partOf` when the NLI URL is
  reachable in the source graph.

**Tests added (3 to existing `TestP6108Precedence`)**:

- `test_both_urls_present_emits_two_p6108_statements` — when both URLs
  are present, exactly two P6108 statements are emitted with the right
  rank assignment and `value_type="url"`.
- `test_nli_url_gets_preferred_rank` — NLI alone gets `normal` rank
  (single-value case); both present → NLI gets `preferred`.
- `test_iiif_manifest_emits_partof_when_nli_url_in_graph` — generated
  manifest declares `partOf` pointing at the NLI URL; absent NLI URL →
  no `partOf` key on the manifest.

Total `TestP6108Precedence` grows from 3 → 6 tests. The previously
existing test `test_published_url_takes_precedence` was updated to
match the new contract (asserts both statements, not single
precedence-winner); the corresponding integration test
`test_p6108_precedence_in_item_builder` in
`tests/integration/test_rules_end_to_end.py` was updated the same way.

### 46. Smart Hebrew→Latin transliteration (added 2026-05-18)

Before Rule 46, work items whose only title was Hebrew received the
synthetic English label `"work from Hebrew manuscript <shelfmark>"` —
visually ugly, useless for search, and called out repeatedly in the
Wikidata talk threads. Rule 46 replaces that single fallback with a
three-tier smart waterfall implemented in
`converter/wikidata/hebrew_translit.py` and consumed by
`converter/wikidata/item_builder.py` at two call sites: the work-label
emit in `_get_or_create_work` (the original motivation) and the P2093
fallback in `_add_person_claims` (Hebrew-only person names get the
transliterated Latin form as a P1810 / "object named as" qualifier so
curators searching Wikidata in English can find the fallback).

**Waterfall**:

| Tier | Source | Behaviour |
|---|---|---|
| 1 | Curated override dict in `hebrew_translit.py` | ~33 entries: canonical Wikipedia-style English labels for famous figures (Maimonides, Rashi, Nahmanides, Abraham ibn Ezra, Yehuda HaLevi, Baal Shem Tov, Isaac Luria, Joseph Karo, …) plus common manuscript references (Torah, Talmud, Mishnah, Zohar, Siddur, Haggadah, …). Both full patronymic and acronym variants point at the same canonical label. Strips ISBD terminators and collapses internal whitespace before lookup. |
| 2 | NLI ALA-LC romanization on the source record | Probes the record dict for `title_romanized`, `marc_880`, `marc_246`, `name_romanized`, etc. Librarian-quality ALA-LC romanization always beats machine transliteration. Pure-Hebrew "romanizations" (importer error) are rejected. List and dict-of-langs shapes are unwrapped. |
| 3 | Deterministic ALA-LC-inspired character map | 27-entry consonantal Hebrew→Latin table (אבגדהוזחטיכךלמםנןסעפףצץקרשת). Final-form letters mapped distinctly (ך→kh, ם→m, ן→n, ף→f, ץ→ts). Silent letters (א, ע) drop to empty. ש→sh, צ→ts. Nikud is stripped before mapping. First alphabetic char gets capitalised. Pass-through of non-Hebrew characters preserves mixed-script embeds. |

**Refusals** — the function returns `None` (so the caller omits the `en`
slot rather than upload a synthetic value) when input is empty,
whitespace-only, a non-string, or Latin-only. The synthetic
`"work from Hebrew manuscript ..."` placeholder is structurally gone
from `item_builder.py` and the source check is pinned by
`tests/unit/test_safety_guards.py::TestWorkItemEnglishLabel::test_shelfmark_fallback_in_source`.

**No new runtime dependencies**. `phonikud` was evaluated and rejected:
it produces IPA phonemes (`ʔ`, `ʃ`, `χ`) rather than Latin letters, so
adopting it would still leave us writing an IPA→Latin mapping; and its
`requires_python = <3.13` would couple our floor to its ceiling.
Wikidata reverse-lookup (search by `rdfs:label@he`, read `rdfs:label@en`)
is intentionally out of scope — Rule 46 requires offline behaviour from
the upload hot path. Both are documented in the module docstring as
possible future extensions.

**Tests added (36) in `tests/unit/test_hebrew_translit.py`**:

- `TestCuratedOverrides` (9): canonical names hit, gershayim variants
  collapse (ASCII `"` ↔ Unicode `״`), ISBD terminators stripped,
  internal whitespace collapsed.
- `TestNliRomanizationRead` (8): every documented romanization key,
  list shape, dict-of-langs shape, pure-Hebrew rejection, Tier 1 beats
  Tier 2.
- `TestAlgorithmicTransliteration` (7): final letters, silent letters,
  sh / ts digraphs, nikud stripped, first letter capitalised.
- `TestEdgeCases` (6): empty / whitespace / non-string / Latin-only all
  return `None`; mixed Hebrew+Latin preserves the Latin embed.
- `TestKeyNormalisation` (3): override-key normalisation contract.
- `TestWaterfallIntegration` (4): tier order Tier 1 > Tier 2 > Tier 3,
  Tier 3 never returns `None` for Hebrew input, the synthetic
  placeholder is never emitted by any path.

Total grows from 502 → 538 unit tests.

**Touched files**:

- NEW `converter/wikidata/hebrew_translit.py` — the module.
- MOD `converter/wikidata/item_builder.py` — work-label fallback now
  routes through `english_label_for_hebrew(title, source_record)`; the
  P2093 person fallback adds a P1810 qualifier with the transliterated
  Latin form when the name is Hebrew-only.
- NEW `tests/unit/test_hebrew_translit.py` — 36 tests.
- MOD `tests/unit/test_safety_guards.py::TestWorkItemEnglishLabel::test_shelfmark_fallback_in_source` — was pinning the dead synthetic
  fallback; now pins the new wired-up `english_label_for_hebrew` call
  AND asserts the old `"work from Hebrew manuscript"` string is gone
  from the source.

#### Upgrade to a 5-tier waterfall + cross-platform parity (added 2026-05-18, same day)

After the initial 3-tier ship, two more tiers were inserted between
Tier 2 (NLI MARC) and Tier 3 (consonantal ALA-LC):

| New tier | Source | Behaviour |
|---|---|---|
| **3 (new)** | Wikidata SPARQL reverse-lookup | When Wikidata already has an `en` label for the exact Hebrew string, prefer it (community consensus form, e.g. "Maimonides" rather than algorithmic). Implemented in `converter/wikidata/wikidata_reverse_lookup.py`. Cached on disk at `platformdirs.user_cache_dir("MHMPipeline")/wikidata_reverse_label_cache.json` (positive 30 days, negative 24 h). Honours `MHM_NO_NETWORK` env var. Never raises — any failure returns `None`. |
| **4 (new)** | DICTA Nakdan vowel adder + vowel-aware ALA-LC | Adds Hebrew vowel marks (nikud) via the `dicta-il/dictabert-large-char-menaked` HF model (~1.1 GB), then applies a deterministic vowel-aware ALA-LC table that produces `Rikardo` rather than the consonantal `Rikrdo`. Lazy-loaded with MPS→CUDA→CPU device fallthrough; graceful `None` return when torch/transformers/model files are absent. Implemented in `converter/wikidata/nakdan_translit.py`. |

Tier numbering after the upgrade: 1 override · 2 NLI MARC · 3 Wikidata SPARQL · 4 Nakdan + vowel-aware ALA-LC · 5 consonantal ALA-LC fallback. The orchestrator in `english_label_for_hebrew` catches any exception from Tier 3/4 modules and falls through — a corrupted cache, broken model file, or transient SPARQL outage must never break the upload pipeline.

**Cross-platform installer parity** (the user explicitly flagged 2026-05-18): the macOS .app and the Windows installer MUST bundle the exact same set of ML model directories so the app behaves identically on both platforms.

- `installer/macos/build_app.sh` line 46: `NAKDAN_MODEL_ID="models--dicta-il--dictabert-large-char-menaked"`, copies the HF snapshot to `models/nakdan/` inside the .app.
- `installer/windows/MHMPipeline.spec` line 92: `datas += _opt_dir('models/nakdan', 'models/nakdan')`.
- `scripts/package_for_windows_build.sh` line 82: `NAKDAN_SRC="${HF_CACHE}/models--dicta-il--dictabert-large-char-menaked"`, flattens it into `models/nakdan/`.

Nakdan absence is **graceful on both platforms** (warning, not hard error). With Nakdan present, Tier 4 unlocks vowel precision; without it, the same input falls through to Tier 5's consonantal output. Other waterfall tiers don't depend on the model so Tier 1/2/3/5 all keep working.

**Cross-platform parity tests** in `tests/integration/test_rules_end_to_end.py::TestMacOsWindowsInstallerParity` (7 tests) lock in the bundle parity: every required HF model directory and every `.pt` classifier checkpoint must be referenced by BOTH installers; the Nakdan HF model ID must match verbatim; absence is graceful on both sides.

**Integration tests** in `tests/integration/test_hebrew_translit_e2e.py` (18) + `tests/integration/test_rules_end_to_end.py::TestRule46TransliterationEndToEnd` (7) cover: tier priority (each tier wins over lower tiers), Stage 6 pipeline integration (legacy synthetic label structurally absent), cache hit/miss + format, graceful degradation (fully offline + no model still produces output), pathological inputs (no raise), SPARQL/Nakdan failure-fall-through, and dissertation examples (`"משה בן מימון" → "Maimonides"`, `"רש״י" → "Rashi"`, `"ריקרדו"` produces Latin with the r/k/d skeleton).

Total tests across Rule 46 (initial + upgrade): 36 unit `test_hebrew_translit.py` + 10 unit `test_wikidata_reverse_lookup.py` + 26 unit `test_nakdan_translit.py` + 18 integration `test_hebrew_translit_e2e.py` + 7 integration (`TestRule46TransliterationEndToEnd`) + 7 integration (`TestMacOsWindowsInstallerParity`) = **104 tests**.

**Touched files (post-upgrade)**:

- NEW `converter/wikidata/wikidata_reverse_lookup.py` — Tier 3 SPARQL helper with on-disk cache + `MHM_NO_NETWORK` env-var honour
- NEW `converter/wikidata/nakdan_translit.py` — Tier 4 Nakdan loader + vowel-aware ALA-LC + graceful-degradation orchestrator
- MOD `converter/wikidata/hebrew_translit.py` — `english_label_for_hebrew` extended to 5-tier waterfall with try/except guards around Tiers 3/4 imports
- MOD `installer/macos/build_app.sh` — bundles `models/nakdan/`
- MOD `installer/windows/MHMPipeline.spec` — bundles `models/nakdan/`
- MOD `scripts/package_for_windows_build.sh` — flattens the Nakdan HF snapshot
- NEW `tests/unit/test_wikidata_reverse_lookup.py` — 10 tests (cache hit/miss, TTL expiry, SPARQL shape, network-disabled, negative cache)
- NEW `tests/unit/test_nakdan_translit.py` — 26 tests (vowel-aware ALA-LC determinism + graceful-degradation paths for the lazy loader)
- NEW `tests/integration/test_hebrew_translit_e2e.py` — 18 tests covering tier priority, pipeline integration, cache, degradation, dissertation examples
- MOD `tests/integration/test_rules_end_to_end.py` — added `TestRule46TransliterationEndToEnd` (7) + `TestMacOsWindowsInstallerParity` (7); `TestNoNetworkCallsDuringBuild::test_build_and_iiif_offline` now sets `MHM_NO_NETWORK=true` so the SPARQL tier doesn't fire

#### Empirical Tier 4 / Tier 5 disablement for work labels (added 2026-05-18, third revision same day)

Live testing of the bundled DICTA Nakdan model on real Stage 6 outputs surfaced two hard problems:

1. **Tier 4 (DICTA Nakdan) is broken under `transformers==5.3.0`** — the model's custom `predict()` method (shipped via `trust_remote_code=True` from `BertForDiacritization.py`) outputs cumulative-prefix garbage on every input, including DICTA's own documented prose example. Bypassing `predict()` and decoding the logits directly returned zero nikud predictions across the test set. Diagnosis: the model was trained on modern Hebrew prose and is out-of-distribution for the medieval Hebrew names + short manuscript titles the MHM corpus actually contains.

2. **Tier 5 (consonantal ALA-LC) is too ugly for public Wikidata items.** On the user's canonical example "תקנות רבנו גרשם מאור הגולה", Tier 5 emitted `"Tknot rvno grshm mor hgolh"` — readable as a consonant skeleton but unfit as a public English label. The user flagged this on 2026-05-18.

**Resolution for work labels** in `converter/wikidata/item_builder.py:_get_or_create_work`:

- Call the waterfall with `allow_nakdan=False, allow_algorithmic=False` so only Tiers 1, 2, 3 contribute.
- When the waterfall returns `None`, fall back to the manuscript's NLI control number as the en label, prefixed `"NLI {control_number}"`. This is deliberately not a transliteration; it's a stable, unambiguous identifier that a Wikidata consumer can resolve to the canonical record.

Result on the user's example: `en = "NLI 990000827290205171"` instead of `"Tknot rvno grshm mor hgolh"`.

**Future work**: web research on 2026-05-18 surfaced [TaatikNet](https://github.com/morrisalp/taatiknet) — a ByT5-small seq2seq model from Morris Alper trained on ~15k Hebrew↔Latin Wiktionary pairs. It is bidirectional Hebrew↔Latin transliteration, far more appropriate for our use case than DICTA Nakdan (which is a nikud-adder, not a transliterator). A future Rule 46 revision can fine-tune TaatikNet on our ~1,100 paired Hebrew/Latin labels extracted from MARC 880-linked fields in the 123k NLI corpus. Until that ships, the NLI-identifier fallback is the public contract.

**Tier 5 still fires for person P2093 fallbacks.** Person names like "Avrhm bn zr" remain readable as Abraham ben Ezra at a squint; the en-label slot on a person item is more forgiving than the work-label slot. Only work labels were singled out as problematic.

#### Fourth iteration — TaatikNet engine swap + compound en label (added 2026-05-18, same day)

Nakdan was empirically broken AND out-of-distribution for medieval Hebrew. Replaced as the Tier 4 engine with **TaatikNet** (`malper/taatiknet`) — a ByT5-small seq2seq Hebrew↔Latin transliterator trained on ~15k Wiktionary pairs by Morris Alper. Verified on the user's canonical example:

| Stage | Output for `"תקנות רבנו גרשם מאור הגולה"` |
|---|---|
| First ship (Tier 5 consonantal) | `"Tknot rvno grshm mor hgolh"` ← ugly |
| Third iteration (NLI fallback only) | `"NLI 990000827290205171"` ← unambiguous but bare |
| **Fourth iteration (TaatikNet + NLI)** | **`"Takanut rivno gereshem meor hagola (NLI 990000827290205171)"`** ← what the user asked for |

**Per-word strategy**: TaatikNet was trained on individual Wiktionary entries, so feeding it a multi-word phrase collapses the output. The wrapper at `converter/wikidata/taatiknet_translit.py:transliterate_hebrew_to_latin` splits on whitespace, transliterates each word, then re-joins. Empirically this produces clean output for both single names and multi-word titles.

**Stress-mark stripping**: TaatikNet emits Spanish-style acute accents on stressed vowels (`Takanút rivnó`). The wrapper applies `unicodedata.normalize("NFD")` + filter on combining marks to drop the accents (`Takanut rivno`) for clean public-facing Wikidata labels. A `preserve_stress_marks=True` flag is available for callers who want the academic stress notation.

**Compound work-label format** (`_get_or_create_work` in `item_builder.py`):
- `en = f"{translit} (NLI {control_number})"` when both are available
- `en = translit` when only translit succeeds (no control number in record)
- `en = f"NLI {control_number}"` when TaatikNet returns None (Tier 4 graceful degradation)
- `en` slot is omitted entirely when neither is available (Wikidata items can be label-less in `en` for non-Latin-origin works)

**Bundle changes**:
- macOS `installer/macos/build_app.sh`: `NAKDAN_MODEL_ID` → `TAATIKNET_MODEL_ID = "models--malper--taatiknet"`; copies to `models/taatiknet/` inside the .app (~1.1 GB, same size as Nakdan was).
- Windows `installer/windows/MHMPipeline.spec`: `models/nakdan` → `models/taatiknet`.
- `scripts/package_for_windows_build.sh`: same swap.
- Cross-platform parity tests in `TestMacOsWindowsInstallerParity` updated to require `taatiknet` (not `nakdan`) on both sides.

**Dead-code cleanup**: `converter/wikidata/nakdan_translit.py` and `tests/unit/test_nakdan_translit.py` are **deleted**. The waterfall and tests now reference `converter/wikidata/taatiknet_translit.py` exclusively. The `allow_nakdan` flag in `english_label_for_hebrew` was kept as a name for backwards compatibility — it gates Tier 4 (now TaatikNet).

**Verification**: 60 Rule 46 integration tests pass (waterfall priority, Stage 6 pipeline integration, cache, graceful degradation, dissertation examples). The smoke test against the user's flagged work item now produces `Takanut rivno gereshem meor hagola (NLI 990000827290205171)` exactly.

### 47. Work items must be CREATE'd in QuickStatements export (added 2026-05-18)

QuickStatements v2 has no implicit item creation: every new entity must
appear as its own `CREATE` block before any other line can reference it
via `LAST`. Manuscript items reference their work items by P1574
(exemplar of); if the works are not CREATE'd first, the manuscript line
emits a stub string value (`__LOCAL:work:...`) that QuickStatements
treats as a literal string rather than an item identifier.

**Bug** (pre-2026-05-18): `QuickStatementsExporter.export()` in
`converter/wikidata/quickstatements.py` iterated only `persons` and
`manuscripts`. Work items built by `_get_or_create_work` carried full
labels (including the Rule 46 compound form
`Takanut rivno gereshem meor hagola (NLI 990001801390205171)`) but were
silently dropped from the QS output. The user flagged this on
2026-05-18 after a Stage 6 run on the test corpus.

**Fix**: the export method now partitions and iterates three groups in
strict dependency order:

```python
persons = [i for i in items if i.entity_type == "person" and not i.existing_qid]
works = [i for i in items if i.entity_type == "work" and not i.existing_qid]
manuscripts = [i for i in items if i.entity_type == "manuscript"]
```

Ship order: **persons first** (work items reference them via P50
author); **works second** (manuscript items reference them via P1574);
**manuscripts last**. The exporter's section-comment banners encode
the same dependency chain.

**Invariant**: any future `entity_type` introduced into `WikidataItem`
must be added to this partition explicitly. The `entity_type in {…}`
filter is the regression barrier — anything outside the explicit set is
silently dropped. Treat the partition as a closed set; add a new branch
when adding a new type.

**Bundle impact**: macOS DMG (`MHMPipeline-0.1.0.dmg`, 5.9 GB) and
Windows source zip (`mhm-pipeline-source.zip`, 5.6 GB) regenerated
2026-05-18; both ship the quickstatements.py fix.

#### Latin-only invariant on en labels (added 2026-05-18, same day)

After verifying the QS export contained the 111 work CREATE blocks, a
second audit found three label-quality issues stemming from TaatikNet
echoing Hebrew on out-of-distribution input:

| Symptom | Cause |
|---|---|
| Pure Hebrew echo: `"גוק אוסק (NLI ...)"` | TaatikNet returns the input unchanged on short / OOD tokens; per-word builder appended the raw Hebrew |
| Mixed Hebrew+Latin: `"מא ktovim omet ... (NLI ...)"` | One of the per-word calls echoed Hebrew; the joiner kept it |
| Partial transliterations: `"1) Daf e (NLI ...)"` | (Separate Stage-2 NER quality issue — Latin-only but garbage title) |

**Invariant** (now enforced): the en slot on a work or person Wikidata
item must NEVER contain Hebrew script. If TaatikNet's output contains
any Hebrew character, the whole transliteration is rejected and the
caller falls back to `"NLI <control_number>"` alone.

**Fix** (two-layer defense in
`converter/wikidata/taatiknet_translit.py` +
`converter/wikidata/hebrew_translit.py`):

1. **Per-word guard** in `_translit_single_word`: after decoding the
   model output, `_has_hebrew(decoded)` rejects the result as a failure
   (returns `None`). The per-word caller already drops `None` words,
   but combined with #2 below this means a single Hebrew-echo word
   collapses the whole phrase.
2. **All-or-nothing phrase contract** in
   `transliterate_hebrew_to_latin`: when `failures > 0` (any Hebrew
   word didn't transliterate cleanly), the whole phrase returns
   `None`. A final `_has_hebrew(out)` defensive check catches any
   regression where Hebrew sneaks through despite zero per-word
   failures.
3. **Waterfall defensive guard** in `english_label_for_hebrew`: after
   Tier 4 returns, `_has_hebrew(ml_label)` rejects any Hebrew-leaking
   output. This is belt-and-suspenders insurance against future engine
   swaps regressing on the Latin-only contract.

**Tests added (4) in `tests/unit/test_hebrew_translit.py`** —
`TestTier4LatinOnlyOutput`: pure echo → None; mixed echo → None; clean
output unchanged; defensive waterfall guard catches engine regression.
Total: 36 → 40 unit tests.

**Note on `"1) Daf e"`-class entries**: these are Latin-only and pass
the new guard. They originate from Stage-2 contents NER extracting the
folio marker `"דף"` ("Daf"/"page") and the MARC 505 list numbering as
part of the work title. Cleaning that is a Stage-2 NER concern, out of
scope for Rule 47.

### 48. Eval-agent lives outside this repo (added 2026-05-22)

Model evaluation has been extracted from this pipeline into a separate
standalone project at `/Users/alexandergo/Documents/Doctorat/eval-agent`.
The agent reads pipeline JSON outputs from disk and uses Gemini 3.x as
judge to score every NER entity + classifier prediction against the
original MARC record.

**Why a separate project**: the previous in-tree script
(`scripts/evaluate_models_with_gemini.py`, ~838 lines) accumulated
enough surface area — Gemini client, sliding-window rate limiter,
verdict cache, per-model prompt builders, structured-output enforcement
— that keeping it next to pipeline code muddled the boundary between
"the system" and "the tool that measures the system." Following
Anthropic's [effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
pattern, the eval-agent is its own project with its own git history,
its own dependencies, its own `CLAUDE.md` operating manual, and
**file-coupled** (not Python-coupled) to this pipeline.

**Hard invariants this pipeline must respect:**

1. **No Python imports from `eval-agent/`** in this repo. The pipeline
   writes JSON outputs to disk (`marc_extracted.json`, `ner_results.json`);
   the eval-agent reads them. That is the only contract.
2. **No write-backs from eval-agent** into this repo. The eval-agent
   never modifies pipeline source or data.
3. **The in-tree script `scripts/evaluate_models_with_gemini.py` is
   preserved for now** as the canonical reference for the lifted
   logic, but it is **deprecated** — any new evaluation work goes into
   the standalone eval-agent. Once the eval-agent is at feature parity,
   the in-tree script can be removed in a follow-up.
4. **Pipeline CI / paper-claim verifier (`paper/verification/`) MUST
   NOT depend on eval-agent.** Cross-tool reporting is fine via files
   (the eval-agent emits markdown reports a human can read; the
   verifier reads claims YAML). No call chain in either direction.

**How to invoke from this repo's context**: see
`.claude/commands/eval-agent.md`, `.codex/commands/eval-agent.md`, and
`.codex/skills/eval-agent/SKILL.md`. Canonical command:

```bash
cd /Users/alexandergo/Documents/Doctorat/eval-agent
bash init.sh                                                            # one-shot bootstrap
export GEMINI_API_KEY="..."
make run PIPELINE_OUTPUT=/Users/alexandergo/Documents/Doctorat/pipeline/eval/work
```

The agent emits a per-run folder under `eval-agent/state/runs/<ts>/`
with `results.jsonl` (per-candidate verdicts), `summary.csv` (per-model
precision metrics), and `report.md` (human-readable summary).

**Scope (eval-agent MVP)**: the 4 Stage-2 trained models (Joint
Person NER, Provenance NER, Contents NER, Genre classifier). The
MARC500 Colophon classifier was retired 2026-05-23 (see Rule 35).
Stage 3 (authority resolution), Stage 4 (RDF mapping), Stage 5
(SHACL violation triage), Stage 6 (Wikidata upload diff) are on
the eval-agent roadmap but not in MVP.
