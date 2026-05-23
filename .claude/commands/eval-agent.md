Run the standalone eval-agent against a pipeline output folder.

The eval-agent lives at `/Users/alexandergo/Documents/Doctorat/eval-agent` (separate project from this pipeline repo). It uses Gemini 3.x as judge to score every prediction (NER entities + classifier outputs) against the original MARC record and emits per-model precision reports.

## When to use this command

- After running Stage 1 + Stage 2 of the pipeline on any TSV input — you want to know how well the 5 trained models did
- Before publishing claims about model accuracy (paper, demo, supervisor handoff)
- To detect regressions when re-training one of the trained models
- To debug WHY certain entities are auto-approved when they shouldn't be
- To compare two runs and surface precision regressions per (evaluator, sub_type)

## Canonical invocation

The canonical input is the pipeline's `eval/work/` directory (produced by Stage 1 + Stage 2 on `data/tsvs/test_subset.tsv`).

```bash
# 0. Confirm pipeline output exists
ls /Users/alexandergo/Documents/Doctorat/pipeline/eval/work/{marc_extracted,ner_results}.json

# 1. Bootstrap the eval-agent (idempotent; one-shot on first session)
cd /Users/alexandergo/Documents/Doctorat/eval-agent
bash init.sh

# 2. Verify the cache + schemas BEFORE starting new work (Phase 2 gate)
make verify

# 3. Run the evaluation (API key required)
export GEMINI_API_KEY="..."
make run PIPELINE_OUTPUT=/Users/alexandergo/Documents/Doctorat/pipeline/eval/work

# 4. Read the latest report
ls -t state/runs/ | head -1 | xargs -I{} cat state/runs/{}/report.md
```

## Default judge + budget

| Parameter | Default | Where to override |
|---|---|---|
| Judge model | `gemini-3.5-flash` | `--judge <id>` or `config/default.yaml` |
| RPM cap | `60` | `--rpm <n>` or `config/default.yaml` |
| Parallel workers | `4` | `--parallel <n>` or `config/default.yaml` |
| Confidence threshold | `0.85` | `--threshold <f>` or `config/default.yaml` |
| Evaluators | all 5 | `--evaluators person_ner,provenance_ner,...` |
| Self-verify after run | on (sample 5%) | `--no-self-verify` to skip |
| Dry-run (no Gemini calls) | off | `--dry-run` to preview candidates |

## Phase 2 subcommands

```bash
# Cache + schema integrity (called automatically by `make run`, but useful standalone)
.venv/bin/python -m eval_agent.cli verify

# Compare two runs — exits 1 on regression, 0 on stable/improved
.venv/bin/python -m eval_agent.cli diff --from <run_id_a> --to <run_id_b>

# Rebuild verdict_cache.jsonl from state/runs/*/results.jsonl (idempotent)
.venv/bin/python -m eval_agent.cli recover

# Regenerate report.md from an existing run (no Gemini calls)
.venv/bin/python -m eval_agent.cli report --run latest

# Health check (API key, schemas, state dirs)
.venv/bin/python -m eval_agent.cli doctor
```

## Hard rules (DO NOT VIOLATE)

1. **Never invoke the eval-agent from inside the pipeline test suite.** It's a sibling project — keep it sibling.
2. **Never import eval-agent modules from this repo.** File-coupling only — the pipeline writes JSON, the eval-agent reads JSON.
3. **Never modify files under `/Users/alexandergo/Documents/Doctorat/eval-agent/`** from this repo.
4. **Pass through to the eval-agent's own session-startup procedure.** Don't pre-empt or simulate it from here.

## Reading the report

The eval-agent emits five artifacts per run under `state/runs/<ts>/`:

| File | What it tells you |
|---|---|
| `results.jsonl` | One Gemini verdict per candidate — name/type/role correctness + reasoning. Best for drilling into specific failures. |
| `summary.csv` | Per (evaluator, sub-type) precision. Best for "is this model good enough to ship?" |
| `report.md` | Human-readable headline numbers + sample failures. Best for sharing with a supervisor. |
| `manifest.json` | Run config + cache stats + token counts. |
| `self_verify.json` | 5% re-judge agreement check (PASS/FAIL at floor 0.95). |

The verdict format is documented in `/Users/alexandergo/Documents/Doctorat/eval-agent/config/schemas/verdict.v1.json`.

## Cost + runtime

- ~$0.05–$0.15 per full 68-record run on `gemini-3.5-flash` (~162 candidates × ~1.5 K input + ~200 output tokens; Flash pricing)
- ~3 minutes runtime at default 60 RPM, 4 parallel workers
- Cache hits are free — incremental re-runs after pipeline tweaks only call Gemini for new/changed candidates
- Switching the `--judge` ID invalidates the cache cleanly (cache key is `sha256(judge_id || prompt)`)

## When NOT to use

- Don't use to evaluate Stage 1 (MARC parse) — it's deterministic, LLM judgment adds no signal
- Don't use Stages 4-6 in MVP — those evaluators land in roadmap phases 4 and 6 (see eval-agent README)
- Don't use as a Wikidata-upload gate — the eval-agent never makes Wikidata writes; the moratorium in `CLAUDE.md` Rule 25 is the canonical gate
