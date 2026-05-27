#!/usr/bin/env python3
"""Benchmark Person NER: trained JointNERPipeline vs. Gemini 2.5 Flash (0-shot + 3-shot).

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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Person NER benchmark: trained vs Gemini 0-shot vs Gemini 3-shot",
    )
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--no-eval-agent", action="store_true",
        help="Skip eval-agent judging — for quick smoke tests.",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--gemini-model", default="gemini-2.5-flash")
    args = parser.parse_args()

    print(
        f"[person-ner] loading validation fold (seed={args.seed})…",
        file=sys.stderr,
    )
    train, val = load_validation_fold(TASK, seed=args.seed)
    items = sample_validation(val, n=args.sample, seed=args.seed)
    print(
        f"[person-ner] {len(items)} validation items sampled "
        f"(val pool: {len(val)}, train pool: {len(train)})",
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

    print(
        f"[person-ner] running 3 methods on {len(items)} items…",
        file=sys.stderr,
    )
    predictions: dict[str, list[list[tuple[str, str]]]] = {
        "trained": [],
        "gemini_0shot": [],
        "gemini_3shot": [],
    }
    for i, item in enumerate(items, start=1):
        if i == 1 or i % 10 == 0:
            print(f"  [{i}/{len(items)}]", file=sys.stderr)
        predictions["trained"].append(trained_predict(pipeline, item.text))
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
        "gemini_model": args.gemini_model,
        "trained_model_repo": TRAINED_MODEL_REPO,
        "few_shot_item_ids": [it.item_id for it in few_shots_items],
        "few_shot_examples": few_shots,
        "label_space": LABEL_SPACE,
        "timestamp": ts,
    }
    write_results_bundle(
        output_dir, items, predictions, metrics, verdicts, metadata,
    )
    print(f"[person-ner] wrote results to {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
