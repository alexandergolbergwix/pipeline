#!/usr/bin/env python3
"""Benchmark Genre classifier: trained GenreClassifier vs. Gemini 3.5 Flash.

Compares three methods on a deterministic validation fold:
    1. Trained model (ner/genre_classifier_model.pt via GenreClassifier)
    2. Gemini 3.5 Flash zero-shot
    3. Gemini 3.5 Flash 3-shot (stratified)

Run from the repo root::

    PYTHONPATH=src:. .venv/bin/python -m eval.gemini_benchmark.benchmark_genre_classifier \\
        [--sample 100] [--seed 42] [--no-eval-agent] [--output-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

TASK: str = "genre_classifier"
EVALUATOR_ID: str = "genre_classifier"
PROMPT_PATH: Path = Path(__file__).parent / "prompts" / "genre_classifier_system.txt"
GENRE_CHECKPOINT: str = "ner/genre_classifier_model.pt"
NOTA_LABEL: str = "__NOTA__"
NOTA_OUTPUT_TOKEN: str = "other"


def derive_label_space_from_data(items: list[BenchmarkItem]) -> list[str]:
    """Union of all genre labels seen in ``items`` (sorted, deterministic)."""
    labels: set[str] = set()
    for it in items:
        for g in it.gold:
            labels.add(str(g))
    return sorted(labels)


def label_space_from_classifier(classifier: Any) -> list[str]:
    """Active genre labels per the loaded checkpoint, NOTA filtered out."""
    mapping: dict[str, int] = classifier.genre_label2id
    return sorted(label for label in mapping if label != NOTA_LABEL)


def trained_predict(classifier: Any, item: BenchmarkItem) -> list[str]:
    """Adapt GenreClassifier output to ``list[str]`` of label names.

    The classifier signature is ``predict(title, notes)``. The benchmark item's
    ``text`` is the concatenated training-time input (title + notes joined by
    space), so we pass it through as a single notes entry with an empty title.
    GenreClassifier emits ``[("other", conf)]`` when NOTA fires; we collapse
    that to the empty list so the metric layer sees a clean "no label" signal.
    """
    pairs: list[tuple[str, float]] = classifier.predict(title="", notes=[item.text])
    if len(pairs) == 1 and pairs[0][0] == NOTA_OUTPUT_TOKEN:
        return []
    return [label for label, _confidence in pairs]


def gemini_predict(
    text: str,
    system_prompt: str,
    label_space: list[str],
    few_shots: list[tuple[str, str]] | None = None,
    model: str = "gemini-3.5-flash",
) -> list[str]:
    schema = {
        "type": "object",
        "properties": {
            "genres": {
                "type": "array",
                "items": {"type": "string", "enum": label_space},
            },
        },
        "required": ["genres"],
    }
    response = call_gemini(
        system_prompt=system_prompt,
        user_text=text,
        response_schema=schema,
        few_shots=few_shots,
        model=model,
    )
    raw = response.get("genres", [])
    if not isinstance(raw, list):
        return []
    return [str(g) for g in raw if isinstance(g, (str, int))]


def few_shot_demonstrations(
    items: list[BenchmarkItem],
) -> list[tuple[str, str]]:
    demos: list[tuple[str, str]] = []
    for it in items:
        gold_payload = {"genres": [str(g) for g in it.gold]}
        demos.append((it.text, json.dumps(gold_payload, ensure_ascii=False)))
    return demos


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark Genre classifier against Gemini 3.5 Flash.",
    )
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-eval-agent", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--gemini-model", default="gemini-3.5-flash")
    parser.add_argument(
        "--checkpoint", type=Path, default=Path(GENRE_CHECKPOINT),
    )
    args = parser.parse_args()

    print(
        f"[genre] loading validation fold (seed={args.seed})…",
        file=sys.stderr,
    )
    train, val = load_validation_fold(TASK, seed=args.seed)
    items = sample_validation(val, n=args.sample, seed=args.seed)
    print(
        f"[genre] {len(items)} validation items sampled",
        file=sys.stderr,
    )

    print(
        f"[genre] loading GenreClassifier (checkpoint={args.checkpoint})…",
        file=sys.stderr,
    )
    from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415

    classifier = GenreClassifier(model_path=str(args.checkpoint))

    # Prefer the checkpoint's active label list — it is the authoritative
    # vocabulary the trained model can emit. Cross-check against the fold so
    # we don't ship a prompt enum that excludes labels actually present in
    # gold annotations (which would crash structured-output enforcement).
    label_space = label_space_from_classifier(classifier)
    data_labels = set(derive_label_space_from_data(train + val))
    extra_data_labels = sorted(data_labels - set(label_space))
    if extra_data_labels:
        label_space = sorted(set(label_space) | data_labels)
    print(
        f"[genre] label space ({len(label_space)}): {label_space}",
        file=sys.stderr,
    )
    if extra_data_labels:
        print(
            f"[genre] note: {len(extra_data_labels)} label(s) found in data "
            f"but not in classifier checkpoint: {extra_data_labels}",
            file=sys.stderr,
        )

    few_shots_items = stratified_few_shots(train, label_space, n=3, seed=args.seed)
    few_shots = few_shot_demonstrations(few_shots_items)
    print(
        f"[genre] few-shot items: "
        f"{[fs.item_id for fs in few_shots_items]}",
        file=sys.stderr,
    )

    raw_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    label_list_text = "\n".join(f"  - {label}" for label in label_space)
    system_prompt = raw_prompt.replace("{{LABEL_SPACE}}", label_list_text)

    print(
        f"[genre] running 3 methods on {len(items)} items…",
        file=sys.stderr,
    )
    predictions: dict[str, list[list[str]]] = {
        "trained": [],
        "gemini_0shot": [],
        "gemini_3shot": [],
    }
    for i, item in enumerate(items, start=1):
        if i % 10 == 0:
            print(f"  [{i}/{len(items)}]", file=sys.stderr)
        predictions["trained"].append(trained_predict(classifier, item))
        predictions["gemini_0shot"].append(
            gemini_predict(
                item.text, system_prompt, label_space, model=args.gemini_model,
            ),
        )
        predictions["gemini_3shot"].append(
            gemini_predict(
                item.text, system_prompt, label_space,
                few_shots=few_shots, model=args.gemini_model,
            ),
        )

    gold = [item.gold for item in items]
    metrics: dict[str, dict] = {
        name: compute_strict_metrics(
            preds, gold, label_space, task_kind="multilabel",
        )
        for name, preds in predictions.items()
    }

    if args.no_eval_agent:
        verdicts: dict[str, list[dict]] = {name: [] for name in predictions}
    else:
        print("[genre] running eval-agent judging…", file=sys.stderr)
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
        "label_space": label_space,
        "label_space_source": "classifier_checkpoint",
        "extra_data_labels": extra_data_labels,
        "timestamp": ts,
        "checkpoint": str(args.checkpoint),
    }
    write_results_bundle(
        output_dir, items, predictions, metrics, verdicts, metadata,
    )
    print(f"[genre] wrote results to {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
