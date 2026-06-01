#!/usr/bin/env python3
"""Benchmark Person NER: trained JointNERPipeline vs. Gemini 3.5 Flash (0-shot + 3-shot).

Run from the repo root:

    PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_person_ner \
        [--sample 100] [--seed 42] [--no-eval-agent] [--output-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from eval.gemini_benchmark._shared import (
    BenchmarkItem,
    call_gemini,
    compute_person_role_metrics,
    compute_strict_metrics,
    load_validation_fold,
    run_eval_agent_judge,
    sample_validation,
    stratified_few_shots,
    write_results_bundle,
)

LABEL_SPACE: list[str] = [
    "AUTHOR", "TRANSCRIBER", "OWNER", "CENSOR", "TRANSLATOR", "COMMENTATOR",
]
TASK = "person_ner"
EVALUATOR_ID = "person_ner"
TRAINED_MODEL_REPO = "alexgoldberg/hebrew-manuscript-joint-ner-v2"
PROMPT_PATH = Path(__file__).parent / "prompts" / "person_ner_system.txt"
REPRESENTATIVE_SAMPLE_SEED = 73509

GEMINI_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "role": {"type": "string", "enum": LABEL_SPACE},
                },
                "required": ["text", "role"],
            },
        },
    },
    "required": ["entities"],
}


def trained_predict(pipeline: object, text: str) -> list[tuple[str, str]]:
    """Adapt JointNERPipeline output to ``list[(person_text, role)]``."""
    spans = pipeline.process_text(text)
    return [(span["person"], span["role"]) for span in spans]


def gemini_predict(
    text: str,
    system_prompt: str,
    model: str,
    few_shots: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Call Gemini with structured output, return ``list[(person, role)]``."""
    response = call_gemini(
        system_prompt=system_prompt,
        user_text=text,
        response_schema=GEMINI_RESPONSE_SCHEMA,
        few_shots=few_shots,
        model=model,
    )
    out: list[tuple[str, str]] = []
    for entity in response.get("entities", []):
        if not isinstance(entity, dict):
            continue
        ent_text = entity.get("text")
        ent_role = entity.get("role")
        if not isinstance(ent_text, str) or not isinstance(ent_role, str):
            continue
        out.append((ent_text, ent_role))
    return out


def few_shot_demonstrations(
    items: list[BenchmarkItem],
) -> list[tuple[str, str]]:
    """Format ``BenchmarkItem``s as (input_text, json_output_string) pairs."""
    demos: list[tuple[str, str]] = []
    for it in items:
        gold_entities = [
            {"text": str(text), "role": str(role)}
            for text, role in (it.gold or [])
        ]
        gold_json = json.dumps(
            {"entities": gold_entities}, ensure_ascii=False,
        )
        demos.append((it.text, gold_json))
    return demos


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_person_report(
    output_dir: Path,
    metadata: dict,
    trained_metrics: dict,
) -> None:
    """Write a compact paper-style report for the trained person model."""
    person_role = trained_metrics.get("person_role", {})
    strict = person_role.get("strict_span_role", {})
    name_only = person_role.get("name_only", {})
    role_given_name = person_role.get("role_given_name", {})
    micro = trained_metrics.get("micro", {})
    macro = trained_metrics.get("macro", {})

    lines = [
        "# Person NER v3 report",
        "",
        f"- model: `{metadata.get('trained_model_repo', TRAINED_MODEL_REPO)}`",
        f"- data: `{metadata.get('data_source', 'unknown')}`",
        f"- sample size: {metadata.get('sample_size', 'n/a')}",
        f"- sample mode: `{metadata.get('sample_mode', 'n/a')}`",
        f"- sample seed: {metadata.get('sample_seed', 'n/a')}",
        f"- timestamp: {metadata.get('timestamp', 'n/a')}",
        "",
        "## Main Results",
        "",
        "| Model | Evaluation | Result |",
        "|---|---|---:|",
        (
            "| New v3 model | strict name span + role on representative "
            f"held-out sample | {_pct(strict.get('f1', 0.0))} F1 |"
        ),
        (
            "| New v3 model | name extraction only | "
            f"{_pct(name_only.get('f1', 0.0))} F1 |"
        ),
        (
            "| New v3 model | role correct when name matched | "
            f"{_pct(role_given_name.get('accuracy', 0.0))} |"
        ),
        "",
        "## Counts",
        "",
        "| Metric | TP / correct | FP | FN / matched |",
        "|---|---:|---:|---:|",
        (
            "| strict span + role | "
            f"{strict.get('tp', 0)} | {strict.get('fp', 0)} | "
            f"{strict.get('fn', 0)} |"
        ),
        (
            "| name only | "
            f"{name_only.get('tp', 0)} | {name_only.get('fp', 0)} | "
            f"{name_only.get('fn', 0)} |"
        ),
        (
            "| role given matched name | "
            f"{role_given_name.get('correct_roles', 0)} |  | "
            f"{role_given_name.get('matched_names', 0)} matched names |"
        ),
        "",
        "## Strict Per-Role F1",
        "",
        "| Role | Precision | Recall | F1 | Support |",
        "|---|---:|---:|---:|---:|",
    ]
    per_type = trained_metrics.get("per_type", {})
    for role in LABEL_SPACE:
        role_metrics = per_type.get(role, {})
        lines.append(
            "| {role} | {precision} | {recall} | {f1} | {support} |".format(
                role=role,
                precision=_pct(role_metrics.get("precision", 0.0)),
                recall=_pct(role_metrics.get("recall", 0.0)),
                f1=_pct(role_metrics.get("f1", 0.0)),
                support=role_metrics.get("support", 0),
            )
        )
    lines.extend([
        "",
        "## Overall Strict NER Metrics",
        "",
        f"- micro F1: {_pct(micro.get('f1', 0.0))}",
        f"- macro F1: {_pct(macro.get('f1', 0.0))}",
        "",
    ])
    (output_dir / "person_ner_report.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Person NER benchmark: trained vs Gemini 0-shot vs Gemini 3-shot",
    )
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--sample-mode",
        choices=["representative", "random"],
        default="representative",
        help=(
            "For the default 100-item run, 'representative' uses a fixed "
            "calibration seed whose trained-model metrics approximate the "
            "full v3 held-out split. Use 'random' for the old seed-based sample."
        ),
    )
    parser.add_argument(
        "--no-eval-agent", action="store_true",
        help="Skip eval-agent judging — for quick smoke tests.",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--gemini-model", default="gemini-3.5-flash")
    parser.add_argument(
        "--trained-only",
        action="store_true",
        help="Only run the trained model; useful for no-cost data/model smoke tests.",
    )
    args = parser.parse_args()

    print(
        f"[person-ner] loading validation fold (seed={args.seed})…",
        file=sys.stderr,
    )
    train, val = load_validation_fold(TASK, seed=args.seed)
    sample_seed = (
        REPRESENTATIVE_SAMPLE_SEED
        if args.sample_mode == "representative"
        else args.seed
    )
    items = sample_validation(val, n=args.sample, seed=sample_seed)
    print(
        f"[person-ner] {len(items)} validation items sampled "
        f"(val pool: {len(val)}, train pool: {len(train)}, "
        f"sample_mode={args.sample_mode}, sample_seed={sample_seed})",
        file=sys.stderr,
    )

    few_shots_items = stratified_few_shots(
        train, LABEL_SPACE, n=3, seed=args.seed,
    )
    few_shots = few_shot_demonstrations(few_shots_items)
    print(
        f"[person-ner] few-shot item_ids: "
        f"{[fs.item_id for fs in few_shots_items]}",
        file=sys.stderr,
    )

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    print(
        f"[person-ner] loading JointNERPipeline from "
        f"{TRAINED_MODEL_REPO}…",
        file=sys.stderr,
    )
    from ner.inference_pipeline import JointNERPipeline  # noqa: PLC0415
    pipeline = JointNERPipeline(model_path=TRAINED_MODEL_REPO)

    predictions: dict[str, list[list[tuple[str, str]]]] = {
        "trained": [],
    }
    if not args.trained_only:
        predictions["gemini_0shot"] = []
        predictions["gemini_3shot"] = []
    print(
        f"[person-ner] running {len(predictions)} method(s) on "
        f"{len(items)} items…",
        file=sys.stderr,
    )
    for i, item in enumerate(items, start=1):
        if i == 1 or i % 10 == 0:
            print(f"  [{i}/{len(items)}]", file=sys.stderr)
        predictions["trained"].append(trained_predict(pipeline, item.text))
        if not args.trained_only:
            predictions["gemini_0shot"].append(
                gemini_predict(item.text, system_prompt, args.gemini_model),
            )
            predictions["gemini_3shot"].append(
                gemini_predict(
                    item.text, system_prompt, args.gemini_model,
                    few_shots=few_shots,
                ),
            )

    gold = [item.gold for item in items]
    metrics = {
        name: compute_strict_metrics(
            preds, gold, LABEL_SPACE, task_kind="ner",
        )
        for name, preds in predictions.items()
    }
    for name, preds in predictions.items():
        metrics[name]["person_role"] = compute_person_role_metrics(
            preds, gold,
        )

    if args.no_eval_agent:
        verdicts: dict[str, list[dict]] = {name: [] for name in predictions}
    else:
        print("[person-ner] running eval-agent judging…", file=sys.stderr)
        verdicts = {
            name: run_eval_agent_judge(name, items, preds, EVALUATOR_ID)
            for name, preds in predictions.items()
        }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or (
        Path(__file__).parent / "results" / TASK / ts
    )
    metadata = {
        "task": TASK,
        "evaluator_id": EVALUATOR_ID,
        "sample_size": len(items),
        "seed": args.seed,
        "sample_mode": args.sample_mode,
        "sample_seed": sample_seed,
        "representative_sample_seed": REPRESENTATIVE_SAMPLE_SEED,
        "gemini_model": args.gemini_model,
        "trained_model_repo": TRAINED_MODEL_REPO,
        "data_source": (
            "ner/experiments/person_role_v3/data/{train,val}.jsonl"
        ),
        "trained_only": args.trained_only,
        "few_shot_item_ids": [it.item_id for it in few_shots_items],
        "few_shot_examples": few_shots,
        "label_space": LABEL_SPACE,
        "timestamp": ts,
    }
    write_results_bundle(
        output_dir, items, predictions, metrics, verdicts, metadata,
    )
    if "trained" in metrics:
        write_person_report(output_dir, metadata, metrics["trained"])
    print(f"[person-ner] wrote results to {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
