"""MARC 500 Sentence Classifier Training — COLOPHON only, 5-Fold Stratified CV.

Binary classification: is this MARC 500 sentence a colophon sentence?

Architecture:
  dicta-il/dictabert [CLS] → Dropout(0.3) → Linear(768 → 1) → sigmoid

Data:
  data/tsvs/marc500_sentences.tsv  (text, is_colophon, is_provenance, control_number)
  Produced by scripts/extract_marc500_sentences.py

Usage:
    PYTHONPATH=src:. .venv/bin/python ner/train_marc500_classifier.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.model_selection import StratifiedKFold
from torch.optim import AdamW
from torch.utils.data import DataLoader, Dataset
from transformers import AutoTokenizer, get_linear_schedule_with_warmup

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from genre_classifier_model import GenreClassificationModel  # noqa: E402

LABEL2ID = {"COLOPHON": 0}
NUM_CLASSES = 1


# ── Dataset ───────────────────────────────────────────────────────────


class SentenceDataset(Dataset):
    def __init__(
        self,
        samples: list[dict],
        tokenizer: AutoTokenizer,
        max_length: int = 64,
    ) -> None:
        self.samples = samples
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        s = self.samples[idx]
        enc = self.tokenizer(
            s["text"],
            max_length=self.max_length,
            truncation=True,
            padding="max_length",
            return_tensors="pt",
        )
        return {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "labels": torch.tensor(s["label_vector"], dtype=torch.float32),
        }


# ── Data loading ──────────────────────────────────────────────────────


def load_samples(tsv_path: str, max_negatives: int = 0) -> list[dict]:
    """Load COLOPHON-labeled sentences; optionally cap pure-negative examples."""
    import csv  # noqa: PLC0415
    import random  # noqa: PLC0415

    positives: list[dict] = []
    negatives: list[dict] = []

    with open(tsv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            text = row["text"].strip()
            if not text:
                continue
            is_col = int(row.get("is_colophon", "0") or "0")
            sample = {
                "text": text,
                "label_vector": [float(is_col)],
            }
            if is_col:
                positives.append(sample)
            else:
                negatives.append(sample)

    if max_negatives > 0 and len(negatives) > max_negatives:
        random.shuffle(negatives)
        negatives = negatives[:max_negatives]

    return positives + negatives


# ── Loss ──────────────────────────────────────────────────────────────


def focal_loss(
    logits: torch.Tensor,
    labels: torch.Tensor,
    pos_weight: torch.Tensor,
    gamma: float = 2.0,
) -> torch.Tensor:
    bce = F.binary_cross_entropy_with_logits(
        logits, labels, pos_weight=pos_weight, reduction="none",
    )
    with torch.no_grad():
        probs = torch.sigmoid(logits)
        fw = torch.where(labels == 1, (1.0 - probs) ** gamma, probs ** gamma)
    return (fw * bce).mean()


def compute_pos_weight(samples: list[dict], device: torch.device) -> torch.Tensor:
    mat = np.array([s["label_vector"] for s in samples])
    pos = mat.sum(axis=0).clip(min=1)
    neg = len(samples) - pos
    return torch.tensor(neg / pos, dtype=torch.float32, device=device)


# ── Threshold tuning ──────────────────────────────────────────────────


def tune_threshold(
    model: GenreClassificationModel,
    loader: DataLoader,
    device: torch.device,
) -> float:
    """Find the sigmoid threshold that maximises binary F1 for COLOPHON."""
    model.eval()
    all_probs: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []
    with torch.no_grad():
        for batch in loader:
            logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
            all_probs.append(torch.sigmoid(logits).cpu().numpy())
            all_labels.append(batch["labels"].cpu().numpy())
    probs = np.vstack(all_probs).ravel()
    labels = np.vstack(all_labels).ravel().astype(bool)

    best_t, best_f1 = 0.5, 0.0
    for t in np.arange(0.30, 0.81, 0.05):
        preds = probs >= t
        tp = int((preds & labels).sum())
        fp = int((preds & ~labels).sum())
        fn = int((~preds & labels).sum())
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return round(best_t, 2)


def binary_f1_at_threshold(
    model: GenreClassificationModel,
    loader: DataLoader,
    device: torch.device,
    threshold: float,
) -> float:
    model.eval()
    all_probs: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []
    with torch.no_grad():
        for batch in loader:
            logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
            all_probs.append(torch.sigmoid(logits).cpu().numpy())
            all_labels.append(batch["labels"].cpu().numpy())
    probs = np.vstack(all_probs).ravel()
    labels = np.vstack(all_labels).ravel().astype(bool)
    preds = probs >= threshold
    tp = int((preds & labels).sum())
    fp = int((preds & ~labels).sum())
    fn = int((~preds & labels).sum())
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def print_metrics(
    model: GenreClassificationModel,
    loader: DataLoader,
    device: torch.device,
    threshold: float,
) -> None:
    model.eval()
    all_probs: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []
    with torch.no_grad():
        for batch in loader:
            logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
            all_probs.append(torch.sigmoid(logits).cpu().numpy())
            all_labels.append(batch["labels"].cpu().numpy())
    probs = np.vstack(all_probs).ravel()
    labels = np.vstack(all_labels).ravel().astype(bool)
    preds = probs >= threshold
    tp = int((preds & labels).sum())
    fp = int((preds & ~labels).sum())
    fn = int((~preds & labels).sum())
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    pos = int(labels.sum())
    print(f"\n  {'Class':<15} {'Thr':>5} {'P':>6} {'R':>6} {'F1':>6} {'Pos':>6}")
    print(f"  {'-'*46}")
    print(f"  {'COLOPHON':<15} {threshold:>5.2f} {p:>6.3f} {r:>6.3f} {f1:>6.3f} {pos:>6}")


# ── NER warm-start ────────────────────────────────────────────────────


def load_ner_encoder_weights(model: GenreClassificationModel, ner_checkpoint_path: str) -> None:
    ckpt = torch.load(ner_checkpoint_path, map_location="cpu", weights_only=False)
    ner_state = ckpt.get("model_state_dict", {})
    bert_weights = {k[len("bert."):]: v for k, v in ner_state.items() if k.startswith("bert.")}
    missing, _ = model.bert.load_state_dict(bert_weights, strict=False)
    print(f"  NER encoder warm-start from {ner_checkpoint_path}")
    if missing:
        print(f"  Missing keys: {missing[:3]}{'...' if len(missing) > 3 else ''}")


# ── Training ──────────────────────────────────────────────────────────


def train_fold(
    model: GenreClassificationModel,
    train_loader: DataLoader,
    val_loader: DataLoader,
    device: torch.device,
    epochs: int,
    lr: float,
    encoder_lr_factor: float,
    weight_decay: float,
    patience: int,
    pos_weight: torch.Tensor,
    focal_gamma: float,
) -> tuple[float, dict]:
    optimizer = AdamW(
        [
            {"params": model.bert.parameters(), "lr": lr * encoder_lr_factor},
            {"params": list(model.dropout.parameters()) + list(model.classifier.parameters()), "lr": lr},
        ],
        weight_decay=weight_decay,
    )
    total_steps = len(train_loader) * epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=max(1, int(0.1 * total_steps)),
        num_training_steps=total_steps,
    )

    best_f1 = 0.0
    best_state: dict = {}
    best_threshold: float = 0.5
    no_improve = 0

    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        for batch in train_loader:
            optimizer.zero_grad()
            logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
            loss = focal_loss(logits, batch["labels"].to(device), pos_weight, gamma=focal_gamma)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            total_loss += loss.item()

        threshold = tune_threshold(model, val_loader, device)
        f1 = binary_f1_at_threshold(model, val_loader, device, threshold)
        print(
            f"  Epoch {epoch + 1}/{epochs}  loss={total_loss / len(train_loader):.4f}"
            f"  val_F1={f1:.4f}  thr={threshold:.2f}",
            flush=True,
        )

        if f1 > best_f1:
            best_f1 = f1
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_threshold = threshold
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                print(f"  Early stop at epoch {epoch + 1}")
                break

    return best_threshold, best_state


# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsv", default="data/tsvs/marc500_sentences.tsv")
    parser.add_argument("--output", default="ner/marc500_classifier_model.pt")
    parser.add_argument("--model-name", default="dicta-il/dictabert")
    parser.add_argument("--ner-checkpoint", default="ner/provenance_ner_model.pt")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--encoder-lr-factor", type=float, default=0.1)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--max-length", type=int, default=64)
    parser.add_argument("--dropout", type=float, default=0.3)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--freeze-layers", type=int, default=6)
    parser.add_argument("--focal-gamma", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--max-negatives", type=int, default=100_000,
        help="Cap pure-negative sentences (0 = no cap). Default 100k.",
    )
    args = parser.parse_args()

    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    device = torch.device(
        "mps" if torch.backends.mps.is_available()
        else "cuda" if torch.cuda.is_available()
        else "cpu",
    )
    print(f"Device: {device}")

    print(f"\nLoading sentences from {args.tsv} ...")
    samples = load_samples(args.tsv, max_negatives=args.max_negatives)
    print(f"Total sentences: {len(samples)}")

    n_col = sum(1 for s in samples if s["label_vector"][0] == 1.0)
    n_neg = len(samples) - n_col
    print(f"  COLOPHON positives : {n_col}")
    print(f"  Negative           : {n_neg}")

    if len(samples) < 20:
        print("ERROR: Too few samples. Run scripts/extract_marc500_sentences.py first.")
        sys.exit(1)

    strat = np.array([int(s["label_vector"][0]) for s in samples])
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    os.makedirs(str(Path(args.output).parent), exist_ok=True)

    skf = StratifiedKFold(n_splits=args.n_folds, shuffle=True, random_state=args.seed)
    fold_results: list[dict] = []
    best_overall_f1 = 0.0
    best_overall_state: dict = {}
    best_overall_threshold: float = 0.5
    best_overall_fold = 0

    print(f"\n{'='*60}")
    print(f"MARC 500 Colophon Classifier — {args.n_folds}-Fold CV")
    print(f"  epochs={args.epochs}  batch={args.batch_size}  max_len={args.max_length}")
    print(f"  lr={args.lr}  enc_lr_factor={args.encoder_lr_factor}  freeze={args.freeze_layers}")
    print(f"  focal_gamma={args.focal_gamma}  patience={args.patience}")
    print(f"{'='*60}")

    for fold, (train_idx, val_idx) in enumerate(skf.split(samples, strat)):
        fold_num = fold + 1
        fold_ckpt = Path(args.output).parent / f"marc500_classifier_fold_{fold_num}.pt"

        print(f"\nFold {fold_num}/{args.n_folds}  train={len(train_idx)}  val={len(val_idx)}")

        train_samples_fold = [samples[i] for i in train_idx]
        val_samples_fold = [samples[i] for i in val_idx]
        pos_weight = compute_pos_weight(train_samples_fold, device)

        train_ds = SentenceDataset(train_samples_fold, tokenizer, args.max_length)
        val_ds = SentenceDataset(val_samples_fold, tokenizer, args.max_length)
        train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=args.batch_size * 2)

        model = GenreClassificationModel(
            args.model_name, NUM_CLASSES, args.dropout, freeze_layers=args.freeze_layers,
        )
        if args.ner_checkpoint and Path(args.ner_checkpoint).exists():
            load_ner_encoder_weights(model, args.ner_checkpoint)
        model = model.to(device)

        threshold, state = train_fold(
            model, train_loader, val_loader, device,
            args.epochs, args.lr, args.encoder_lr_factor,
            args.weight_decay, args.patience, pos_weight,
            focal_gamma=args.focal_gamma,
        )

        model.load_state_dict(state)
        f1 = binary_f1_at_threshold(model, val_loader, device, threshold)
        print(f"Fold {fold_num}: F1={f1:.4f}  threshold={threshold:.2f}")
        print_metrics(model, val_loader, device, threshold)

        fold_results.append({"fold": fold_num, "f1": f1, "threshold": threshold})

        torch.save(
            {
                "model_state_dict": state,
                "label2id": LABEL2ID,
                "task": "marc500_colophon_classification",
                "threshold": threshold,
                "best_fold_f1": f1,
                "base_model": args.model_name,
                "num_classes": NUM_CLASSES,
                "max_length": args.max_length,
                "fold": fold_num,
            },
            fold_ckpt,
        )
        print(f"  Fold checkpoint → {fold_ckpt}")

        if f1 > best_overall_f1:
            best_overall_f1 = f1
            best_overall_state = state
            best_overall_threshold = threshold
            best_overall_fold = fold_num

    f1_scores = [r["f1"] for r in fold_results]
    print(f"\n{'='*60}")
    print(f"Mean F1:      {np.mean(f1_scores):.4f} ± {np.std(f1_scores):.4f}")
    print(f"Best fold F1: {max(f1_scores):.4f}  (fold {best_overall_fold})")
    print(f"Best threshold: {best_overall_threshold:.2f}")
    print(f"{'='*60}")

    torch.save(
        {
            "model_state_dict": best_overall_state,
            "label2id": LABEL2ID,
            "task": "marc500_colophon_classification",
            "threshold": best_overall_threshold,
            "best_fold_f1": best_overall_f1,
            "mean_fold_f1": float(np.mean(f1_scores)),
            "base_model": args.model_name,
            "num_classes": NUM_CLASSES,
            "max_length": args.max_length,
        },
        args.output,
    )
    print(f"\nSaved best checkpoint → {args.output}  (F1={best_overall_f1:.4f})")


if __name__ == "__main__":
    main()
