# MHM Pipeline — Mapping Hebrew Manuscripts

End-to-end automated pipeline that converts MARC 21 catalog records of Hebrew
manuscripts into Wikidata items and an HMO-aligned RDF graph. Built as a desktop
PyQt6 application for Bar-Ilan University doctoral research.

License: GPL — see `LICENSE`.

---

## What it does

```
MARC 21 record  →  NER  →  Authority match  →  RDF graph  →  SHACL validation  →  Wikidata
   (stage 0)       (1)        (2)                 (3)             (4)               (5)
```

| Stage | Input | What happens | Tools |
|---|---|---|---|
| 0. MARC parse | `.mrc` file | Field extraction, normalization | `pymarc`, `field_handlers.py` |
| 1. NER | Stage 0 JSON | Three models extract Persons + Provenance + Contents | DictaBERT-based |
| 2. Authority | Stage 0 + 1 | Match against Mazal/NLI, VIAF, KIMA places | SPARQL + local DBs |
| 3. RDF build | Stage 2 JSON | Build an HMO/CIDOC-CRM graph | `MarcToRdfMapper`, `rdflib` |
| 4. SHACL | Stage 3 TTL | Validate against `ontology/shacl-shapes.ttl` | `pyshacl` |
| 5. Wikidata | Stage 2 JSON | Create or update items via OAuth 2.0 | `WikibaseIntegrator` |

---

## Quick start

### Install

```bash
git clone https://github.com/alexandergolbergwix/pipeline.git
cd pipeline

uv venv --python 3.12
uv lock
uv sync --frozen --python 3.12
```

### Launch the GUI

```bash
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app
```

Or use the macOS app: open `dist/MHM Pipeline.app` (built via `installer/macos/`).

### Run a smoke test (no GUI)

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
```

---

## Project structure

```
pipeline/
├── src/mhm_pipeline/        # PyQt6 application
│   ├── app.py               # Entry point
│   ├── gui/                 # Main window, panels, widgets
│   ├── controller/          # Workers (one per stage), pipeline controller
│   ├── settings/            # SettingsManager (QSettings backend)
│   └── platform_/           # GPU detection, platform paths
├── converter/               # Pipeline business logic
│   ├── parser/              # MARC reading
│   ├── transformer/         # Field handlers, MARC → RDF mapper
│   ├── authority/           # Mazal, VIAF, KIMA matchers
│   ├── wikidata/            # Reconciler, item builder, uploader
│   ├── rdf/                 # Graph builder, namespaces
│   └── validation/          # SHACL runner
├── ner/                     # Three NER models + training scripts
├── ontology/                # HMO ontology + SHACL shapes
├── data/                    # KIMA TSVs + index DB, sample MARC
├── scripts/                 # Audit / revert / cleanup utilities
│   └── lib/wikidata_safety.py   # SHARED safety helpers — read this first
├── docs/                    # WIKIDATA_REVERT_SAFETY.md, DUPLICATE_PREVENTION.md
├── tests/                   # Unit + integration tests
└── .claude/                 # Slash commands and per-project agent settings
```

---

## NER models

Three models, all DictaBERT-based:

| Model | F1 | Source | Entity types |
|---|---|---|---|
| Person NER | 85.70 % | `alexgoldberg/hebrew-manuscript-joint-ner-v2` (HuggingFace) | PERSON (with role) |
| Provenance NER v2 | 95.91 % | `ner/provenance_ner_model.pt` | OWNER, DATE, COLLECTION |
| Contents NER | 99.99 % | `ner/contents_ner_model.pt` | WORK, FOLIO, WORK_AUTHOR |

The `.pt` files are too large for git — download from the project release page or
the bundled `.app`.

---

## Wikidata safety

This pipeline writes to live Wikidata. It does so through several enforcement
layers that prevent the wrong-merge / wrong-edit class of error.
**Before changing any Wikidata-touching code, read [`docs/WIKIDATA_REVERT_SAFETY.md`](docs/WIKIDATA_REVERT_SAFETY.md).**

The non-negotiable rules:

1. **Creator check** — never modify items not created by the authenticated user.
2. **Latest-editor check** — never undo when the last revision is by anyone else.
3. **Identity-conflict guard** — never write a P569/P570/P19/P20/P227/P214/P8189/P213/P244 value that conflicts with an existing one on the target item.
4. **Pre-merge conflict check** — never merge two items that disagree on identity properties.
5. **Label-overwrite guard** — never overwrite an existing label.

These are enforced by:

- `converter/wikidata/uploader.py` — `_is_our_item`, `_would_create_identity_conflict`, label guard
- `converter/wikidata/reconciler.py` — `_candidate_conflicts`
- `scripts/merge_duplicates.py` — `_has_conflict`
- `scripts/lib/wikidata_safety.py` — `is_safe_to_revert` (used by every revert script)
- `tests/unit/test_safety_guards.py` — 19 unit tests, must always pass

---

## Slash commands (Claude Code)

The repo ships project-scoped slash commands under `.claude/commands/`. Run them
as `/skill-name` inside a Claude Code session.

| Command | Purpose |
|---|---|
| `/run-tests` | Full test suite |
| `/run-e2e` | End-to-end integration tests |
| `/launch-app` | Open the GUI in a new Terminal window |
| `/check-coverage` | Measure ontology class/property coverage |
| `/reinstall-app` | Rebuild and reinstall the macOS app |
| `/update-docs` | Sync the LaTeX design docs with code reality |
| `/audit-wikidata-edits` | Scan for items I modified that I did not create |
| `/revert-wikidata-edits` | Run the revert chain (with mandatory pre-flight) |
| `/wikidata-safety-check` | Verify all safety guards before any bulk run |

---

## Authentication for Wikidata uploads

Three methods are supported. The format determines which is used:

- **Bot password**: `Username@BotName:password`
- **OAuth 2.0**: `consumer_key|consumer_secret`
- **OAuth 1.0a**: `consumer_key|consumer_secret|access_token|access_secret`

For revert scripts, an OAuth-2.0 bearer token (JWT) works. **NEVER commit a token** —
keep it in a file outside the repo (e.g. `~/.wd_token`) or pass it via stdin.
The `.gitignore` blocks `*.token`, `*token*.txt`, `*.env`, `*credentials*.json`,
and `/tmp/wd_token.txt`.

---

## Project documents

Two LaTeX documents are the source of truth for the system design and research
context. Update them whenever the architecture or stage definitions change:

- `ProjectDefinitionDocument.tex` — research context, all six stages, MARC
  field mappings, data inventory, technical requirements.
- `SystemDesignDocument.tex` — PyQt6 framework, `uv` + native installer
  distribution, application architecture, GUI design, code standards.

---

## Citing

If this pipeline contributes to your work, please cite:

```
Alexander Goldberg. "Mapping Hebrew Manuscripts: An End-to-End Pipeline for
Converting MARC 21 Records to a Validated Linked Data Knowledge Graph."
Bar-Ilan University, 2026.
```

---

## Contact

Issues and questions: file a GitHub issue, or ping
[`User:Alexander Goldberg IL`](https://www.wikidata.org/wiki/User:Alexander_Goldberg_IL)
on Wikidata for data-related concerns.
