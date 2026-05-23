# /verify-paper — Run the paper-claim verification harness

Read `paper/verification/README.md` first. Then invoke the harness with
sensible defaults.

## Quick start

Default invocation: run every **testable** claim, write evidence
artefacts to `paper/verification/results/<timestamp>/`, append per-claim
audit pages to `paper/verification/audit/`, print a summary table and
write it to `paper/verification/reports/<timestamp>-summary.md`.

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python paper/verification/verify_paper.py
```

## Variants

```bash
# Single claim
PYTHONPATH=src:. .venv/bin/python paper/verification/verify_paper.py --claim ABS-ner-person-f1

# All claims in one category
PYTHONPATH=src:. .venv/bin/python paper/verification/verify_paper.py --category coverage
PYTHONPATH=src:. .venv/bin/python paper/verification/verify_paper.py --category ner-performance
PYTHONPATH=src:. .venv/bin/python paper/verification/verify_paper.py --category safety

# Fast / static checks only (CI hook — completes in seconds)
PYTHONPATH=src:. .venv/bin/python paper/verification/verify_paper.py \
    --verifier static_grep,pytest_run

# Include manual + literature claims (for reviewer audit completeness)
PYTHONPATH=src:. .venv/bin/python paper/verification/verify_paper.py --include-manual

# Re-render audit pages from cached results without re-running verifiers
PYTHONPATH=src:. .venv/bin/python paper/verification/tools/render_audit.py
```

## Reading the output

- **`✓`** — actual matches expected; nothing to do.
- **`✗`** — actual differs in a way the paper would have to retract or
  correct. Open `paper/verification/DRIFT_LOG.md` and log it.
- **`⚠ paper out-of-date`** — code has improved past the paper number
  (e.g., paper says "232 unit tests", suite is at 248). Append a
  `DRIFT_LOG.md` entry under "Drift type 2: forward drift". Not blocking.
- **`—`** — non-app-testable (`manual` / `literature` / `structural`);
  manual review required, doesn't gate exit code.

Exit codes:

- `0` — all testable claims passed.
- `1` — at least one claim **failed** (`✗`).
- `2` — harness errored (corpus mismatch, missing model, etc.).

## When `verify_paper.py` doesn't exist yet

The harness is being built incrementally. As of 2026-04-30 the directory
holds the schema (`CLAIMS.yaml`), the protocol (`PROTOCOL.md`), the
drift log (`DRIFT_LOG.md`), and stubs for verifiers + tools. To bring
verifiers online, the next steps are:

1. Implement `paper/verification/verifiers/static_grep.py` first
   (fastest signal, no test corpus needed).
2. Then `pytest_run.py` (for safety-guard claims).
3. Then `pipeline_run.py` (slow; needs the 100-MS test corpus).
4. Then `ner_eval.py` and `genre_eval.py` (need model checkpoints).
5. Finally `wikidata_build.py`, `validator_run.py`, `manual.py`.

Each verifier exposes `run(claim, args) -> VerificationResult`. See
`HOW_TO_RUN.md` for the contract.

## Updating `CLAIMS.yaml`

Never hand-edit. When the paper text changes:

1. Spawn the Plan agent + 5 mining agents per `PROTOCOL.md` (one
   Claude session).
2. Save each agent's YAML fragment under
   `paper/verification/tools/raw_fragments/agent-<X>.yaml`.
3. Run `python paper/verification/tools/synthesize_claims.py`.
4. The synthesizer merges, deduplicates `paper_loc` entries, runs the
   11-step quality gate (see `PROTOCOL.md` §6), and writes the
   canonical `CLAIMS.yaml`.
5. Inspect the diff: `git diff paper/verification/CLAIMS.yaml`.
6. Re-run the harness; commit only if no claim regressed.

## Related rules

- **CLAUDE.md Rule 39** — full doctrine for the verification harness.
- **CLAUDE.md Rule 38** — Wikidata modification guard (testable via
  `pytest_run` against `tests/unit/test_safety_guards.py`).
- **CLAUDE.md Rule 23–28** — community-feedback fixes; many of these
  surface as `category: safety` claims in `CLAIMS.yaml`.
