"""Shared utilities for the Gemini benchmark suite.

Four sibling scripts in this package compare three methods on each of
four trained pipeline stages (Person NER, Provenance NER, Contents NER,
Genre classifier). Everything they share lives here: validation-fold
loading that replays the trainer's StratifiedKFold, a deterministic
sub-sampler, a stratified few-shot picker, a raw-urllib Gemini call with
retry + structured-output enforcement, strict per-type metrics, an
eval-agent subprocess driver, and the four-file output bundle writer.

The module is pure-function where possible. The Gemini call performs
network I/O; the eval-agent driver spawns a subprocess. Everything else
is deterministic given the same inputs.
"""

from __future__ import annotations

import csv
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sklearn.model_selection import StratifiedKFold


_REPO_ROOT = Path(__file__).resolve().parents[2]
_NER_DATA_DIR = _REPO_ROOT / "ner" / "processed-data"
_TSV_DIR = _REPO_ROOT / "data" / "tsvs"

_PERSON_ROLE_V3_DATA_DIR = (
    _REPO_ROOT / "ner" / "experiments" / "person_role_v3" / "data"
)
_PERSON_ROLE_V3_TRAIN_FILE = _PERSON_ROLE_V3_DATA_DIR / "train.jsonl"
_PERSON_ROLE_V3_VAL_FILE = _PERSON_ROLE_V3_DATA_DIR / "val.jsonl"

_PROVENANCE_DATA_FILE = _NER_DATA_DIR / "provenance_dataset.jsonl"
_CONTENTS_DATA_FILE = _NER_DATA_DIR / "contents_dataset.jsonl"
_GENRE_TSV = _TSV_DIR / "genre_samples.tsv"

_VALID_TASKS = frozenset({
    "person_ner", "provenance_ner", "contents_ner", "genre_classifier",
})

_PERSON_CLASS_LABEL2ID: dict[str, int] = {
    "AUTHOR": 0,
    "TRANSCRIBER": 1,
    "OWNER": 2,
    "CENSOR": 3,
    "TRANSLATOR": 4,
    "COMMENTATOR": 5,
}

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

_GEMINI_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "$schema", "$id", "$ref", "$defs", "definitions",
    "additionalProperties", "const", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minItems", "maxItems", "minLength", "maxLength",
    "title", "examples",
})


# ── Data model ───────────────────────────────────────────────────────


@dataclass
class BenchmarkItem:
    """One held-out validation record passed to all three methods."""

    item_id: str
    text: str
    gold: Any
    metadata: dict = field(default_factory=dict)


# ── Validation fold loading ──────────────────────────────────────────


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"source data file not found: {path}")
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"source data file not found: {path}")
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            rows.append(dict(row))
    return rows


def _bio_to_entities(
    tokens: Sequence[str], tags: Sequence[str],
) -> list[tuple[str, str]]:
    """Convert (tokens, BIO-tags) into a list of (entity_text, entity_type)."""
    entities: list[tuple[str, str]] = []
    cur_tokens: list[str] = []
    cur_type: str | None = None
    for tok, tag in zip(tokens, tags):
        if tag == "O" or tag == "" or tag is None:
            if cur_tokens and cur_type is not None:
                entities.append((" ".join(cur_tokens), cur_type))
                cur_tokens = []
                cur_type = None
            continue
        if tag.startswith("B-"):
            if cur_tokens and cur_type is not None:
                entities.append((" ".join(cur_tokens), cur_type))
            cur_type = tag[2:]
            cur_tokens = [tok]
        elif tag.startswith("I-"):
            ent_type = tag[2:]
            if cur_type == ent_type and cur_tokens:
                cur_tokens.append(tok)
            else:
                if cur_tokens and cur_type is not None:
                    entities.append((" ".join(cur_tokens), cur_type))
                cur_type = ent_type
                cur_tokens = [tok]
        else:
            if cur_tokens and cur_type is not None:
                entities.append((" ".join(cur_tokens), cur_type))
                cur_tokens = []
                cur_type = None
    if cur_tokens and cur_type is not None:
        entities.append((" ".join(cur_tokens), cur_type))
    return entities


def _build_ner_items(
    samples: list[dict], task: str,
) -> list[BenchmarkItem]:
    items: list[BenchmarkItem] = []
    for idx, sample in enumerate(samples):
        tokens = list(sample.get("tokens") or [])
        tags = list(sample.get("ner_tags") or [])
        entities = _bio_to_entities(tokens, tags)
        text = str(sample.get("text") or " ".join(tokens))
        items.append(BenchmarkItem(
            item_id=f"{task}-{idx:06d}",
            text=text,
            gold=entities,
            metadata={
                "tokens": tokens,
                "ner_tags": tags,
                "source_index": idx,
                "source_file": sample.get("source_file"),
                "source_id": sample.get("source_id"),
                "roles": list(sample.get("roles") or []),
            },
        ))
    return items


def _stratification_labels(samples: list[dict], task: str) -> np.ndarray:
    if task == "person_ner":
        labels: list[int] = []
        for s in samples:
            roles = s.get("roles") or ["AUTHOR"]
            primary = roles[0] if roles else "AUTHOR"
            labels.append(_PERSON_CLASS_LABEL2ID.get(primary, 0))
        return np.array(labels)
    if task in ("provenance_ner", "contents_ner"):
        return np.array([
            min(int(s.get("entity_count", 1) or 1), 3) for s in samples
        ])
    raise ValueError(f"_stratification_labels: unexpected task {task}")


def _load_person_role_v3_splits() -> tuple[list[dict], list[dict]]:
    """Load the dedicated role-aware train/validation split for Person NER."""
    paths = (_PERSON_ROLE_V3_TRAIN_FILE, _PERSON_ROLE_V3_VAL_FILE)
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "person role-aware v3 source file(s) missing: "
            + ", ".join(str(p) for p in missing)
            + ". Re-run the v3 person-role data/training pipeline first."
        )
    return _read_jsonl(_PERSON_ROLE_V3_TRAIN_FILE), _read_jsonl(
        _PERSON_ROLE_V3_VAL_FILE,
    )


def _load_genre_samples() -> list[dict[str, Any]]:
    rows = _read_tsv(_GENRE_TSV)
    out: list[dict[str, Any]] = []
    for row in rows:
        text = (row.get("text") or "").strip()
        raw_genres = (row.get("genres") or "").strip()
        if not text or not raw_genres:
            continue
        genres = [g.strip() for g in raw_genres.split(";") if g.strip()]
        if not genres:
            continue
        out.append({"text": text, "genres": genres})
    return out


def load_validation_fold(
    task: str,
    fold: int = 0,
    seed: int = 42,
) -> tuple[list[BenchmarkItem], list[BenchmarkItem]]:
    """Return deterministic train/validation items for a benchmark task.

    Person NER uses the dedicated role-aware v3 split created for the current
    model. The other tasks replay their trainer's five-fold split.
    """
    if task not in _VALID_TASKS:
        raise ValueError(
            f"unknown task {task!r}; expected one of {sorted(_VALID_TASKS)}"
        )
    if fold < 0 or fold >= 5:
        raise ValueError(f"fold must be in [0, 5); got {fold}")

    if task == "person_ner":
        train_samples, val_samples = _load_person_role_v3_splits()
        return (
            _build_ner_items(train_samples, task),
            _build_ner_items(val_samples, task),
        )

    if task == "provenance_ner":
        samples = _read_jsonl(_PROVENANCE_DATA_FILE)
        strat = _stratification_labels(samples, task)
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
        splits = list(skf.split(samples, strat))
        train_idx, val_idx = splits[fold]
        train = _build_ner_items([samples[i] for i in train_idx], task)
        val = _build_ner_items([samples[i] for i in val_idx], task)
        return train, val

    if task == "contents_ner":
        samples = _read_jsonl(_CONTENTS_DATA_FILE)
        strat = _stratification_labels(samples, task)
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
        splits = list(skf.split(samples, strat))
        train_idx, val_idx = splits[fold]
        train = _build_ner_items([samples[i] for i in train_idx], task)
        val = _build_ner_items([samples[i] for i in val_idx], task)
        return train, val

    # genre_classifier
    samples = _load_genre_samples()
    strat = np.array([s["genres"][0] for s in samples])
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    splits = list(skf.split(samples, strat))
    train_idx, val_idx = splits[fold]

    def _to_item(idx: int, src_idx: int) -> BenchmarkItem:
        s = samples[src_idx]
        return BenchmarkItem(
            item_id=f"genre_classifier-{src_idx:06d}",
            text=s["text"],
            gold=list(s["genres"]),
            metadata={"source_index": src_idx},
        )

    train = [_to_item(i, idx) for i, idx in enumerate(train_idx)]
    val = [_to_item(i, idx) for i, idx in enumerate(val_idx)]
    return train, val


# ── Deterministic sub-sample ─────────────────────────────────────────


def sample_validation(
    items: list[BenchmarkItem],
    n: int = 100,
    seed: int = 42,
) -> list[BenchmarkItem]:
    """Deterministic sub-sample. If ``len(items) <= n``, return all."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    rng = random.Random(seed)
    indices = list(range(len(items)))
    rng.shuffle(indices)
    picked = sorted(indices[:n])
    return [items[i] for i in picked]


# ── Stratified few-shots ─────────────────────────────────────────────


def _labels_of(item: BenchmarkItem) -> set[str]:
    """Extract the set of distinct label strings from ``item.gold``."""
    gold = item.gold
    if isinstance(gold, list):
        out: set[str] = set()
        for elt in gold:
            if isinstance(elt, tuple) and len(elt) == 2:
                out.add(str(elt[1]))
            elif isinstance(elt, str):
                out.add(elt)
        return out
    return set()


def stratified_few_shots(
    train_items: list[BenchmarkItem],
    label_space: Sequence[str],
    n: int = 3,
    seed: int = 42,
) -> list[BenchmarkItem]:
    """Pick ``n`` train examples whose union of labels covers as many distinct labels as possible."""
    if n <= 0 or not train_items:
        return []
    target = min(n, len(set(label_space)))
    rng = random.Random(seed)
    pool = list(range(len(train_items)))
    rng.shuffle(pool)

    picked: list[int] = []
    covered: set[str] = set()
    # Greedy pass: pick items that add new labels first.
    for idx in pool:
        if len(picked) >= n:
            break
        item_labels = _labels_of(train_items[idx])
        if item_labels - covered:
            picked.append(idx)
            covered.update(item_labels)
        if len(covered) >= target and len(picked) >= n:
            break

    # If we still need more, fill from the shuffled pool, skipping picked.
    if len(picked) < n:
        for idx in pool:
            if len(picked) >= n:
                break
            if idx not in picked:
                picked.append(idx)

    return [train_items[i] for i in picked[:n]]


# ── Gemini call ──────────────────────────────────────────────────────


def _sanitize_schema_for_gemini(schema: dict | None) -> dict | None:
    if schema is None:
        return None
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in _GEMINI_UNSUPPORTED_SCHEMA_KEYS:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {
                pk: _sanitize_schema_for_gemini(pv)
                for pk, pv in v.items()
            }
        elif k == "items" and isinstance(v, dict):
            out[k] = _sanitize_schema_for_gemini(v)
        elif isinstance(v, dict):
            out[k] = _sanitize_schema_for_gemini(v)
        elif isinstance(v, list):
            out[k] = [
                _sanitize_schema_for_gemini(elt) if isinstance(elt, dict) else elt
                for elt in v
            ]
        else:
            out[k] = v
    return out


def _build_gemini_contents(
    system_prompt: str,
    user_text: str,
    few_shots: list[tuple[str, str]] | None,
) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    if system_prompt:
        contents.append({
            "role": "user",
            "parts": [{"text": system_prompt}],
        })
        contents.append({
            "role": "model",
            "parts": [{"text": "Understood."}],
        })
    if few_shots:
        for ex_in, ex_out in few_shots:
            contents.append({
                "role": "user",
                "parts": [{"text": ex_in}],
            })
            contents.append({
                "role": "model",
                "parts": [{"text": ex_out}],
            })
    contents.append({
        "role": "user",
        "parts": [{"text": user_text}],
    })
    return contents


def _parse_gemini_response(raw_text: str) -> dict:
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"Gemini response did not decode to a JSON object: {text[:240]}"
        )
    return parsed


def call_gemini(
    system_prompt: str,
    user_text: str,
    response_schema: dict | None = None,
    few_shots: list[tuple[str, str]] | None = None,
    model: str = "gemini-3.5-flash",
    max_attempts: int = 4,
    temperature: float = 0.2,
) -> dict:
    """One Gemini API call with retry and structured-output enforcement."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set; export it before running the benchmark"
        )

    url = _GEMINI_ENDPOINT.format(model=model)
    gen_cfg: dict[str, Any] = {
        "temperature": float(temperature),
        "maxOutputTokens": 4096,
    }
    if response_schema is not None:
        gen_cfg["responseMimeType"] = "application/json"
        sanitized = _sanitize_schema_for_gemini(response_schema)
        if sanitized is not None:
            gen_cfg["responseSchema"] = sanitized

    payload = {
        "contents": _build_gemini_contents(system_prompt, user_text, few_shots),
        "generationConfig": gen_cfg,
    }

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(
                f"Gemini HTTP error (attempt {attempt}/{max_attempts}): "
                f"{exc.code} — {err_body[:240]}"
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = RuntimeError(
                f"Gemini network error (attempt {attempt}/{max_attempts}): {exc}"
            )
        except RuntimeError as exc:
            last_error = exc
        else:
            candidates = data.get("candidates") or []
            if not candidates:
                last_error = RuntimeError(
                    f"Gemini response had no candidates: {json.dumps(data)[:240]}"
                )
            else:
                parts = (
                    (candidates[0].get("content") or {}).get("parts") or []
                )
                if not parts:
                    finish = candidates[0].get("finishReason", "unknown")
                    last_error = RuntimeError(
                        f"Gemini candidate had no parts (finish={finish})"
                    )
                else:
                    raw_text = parts[0].get("text") or ""
                    try:
                        return _parse_gemini_response(raw_text)
                    except (json.JSONDecodeError, RuntimeError) as exc:
                        last_error = RuntimeError(
                            f"Gemini parse error (attempt {attempt}): "
                            f"{exc}: {raw_text[:240]}"
                        )
        if attempt < max_attempts:
            backoff = 2 ** (attempt - 1)
            time.sleep(backoff)

    raise RuntimeError(
        f"Gemini call failed after {max_attempts} attempts: {last_error}"
    )


# ── Strict metrics ───────────────────────────────────────────────────


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _to_ner_set(items: Any) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    if not isinstance(items, list):
        return out
    for elt in items:
        if isinstance(elt, tuple) and len(elt) == 2:
            out.add((str(elt[0]), str(elt[1])))
        elif isinstance(elt, list) and len(elt) == 2:
            out.add((str(elt[0]), str(elt[1])))
        elif isinstance(elt, dict):
            text = elt.get("text") or elt.get("entity") or elt.get("name")
            etype = elt.get("type") or elt.get("label")
            if text is not None and etype is not None:
                out.add((str(text), str(etype)))
    return out


def _normalise_entity_text(text: object) -> str:
    return " ".join(str(text).split())


def _person_name_role_sets(
    items: Any,
    *,
    include_role: bool,
) -> set[tuple[str, str] | str]:
    out: set[tuple[str, str] | str] = set()
    for text, role in _to_ner_set(items):
        norm_text = _normalise_entity_text(text)
        if include_role:
            out.add((norm_text, role))
        else:
            out.add(norm_text)
    return out


def compute_person_role_metrics(
    predictions: list[Any],
    gold: list[Any],
) -> dict:
    """Person NER metrics matching the v3 training report shape."""
    if len(predictions) != len(gold):
        raise ValueError(
            f"predictions ({len(predictions)}) and gold ({len(gold)}) "
            "must have the same length"
        )

    strict_tp = strict_fp = strict_fn = 0
    name_tp = name_fp = name_fn = 0
    matched_names = 0
    correct_roles = 0

    for pred_item, gold_item in zip(predictions, gold):
        pred_span_role = _person_name_role_sets(pred_item, include_role=True)
        gold_span_role = _person_name_role_sets(gold_item, include_role=True)
        strict_tp += len(pred_span_role & gold_span_role)
        strict_fp += len(pred_span_role - gold_span_role)
        strict_fn += len(gold_span_role - pred_span_role)

        pred_names = _person_name_role_sets(pred_item, include_role=False)
        gold_names = _person_name_role_sets(gold_item, include_role=False)
        name_tp += len(pred_names & gold_names)
        name_fp += len(pred_names - gold_names)
        name_fn += len(gold_names - pred_names)

        pred_roles_by_name: dict[str, set[str]] = {}
        for text, role in _to_ner_set(pred_item):
            pred_roles_by_name.setdefault(
                _normalise_entity_text(text), set(),
            ).add(role)
        for text, role in _to_ner_set(gold_item):
            name = _normalise_entity_text(text)
            if name not in pred_roles_by_name:
                continue
            matched_names += 1
            if role in pred_roles_by_name[name]:
                correct_roles += 1

    strict_p, strict_r, strict_f = _prf(strict_tp, strict_fp, strict_fn)
    name_p, name_r, name_f = _prf(name_tp, name_fp, name_fn)
    return {
        "strict_span_role": {
            "precision": strict_p,
            "recall": strict_r,
            "f1": strict_f,
            "tp": strict_tp,
            "fp": strict_fp,
            "fn": strict_fn,
        },
        "name_only": {
            "precision": name_p,
            "recall": name_r,
            "f1": name_f,
            "tp": name_tp,
            "fp": name_fp,
            "fn": name_fn,
        },
        "role_given_name": {
            "matched_names": matched_names,
            "correct_roles": correct_roles,
            "accuracy": _safe_div(correct_roles, matched_names),
        },
    }


def _to_label_set(items: Any) -> set[str]:
    out: set[str] = set()
    if not isinstance(items, list):
        return out
    for elt in items:
        if isinstance(elt, str):
            out.add(elt)
        elif isinstance(elt, dict):
            label = elt.get("label") or elt.get("genre") or elt.get("name")
            if label is not None:
                out.add(str(label))
    return out


def compute_strict_metrics(
    predictions: list[Any],
    gold: list[Any],
    label_space: Sequence[str],
    task_kind: str,
) -> dict:
    """Strict per-type + micro + macro P/R/F1."""
    if task_kind not in ("ner", "multilabel"):
        raise ValueError(
            f"task_kind must be 'ner' or 'multilabel'; got {task_kind!r}"
        )
    if len(predictions) != len(gold):
        raise ValueError(
            f"predictions ({len(predictions)}) and gold ({len(gold)}) "
            "must have the same length"
        )

    label_list = list(label_space)
    per_type: dict[str, dict[str, float]] = {}
    total_tp = 0
    total_fp = 0
    total_fn = 0

    if task_kind == "ner":
        pred_sets = [_to_ner_set(p) for p in predictions]
        gold_sets = [_to_ner_set(g) for g in gold]
        for label in label_list:
            tp = fp = fn = 0
            for ps, gs in zip(pred_sets, gold_sets):
                ps_l = {e for e in ps if e[1] == label}
                gs_l = {e for e in gs if e[1] == label}
                tp += len(ps_l & gs_l)
                fp += len(ps_l - gs_l)
                fn += len(gs_l - ps_l)
            p, r, f = _prf(tp, fp, fn)
            per_type[label] = {
                "precision": p, "recall": r, "f1": f, "support": tp + fn,
            }
            total_tp += tp
            total_fp += fp
            total_fn += fn
    else:
        pred_sets_l = [_to_label_set(p) for p in predictions]
        gold_sets_l = [_to_label_set(g) for g in gold]
        for label in label_list:
            tp = fp = fn = 0
            for ps, gs in zip(pred_sets_l, gold_sets_l):
                in_pred = label in ps
                in_gold = label in gs
                if in_pred and in_gold:
                    tp += 1
                elif in_pred:
                    fp += 1
                elif in_gold:
                    fn += 1
            p, r, f = _prf(tp, fp, fn)
            per_type[label] = {
                "precision": p, "recall": r, "f1": f, "support": tp + fn,
            }
            total_tp += tp
            total_fp += fp
            total_fn += fn

    micro_p, micro_r, micro_f = _prf(total_tp, total_fp, total_fn)
    if per_type:
        macro_p = sum(v["precision"] for v in per_type.values()) / len(per_type)
        macro_r = sum(v["recall"] for v in per_type.values()) / len(per_type)
        macro_f = sum(v["f1"] for v in per_type.values()) / len(per_type)
    else:
        macro_p = macro_r = macro_f = 0.0

    return {
        "per_type": per_type,
        "micro": {"precision": micro_p, "recall": micro_r, "f1": micro_f},
        "macro": {"precision": macro_p, "recall": macro_r, "f1": macro_f},
        "n_items": len(predictions),
    }


# ── Eval-agent driver ────────────────────────────────────────────────


def _locate_eval_agent_root() -> Path | None:
    env_path = os.environ.get("EVAL_AGENT_PATH")
    if env_path:
        cand = Path(env_path).expanduser().resolve()
        if (cand / "eval_agent" / "cli.py").is_file():
            return cand

    try:
        sys.path.insert(0, str(_REPO_ROOT / "src"))
        from mhm_pipeline.eval_agent_runner import locate_bundled_eval_agent
        return locate_bundled_eval_agent()
    except (ImportError, FileNotFoundError, OSError):
        pass

    sibling = _REPO_ROOT.parent / "eval-agent"
    if (sibling / "eval_agent" / "cli.py").is_file():
        return sibling
    return None


def _entity_payload_for_evaluator(
    evaluator_id: str, pred: Any,
) -> list[dict[str, Any]]:
    """Convert a method's predicted payload into ner_results.json entries."""
    out: list[dict[str, Any]] = []
    if evaluator_id == "genre_classifier":
        labels = _to_label_set(pred)
        return [
            {"label": label, "confidence": 1.0} for label in sorted(labels)
        ]

    pred_set = _to_ner_set(pred)
    if evaluator_id == "person_ner":
        for text, etype in sorted(pred_set):
            out.append({
                "source": "person_ner",
                "person": text,
                "text": text,
                "role": etype,
                "type": "PERSON",
                "confidence": 0.85,
            })
        return out

    out_source = evaluator_id
    for text, etype in sorted(pred_set):
        out.append({
            "source": out_source,
            "text": text,
            "type": etype,
            "confidence": 0.9,
        })
    return out


def _synth_marc_record(item: BenchmarkItem) -> dict[str, Any]:
    return {
        "_control_number": item.item_id,
        "title": item.text[:200],
        "subtitle": None,
        "variant_titles": [],
        "authors": [],
        "contributors": [],
        "provenance": [],
        "notes": [item.text],
        "colophon_text": "",
        "data_from_colophon": {},
        "genres": [],
        "subjects": [],
        "dates": {},
        "place": None,
        "acquisition_source": None,
        "related_places": [],
        "is_anthology": False,
        "has_decoration": False,
    }


def _synth_ner_record(
    item: BenchmarkItem, evaluator_id: str, pred: Any,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "_control_number": item.item_id,
        "text": item.text,
        "entities": [],
        "ml_genres": [],
    }
    payload = _entity_payload_for_evaluator(evaluator_id, pred)
    if evaluator_id == "genre_classifier":
        rec["ml_genres"] = payload
    else:
        rec["entities"] = payload
    return rec


def run_eval_agent_judge(
    method_name: str,
    items: list[BenchmarkItem],
    predictions: list[Any],
    evaluator_id: str,
) -> list[dict]:
    """Invoke the bundled eval-agent on this method's predictions."""
    if len(items) != len(predictions):
        raise ValueError(
            f"items ({len(items)}) and predictions ({len(predictions)}) "
            "must have the same length"
        )

    agent_root = _locate_eval_agent_root()
    if agent_root is None:
        return []

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tmp_dir = Path("/tmp/gemini_benchmark") / f"{method_name}_{ts}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_dir / "state"
    (state_dir / "runs").mkdir(parents=True, exist_ok=True)
    (state_dir / "cache").mkdir(parents=True, exist_ok=True)

    marc_records = [_synth_marc_record(it) for it in items]
    ner_records = [
        _synth_ner_record(it, evaluator_id, pred)
        for it, pred in zip(items, predictions)
    ]

    (tmp_dir / "marc_extracted.json").write_text(
        json.dumps(marc_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (tmp_dir / "ner_results.json").write_text(
        json.dumps(ner_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    env = dict(os.environ)
    py_path = env.get("PYTHONPATH", "")
    parts = [str(agent_root), py_path] if py_path else [str(agent_root)]
    env["PYTHONPATH"] = os.pathsep.join(p for p in parts if p)
    env["EVAL_AGENT_STATE_DIR"] = str(state_dir)

    cmd = [
        sys.executable, "-m", "eval_agent.cli", "run",
        "--pipeline-output", str(tmp_dir),
        "--state-dir", str(state_dir),
        "--evaluators", evaluator_id,
    ]

    try:
        subprocess.run(
            cmd,
            cwd=str(tmp_dir),
            env=env,
            check=False,
            capture_output=True,
            timeout=3600,
        )
    except (subprocess.SubprocessError, OSError):
        return []

    runs_dir = state_dir / "runs"
    if not runs_dir.is_dir():
        return []
    run_subdirs = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )
    if not run_subdirs:
        return []
    results_path = run_subdirs[-1] / "results.jsonl"
    if not results_path.is_file():
        return []

    verdicts: list[dict] = []
    with results_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                verdicts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return verdicts


# ── Output bundle ────────────────────────────────────────────────────


def _summarise_verdicts(verdicts: list[dict]) -> dict[str, int]:
    buckets = {"looks_right": 0, "wrong": 0, "partly": 0, "abstain": 0, "other": 0}
    for v in verdicts:
        overall = (v.get("verdict") or {}).get("overall") if isinstance(v.get("verdict"), dict) else None
        if overall is None:
            overall = v.get("overall") or v.get("status")
        if not isinstance(overall, str):
            buckets["other"] += 1
            continue
        norm = overall.lower().replace("-", "_")
        if norm in ("pass", "full", "looks_right", "correct"):
            buckets["looks_right"] += 1
        elif norm in ("fail", "wrong", "incorrect"):
            buckets["wrong"] += 1
        elif norm in ("partial", "partly", "partially_correct"):
            buckets["partly"] += 1
        elif norm in ("abstain", "unsure", "cant_tell", "cannot_tell", "unclear"):
            buckets["abstain"] += 1
        else:
            buckets["other"] += 1
    return buckets


def _format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _build_summary_md(
    items: list[BenchmarkItem],
    predictions_by_method: dict[str, list[Any]],
    metrics_by_method: dict[str, dict],
    verdicts_by_method: dict[str, list[dict]],
    metadata: dict,
) -> str:
    lines: list[str] = []
    task = metadata.get("task", "unknown")
    sample_size = metadata.get("sample_size", len(items))
    seed = metadata.get("seed", "n/a")
    gemini_model = metadata.get("gemini_model", "n/a")
    ts = metadata.get("timestamp", datetime.now(timezone.utc).isoformat())

    lines.append(f"# Gemini benchmark summary — {task}")
    lines.append("")
    lines.append(f"- task: `{task}`")
    lines.append(f"- sample size: {sample_size}")
    lines.append(f"- seed: {seed}")
    sample_mode = metadata.get("sample_mode")
    sample_seed = metadata.get("sample_seed")
    if sample_mode is not None:
        lines.append(f"- sample mode: `{sample_mode}`")
    if sample_seed is not None:
        lines.append(f"- sample seed: {sample_seed}")
    lines.append(f"- gemini model: `{gemini_model}`")
    lines.append(f"- timestamp: {ts}")
    few_shots = metadata.get("few_shot_examples")
    if few_shots is not None:
        lines.append(f"- few-shot examples: {len(few_shots)}")
    lines.append("")

    lines.append("## Strict metrics + AI-judge verdicts per method")
    lines.append("")
    lines.append(
        "| Method | Micro P | Micro R | Micro F1 | Macro F1 | Looks right | Wrong | Partly | Abstain |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|---|"
    )
    for method in predictions_by_method:
        metrics = metrics_by_method.get(method, {})
        micro = metrics.get("micro", {})
        macro = metrics.get("macro", {})
        buckets = _summarise_verdicts(verdicts_by_method.get(method, []))
        lines.append(
            "| {method} | {mp} | {mr} | {mf} | {Mf} | {lr} | {w} | {p} | {a} |".format(
                method=method,
                mp=_format_pct(micro.get("precision", 0.0)),
                mr=_format_pct(micro.get("recall", 0.0)),
                mf=_format_pct(micro.get("f1", 0.0)),
                Mf=_format_pct(macro.get("f1", 0.0)),
                lr=buckets["looks_right"],
                w=buckets["wrong"],
                p=buckets["partly"],
                a=buckets["abstain"],
            )
        )

    lines.append("")
    lines.append("## Per-type strict F1 by method")
    lines.append("")
    all_labels: list[str] = []
    seen: set[str] = set()
    for metrics in metrics_by_method.values():
        for label in metrics.get("per_type", {}):
            if label not in seen:
                all_labels.append(label)
                seen.add(label)
    if all_labels:
        header = "| Label | " + " | ".join(predictions_by_method.keys()) + " |"
        sep = "|---|" + "|".join("---" for _ in predictions_by_method) + "|"
        lines.append(header)
        lines.append(sep)
        for label in all_labels:
            row = [label]
            for method in predictions_by_method:
                f1 = (
                    metrics_by_method.get(method, {})
                    .get("per_type", {})
                    .get(label, {})
                    .get("f1", 0.0)
                )
                row.append(_format_pct(f1))
            lines.append("| " + " | ".join(row) + " |")
    else:
        lines.append("_no per-type metrics available_")

    person_role_methods = {
        method: metric.get("person_role")
        for method, metric in metrics_by_method.items()
        if isinstance(metric.get("person_role"), dict)
    }
    if person_role_methods:
        lines.append("")
        lines.append("## Person role metrics")
        lines.append("")
        lines.append(
            "| Method | Strict span+role F1 | Name-only F1 | Role correct when name matched |"
        )
        lines.append("|---|---|---|---|")
        for method, person_metrics in person_role_methods.items():
            strict = person_metrics.get("strict_span_role", {})
            name_only = person_metrics.get("name_only", {})
            role_given_name = person_metrics.get("role_given_name", {})
            lines.append(
                "| {method} | {strict_f1} | {name_f1} | {role_acc} |".format(
                    method=method,
                    strict_f1=_format_pct(strict.get("f1", 0.0)),
                    name_f1=_format_pct(name_only.get("f1", 0.0)),
                    role_acc=_format_pct(role_given_name.get("accuracy", 0.0)),
                )
            )

    lines.append("")
    return "\n".join(lines)


def write_results_bundle(
    output_dir: Path,
    items: list[BenchmarkItem],
    predictions_by_method: dict[str, list[Any]],
    metrics_by_method: dict[str, dict],
    verdicts_by_method: dict[str, list[dict]],
    metadata: dict,
) -> None:
    """Write the four-file output bundle to ``output_dir``."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with (output_dir / "predictions.jsonl").open("w", encoding="utf-8") as f:
        for idx, item in enumerate(items):
            row = {
                "item_id": item.item_id,
                "text": item.text,
                "gold": item.gold,
                "predictions": {
                    method: preds[idx] if idx < len(preds) else None
                    for method, preds in predictions_by_method.items()
                },
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with (output_dir / "verdicts.jsonl").open("w", encoding="utf-8") as f:
        for method, verdicts in verdicts_by_method.items():
            for verdict in verdicts:
                row = {"method": method, "verdict": verdict}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    metrics_payload = {
        "metadata": metadata,
        "metrics_by_method": metrics_by_method,
        "verdict_summary_by_method": {
            method: _summarise_verdicts(verdicts)
            for method, verdicts in verdicts_by_method.items()
        },
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_md = _build_summary_md(
        items, predictions_by_method, metrics_by_method,
        verdicts_by_method, metadata,
    )
    (output_dir / "summary.md").write_text(summary_md, encoding="utf-8")


__all__ = [
    "BenchmarkItem",
    "load_validation_fold",
    "sample_validation",
    "stratified_few_shots",
    "call_gemini",
    "compute_strict_metrics",
    "compute_person_role_metrics",
    "run_eval_agent_judge",
    "write_results_bundle",
]
