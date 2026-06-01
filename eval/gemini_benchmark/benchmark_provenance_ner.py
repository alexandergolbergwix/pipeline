#!/usr/bin/env python3
"""Benchmark Provenance NER: trained NERInferencePipeline vs. Gemini 3.5 Flash.

Compares three methods on a deterministic validation fold:
    1. Trained model (ner/provenance_ner_model.pt via NERInferencePipeline)
    2. Gemini 3.5 Flash zero-shot
    3. Gemini 3.5 Flash 3-shot (stratified)

Run from the repo root::

    PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_provenance_ner \\
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

LABEL_SPACE: list[str] = ["OWNER", "DATE", "COLLECTION"]
TASK: str = "provenance_ner"
EVALUATOR_ID: str = "provenance_ner"
PROMPT_PATH: Path = Path(__file__).parent / "prompts" / "provenance_ner_system.txt"
PROVENANCE_CHECKPOINT: str = "ner/provenance_ner_model.pt"


def trained_predict(pipeline: object, text: str) -> list[tuple[str, str]]:
    """Adapt NERInferencePipeline.process_text output to list[(text, type)]."""
    spans = pipeline.process_text(text)  # type: ignore[attr-defined]
    return [(span["text"], span["type"]) for span in spans]


def gemini_predict(
    text: str,
    system_prompt: str,
    few_shots: list[tuple[str, str]] | None = None,
    model: str = "gemini-3.5-flash",
) -> list[tuple[str, str]]:
    schema = {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "type": {"type": "string", "enum": LABEL_SPACE},
                    },
                    "required": ["text", "type"],
                },
            },
        },
        "required": ["entities"],
    }
    response = call_gemini(
        system_prompt=system_prompt,
        user_text=text,
        response_schema=schema,
        few_shots=few_shots,
        model=model,
    )
    entities = response.get("entities", [])
    out: list[tuple[str, str]] = []
    if isinstance(entities, list):
        for e in entities:
            if isinstance(e, dict) and "text" in e and "type" in e:
                out.append((str(e["text"]), str(e["type"])))
    return out


def few_shot_demonstrations(
    items: list[BenchmarkItem],
) -> list[tuple[str, str]]:
    demos: list[tuple[str, str]] = []
    for it in items:
        gold_payload = {
            "entities": [
                {"text": text, "type": type_} for text, type_ in it.gold
            ],
        }
        demos.append((it.text, json.dumps(gold_payload, ensure_ascii=False)))
    return demos


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark Provenance NER against Gemini 3.5 Flash.",
    )
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-eval-agent", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--gemini-model", default="gemini-3.5-flash")
    parser.add_argument(
        "--checkpoint", type=Path, default=Path(PROVENANCE_CHECKPOINT),
    )
    args = parser.parse_args()

    print(
        f"[provenance-ner] loading validation fold (seed={args.seed})…",
        file=sys.stderr,
    )
    train, val = load_validation_fold(TASK, seed=args.seed)
    items = sample_validation(val, n=args.sample, seed=args.seed)
    print(
        f"[provenance-ner] {len(items)} validation items sampled",
        file=sys.stderr,
    )

    few_shots_items = stratified_few_shots(
        train, LABEL_SPACE, n=3, seed=args.seed,
    )
    few_shots = few_shot_demonstrations(few_shots_items)
    print(
        f"[provenance-ner] few-shot items: "
        f"{[fs.item_id for fs in few_shots_items]}",
        file=sys.stderr,
    )

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    print(
        f"[provenance-ner] loading NERInferencePipeline "
        f"(checkpoint={args.checkpoint})…",
        file=sys.stderr,
    )
    from ner.ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415

    pipeline = NERInferencePipeline(model_path=str(args.checkpoint))

    print(
        f"[provenance-ner] running 3 methods on {len(items)} items…",
        file=sys.stderr,
    )
    predictions: dict[str, list[list[tuple[str, str]]]] = {
        "trained": [],
        "gemini_0shot": [],
        "gemini_3shot": [],
    }
    for i, item in enumerate(items, start=1):
        if i % 10 == 0:
            print(f"  [{i}/{len(items)}]", file=sys.stderr)
        predictions["trained"].append(trained_predict(pipeline, item.text))
        predictions["gemini_0shot"].append(
            gemini_predict(item.text, system_prompt, model=args.gemini_model),
        )
        predictions["gemini_3shot"].append(
            gemini_predict(
                item.text, system_prompt,
                few_shots=few_shots, model=args.gemini_model,
            ),
        )

    gold = [item.gold for item in items]
    metrics: dict[str, dict] = {
        name: compute_strict_metrics(preds, gold, LABEL_SPACE, task_kind="ner")
        for name, preds in predictions.items()
    }

    if args.no_eval_agent:
        verdicts: dict[str, list[dict]] = {name: [] for name in predictions}
    else:
        print(
            "[provenance-ner] running eval-agent judging…",
            file=sys.stderr,
        )
        verdicts = {
            name: run_eval_agent_judge(name, items, preds, EVALUATOR_ID)
            for name, preds in predictions.items()
        }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (
        args.output_dir
        or (Path(__file__).parent / "results" / TASK / ts)
    )
    metadata: dict = {
        "task": TASK,
        "evaluator_id": EVALUATOR_ID,
        "sample_size": len(items),
        "seed": args.seed,
        "gemini_model": args.gemini_model,
        "few_shot_item_ids": [it.item_id for it in few_shots_items],
        "label_space": LABEL_SPACE,
        "timestamp": ts,
        "checkpoint": str(args.checkpoint),
    }
    write_results_bundle(
        output_dir, items, predictions, metrics, verdicts, metadata,
    )
    print(
        f"[provenance-ner] wrote results to {output_dir}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
