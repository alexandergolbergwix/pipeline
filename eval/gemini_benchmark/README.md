# Gemini-vs-trained-models benchmark

Compare the project's four trained models (Person NER, Provenance NER, Contents
NER, Genre classifier) against Gemini 2.5 Flash in zero-shot and 3-shot modes,
on a deterministic `seed=42` sample of 100 held-out validation records per
task. Strict metrics come from the benchmark itself; lenient (rubric-based)
metrics come from a sibling eval-agent run that judges every prediction with
Gemini 3.x as a second-opinion judge.

## Prerequisites

- `.venv` set up at the repo root. The canonical bootstrap is `uv venv
  --python 3.12 && uv sync --python 3.12` from the repo root.
- `GEMINI_API_KEY` exported in the shell:

  ```bash
  export GEMINI_API_KEY="..."
  ```
- The four trained model artefacts in place:
  - `ner/provenance_ner_model.pt`
  - `ner/contents_ner_model.pt`
  - `ner/genre_classifier_model.pt`
  - HF model `alexgoldberg/hebrew-manuscript-joint-ner-v2` (downloaded
    automatically on first run, cached under `~/.cache/huggingface/`).
- For `--no-eval-agent` runs (strict metrics only), nothing else is needed.
  For full runs the sibling eval-agent project at
  `/Users/alexandergo/Documents/Doctorat/eval-agent/` must be present and
  bootstrapped (`bash init.sh` once).

## Quick start

All commands run from the repo root and use the project venv. The default
invocation is the canonical 100-record / `seed=42` benchmark; the `--sample 2
--no-eval-agent` variant is a smoke test that exercises every code path
without burning Gemini budget.

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline

# Person NER — full benchmark
PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_person_ner
# smoke:
PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_person_ner --sample 2 --no-eval-agent

# Provenance NER — full benchmark
PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_provenance_ner
# smoke:
PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_provenance_ner --sample 2 --no-eval-agent

# Contents NER — full benchmark
PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_contents_ner
# smoke:
PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_contents_ner --sample 2 --no-eval-agent

# Genre classifier — full benchmark
PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_genre_classifier
# smoke:
PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_genre_classifier --sample 2 --no-eval-agent
```

Defaults: `--sample 100 --seed 42 --gemini-model gemini-2.5-flash --few-shot-k
3 --output-dir eval/gemini_benchmark/results/<task>/`.

## Output layout

Each run writes a fresh timestamped directory:

```
eval/gemini_benchmark/results/<task>/<UTC-timestamp>/
├── predictions.jsonl   # one row per (item, method) — trained / gemini-zero / gemini-3shot
├── verdicts.jsonl      # eval-agent per-prediction verdicts (empty if --no-eval-agent)
├── metrics.json        # strict + lenient micro-precision/recall/F1 per method
└── summary.md          # human-readable comparison table
```

- `predictions.jsonl` — raw per-item predictions for every method. Use it to
  inspect individual cases or recompute metrics with custom matching rules.
- `verdicts.jsonl` — the eval-agent's per-prediction judgement (pass / fail /
  partial / unsure) used to compute the lenient column in `metrics.json`.
- `metrics.json` — the numbers that go into the paper. Both strict
  (gold-vs-pred exact match) and lenient (Gemini judge) for each method.
- `summary.md` — the same numbers rendered as a Markdown table, suitable for
  paste-into-Slack / paste-into-paper.

## Comparing across models

There is no aggregator on purpose. To see every recent run at a glance:

```bash
cat eval/gemini_benchmark/results/*/*/summary.md
```

Or just the latest run per task:

```bash
for task in person_ner provenance_ner contents_ner genre_classifier; do
  latest=$(ls -1 eval/gemini_benchmark/results/$task/ | sort | tail -1)
  echo "=== $task / $latest ==="
  cat eval/gemini_benchmark/results/$task/$latest/summary.md
done
```

## Determinism and reproducibility

- The validation split is recomputed deterministically from `--seed` plus the
  task's raw dataset file — same `seed` + same dataset bytes → same 100 item
  IDs.
- The trained-model column of `predictions.jsonl` is byte-identical across
  re-runs on the same machine. Floating-point ordering on MPS / CUDA can
  shift the last digit on a different host, but the predicted labels and
  spans do not change.
- `few_shot_item_ids` are recorded in `metadata` (top of `predictions.jsonl`)
  and are reproducible from the same seed.
- Gemini outputs vary slightly between runs because the client uses
  `temperature=0.2`. Re-running with `--no-eval-agent` and the same `--seed
  --sample` gives identical trained-model outputs and slightly different
  Gemini outputs.

## Cost estimate

Per script, per full benchmark (`--sample 100`, both zero-shot and 3-shot):

- 2 Gemini methods × 100 items = **~200 Gemini API calls per script**, so
  ~800 calls for the full 4-script suite.
- At `gemini-2.5-flash` pricing this is roughly **$2–4 per full run**.

Adding the eval-agent (drop the `--no-eval-agent` flag) issues roughly 3
additional judge calls per prediction (one per method) — about **400 extra
calls per script**, or roughly **$8–12 total** for the full suite.

## Troubleshooting

- `RuntimeError: GEMINI_API_KEY is not set` — export the env var:
  `export GEMINI_API_KEY="..."`.
- `FileNotFoundError: ner/provenance_ner_model.pt` (or
  `contents_ner_model.pt` / `genre_classifier_model.pt`) — the trained
  checkpoints have not been downloaded / built. See the training scripts
  under `ner/train_*.py`, or fetch them from the project's release artefacts.
- HTTP 429 / rate-limit from Gemini — the shared client retries 4 times with
  exponential backoff. If it still fails, re-run later. Concurrency is fixed
  at 1 today and is not configurable.
- Eval-agent subprocess fails — re-run with `--no-eval-agent`. The strict
  metrics in `metrics.json` are still meaningful on their own; only the
  lenient column will be missing.
- HF model load reports `MISSING: pooler.dense.weight` — harmless. The
  pooler is not used by the token-classification head.

## What each script evaluates

| Script | Model under test | Label space | Source data | Evaluator id |
|---|---|---|---|---|
| `benchmark_person_ner.py` | `alexgoldberg/hebrew-manuscript-joint-ner-v2` (HF) | `PERSON` spans + role | `ner/processed-data/multi_entity_{train_filtered,val_filtered,test}.jsonl` | `person_ner` |
| `benchmark_provenance_ner.py` | `ner/provenance_ner_model.pt` | `OWNER`, `DATE`, `COLLECTION` | `ner/processed-data/provenance_dataset.jsonl` | `provenance_ner` |
| `benchmark_contents_ner.py` | `ner/contents_ner_model.pt` | `WORK`, `FOLIO`, `WORK_AUTHOR` | `ner/processed-data/contents_dataset.jsonl` | `contents_ner` |
| `benchmark_genre_classifier.py` | `ner/genre_classifier_model.pt` | 8-class multi-label genre + NOTA | `data/tsvs/genre_samples.tsv` | `genre_classifier` |

Shared infrastructure (dataset splitting, prompt loading, Gemini client,
metric helpers, eval-agent driver) lives in `_shared.py`. Per-task system
prompts live in `prompts/<task>_system.txt`.
