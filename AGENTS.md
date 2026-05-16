# MHM Pipeline — Codex Instructions

## Before Any Planning or Implementation

Always read these two documents first:

- `ProjectDefinitionDocument.tex` — research context, six pipeline stages, MARC field mappings, data inventory, component interfaces, and technical requirements
- `SystemDesignDocument.tex` — chosen framework (PyQt6), distribution strategy (uv + native installers), application architecture, module structure, GUI design, and clean code standards

Do not propose or implement changes that contradict these documents without first flagging the conflict.

## Documentation Sync Rule

Update `SystemDesignDocument.tex` whenever:

- The application architecture changes
- The GUI design changes
- The distribution or installer strategy changes
- The module or package structure changes
- New cross-platform considerations are identified
- The clean-code toolchain changes

Update `ProjectDefinitionDocument.tex` whenever:

- A pipeline stage is added or modified
- MARC field mappings change
- A new external API or authority source is integrated
- Hardware or software requirements change
- The data inventory changes

Rule: a code change that alters the system design is not complete until the relevant `.tex` document is updated.

## Project Overview

MHM (Mapping Hebrew Manuscripts) is an end-to-end MARC-to-RDF conversion pipeline:

1. Stage 1 — MARC Input Parsing (`UnifiedReader` + `field_handlers.py`)
2. Stage 2 — NER Extraction
3. Stage 3 — Authority Resolution (Mazal/NLI, VIAF, KIMA)
4. Stage 4 — RDF Graph Construction (`MarcToRdfMapper`, HMO ontology)
5. Stage 5 — SHACL Validation (`pyshacl`)
6. Stage 6 — Wikidata Upload (`WikibaseIntegrator` + QuickStatements dry-run)

Key paths:

- GUI entry point: `src/mhm_pipeline/app.py`
- Main window: `src/mhm_pipeline/gui/main_window.py`
- Editable extraction UI: `src/mhm_pipeline/gui/widgets/extraction_editor.py`
- RDF mapper: `converter/transformer/mapper.py`
- Mazal DB: `converter/authority/mazal_index.db`
- KIMA DB: `data/kima/kima_index.db`
- Ontology: `ontology/hebrew-manuscripts.ttl`
- SHACL shapes: `ontology/shacl-shapes.ttl`

## Codex Task References

Codex does not use Claude slash commands directly. The Claude task prompts have been copied and adapted into `.codex/commands/`.

Available references:

- `.codex/commands/run-tests.md`
- `.codex/commands/run-e2e.md`
- `.codex/commands/check-coverage.md`
- `.codex/commands/launch-app.md`
- `.codex/commands/update-docs.md`
- `.codex/commands/reinstall-app.md`
- `.codex/commands/refactor-pure-functions.md`
- `.codex/commands/wikidata-safety-check.md`
- `.codex/commands/audit-wikidata-edits.md`
- `.codex/commands/revert-wikidata-edits.md`
- `.codex/commands/generate-presentation-audio.md`

Project-specific skills:

- `.codex/skills/mhm-pipeline-runtime/SKILL.md` — use for macOS/Windows
  installer rebuilds, bundled runtime/model checks, RDF/SHACL validation fixes,
  and knowledge-graph viewer category issues.

## Presentation Audio / Gemini TTS Rule

When generating text-to-speech audio for the Bar-Ilan presentation, use
`docs/presentations/generate_hebrew_speaker_audio.py` rather than creating a new
script. It extracts Hebrew speaker notes from
`docs/presentations/bar-ilan-phd-pipeline-speaker-notes-he.tex`, sends one Gemini
TTS request per slide, supports `--parallel`, and combines the slide WAV files
in the correct order.

Do not print, echo, commit, or store API keys. If `API_KEY` is not set, let the
script ask for it via hidden terminal input. Start with `--parallel 4`; reduce
to 2-3 if Google rate-limits, or increase only if the user asks. If Gemini TTS
fails for Hebrew support reasons, explain that limitation and offer the local
macOS `Carmit` fallback.

## Code Standards

- Use `pyproject.toml` as the single source of dependency and tool configuration
- All Python code must have type annotations; never use `Any`
- Format and lint with `ruff`
- Type-check with `mypy` in strict mode
- Test files use `.spec.py` under `tests/`
- Use `pathlib.Path` for file paths; avoid `os.path` string concatenation
- GPU device selection must always fall through `MPS -> CUDA -> CPU`
- Never hardcode absolute paths; use `platformdirs` for app data directories
- Prefer pure functions and predicate helpers over deeply nested conditionals

## Learned Rules

1. Ensure `README.md` exists before running `uv sync` or `uv build`
2. Never import `torch` or `transformers` at module top level; import lazily inside functions
3. Always use `uv ... --python 3.12`
4. Run `uv lock` before `uv sync --frozen` if `uv.lock` is missing
5. Use `PYTHONPATH=src:.` when running the app from the repo root
6. Read background task output files directly; avoid blocking output calls
7. Set `first_run_done=True` when testing to skip the setup wizard
8. Launch the GUI in the background when testing from a terminal
9. Never run concurrent `uv` installs into the same `.venv`
10. Reference launch command:

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app
```

11. VIAF requires `Accept: application/json`; do not rely on the old JSON schema or `/viaf.json`
12. Verify `data/kima/kima_index.db` exists before authority matching
13. macOS and Windows distributable bundles must be runtime-complete: include
    all five task models, DictaBERT/base encoder assets, Mazal DB, KIMA DB,
    ontology/SHACL files, and the runtime Python libraries. Exclude only
    training/evaluation/developer artifacts such as k-fold checkpoints, TSV
    corpora, paper files, docs, tests, and processed-data.
14. For macOS DMG rebuilds, use `installer/macos/build_app.sh`. The DMG builder
    must use the sparse-image → mounted copy → compressed UDZO workflow; the
    old one-shot `hdiutil create -srcfolder` path is unreliable for large
    multi-GB model bundles.
15. When RDF/SHACL behavior changes, validate the actual generated TTL and update
    both `ProjectDefinitionDocument.tex` and the ontology/SHACL copy if needed.
    The current clean target for `/Users/alexandergo/Desktop/test_subset/output.ttl`
    is `Validation Report: Conforms: True`.
16. In the knowledge graph viewer, `Default` is a UI fallback category, not an RDF
    class. It means the RDF node has a legitimate ontology type that is not yet
    mapped in `_TYPE_MAP` in `src/mhm_pipeline/gui/widgets/knowledge_graph_view.py`
    (for example `TransmissionWitness`, `TextTradition`, `CanonicalReference`,
    `Colophon`, `DigitalAccess`, `TextLocation`, or vocabulary nodes).

## Paper-Claim Verification (`paper/verification/`)

Self-contained verification harness for every quantitative or
architectural claim in `paper/swj-paper.tex`. A reviewer can ask
*"is 95.9 % F1 still true?"* and the answer is one command + one
updated audit page.

**Always update `paper/verification/CLAIMS.yaml` when**:

- The paper text changes — re-run the 5-agent extraction (Plan agent
  for protocol design, then 5 mining agents per `PROTOCOL.md`).
  Never hand-edit individual rows.
- A claim regresses (`✗`) — decide paper-revise or code-revise, log
  it in `paper/verification/DRIFT_LOG.md`. Do not silently change
  `expected` to make the test pass.
- The codebase moves past a paper number (`⚠ paper out-of-date`) — add
  a `DRIFT_LOG.md` entry under the appropriate "Drift type" heading.

**Read first**:

- `paper/verification/README.md` — directory tour + categories + statuses
- `paper/verification/PROTOCOL.md` — schema, decision rules, ID conventions
- `paper/verification/HOW_TO_RUN.md` — invocations and exit codes
- `paper/verification/DRIFT_LOG.md` — open questions for paper revision

**Run**:

```bash
PYTHONPATH=src:. .venv/bin/python paper/verification/verify_paper.py
```

Or use the `/verify-paper` slash command (Claude) / the equivalent in
`.codex/commands/verify-paper.md`.

**Hard rules**:

- Every entry in `CLAIMS.yaml` has `id`, `claim`, `paper_loc`,
  `category`, `verifier`, `expected`, `status`. `command` +
  `evidence_artifact` are required iff `status: testable`.
- New verifier types live in `paper/verification/verifiers/` and follow
  the standard `run(claim, args) -> VerificationResult` signature.
- The harness pins the test-corpus SHA256 in
  `paper/verification/fixtures/test_corpus_sha256.txt` and refuses to
  run on a different corpus.
- See CLAUDE.md Rule 39 for the full doctrine.

## Settings Note

Claude-specific permission files live in `.claude/settings.json` and `.claude/settings.local.json`. Their intent has been summarized for Codex in `.codex/settings-mapping.md`, but they are not directly portable.
