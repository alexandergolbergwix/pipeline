"""Genre Classifier Training with 5-Fold Stratified CV.

Trains a DictaBERT-based multi-label sequence classifier to predict genre
(P136) from manuscript title + general notes (MARC 245 + 500 fields).

Distant supervision: labels come from records that have MARC 655 genre/form
headings, already parsed by field_handlers into the `genres` list. Records
without MARC 655 are the inference targets.

Architecture:
  - Encoder: dicta-il/dictabert (frozen — avoids overfitting on ~364 samples)
  - Head: Dropout(0.3) → Linear(768, num_genres) → sigmoid
  - Loss: BCEWithLogitsLoss (multi-label)
  - Metric: micro-F1 at threshold 0.5

Usage:
    PYTHONPATH=src:. .venv/bin/python ner/train_genre_classifier.py
    PYTHONPATH=src:. .venv/bin/python ner/train_genre_classifier.py \\
        --tsv-files data/tsvs/17th_century_samples.tsv data/tsvs/top100_richest.tsv \\
        --output ner/genre_classifier_model.pt \\
        --epochs 30 --batch-size 16
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from sklearn.model_selection import StratifiedKFold
from torch.optim import AdamW
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup

# Make sure converter/ is importable when running from repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from converter.parser.unified_reader import UnifiedReader  # noqa: E402
from converter.transformer.field_handlers import extract_all_data  # noqa: E402
from converter.wikidata.property_mapping import GENRE_TO_QID  # noqa: E402

# Minimum training examples required to include a genre class.
# Genres below this threshold go to the NOTA ("none of the above") class.
MIN_GENRE_EXAMPLES: int = 50

# Sentinel label for "not one of the top genres" — allows the model to abstain.
NOTA_LABEL: str = "__NOTA__"

# Will be populated after data loading: frequent genres + NOTA at the last index.
GENRE_LABELS: list[str] = []
NUM_GENRES: int = 0
GENRE_TO_IDX: dict[str, int] = {}


# ── Model ────────────────────────────────────────────────────────────


class GenreClassificationModel(nn.Module):
    """Frozen DictaBERT encoder + multi-label linear classification head."""

    def __init__(self, bert_model_name: str, num_genres: int, dropout: float = 0.3) -> None:
        super().__init__()
        self.bert = AutoModel.from_pretrained(bert_model_name)
        for param in self.bert.parameters():
            param.requires_grad = False
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(self.bert.config.hidden_size, num_genres)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
    ) -> torch.Tensor:
        out = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        cls = out.last_hidden_state[:, 0, :]
        return self.classifier(self.dropout(cls))  # raw logits


# ── Dataset ──────────────────────────────────────────────────────────


class GenreDataset(Dataset):
    """Multi-label genre classification dataset."""

    def __init__(
        self,
        samples: list[dict],
        tokenizer: AutoTokenizer,
        max_length: int = 256,
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


# ── Data loading ─────────────────────────────────────────────────────


def load_samples(tsv_files: list[str], min_examples: int = MIN_GENRE_EXAMPLES) -> list[dict]:
    """Load labeled samples with frequent genres + explicit NOTA class.

    Three-pass approach:
    1. Collect all records that have any MARC 655 genre in GENRE_TO_QID.
    2. Count genre frequencies; keep only genres with >= min_examples occurrences.
       Records whose genres are ALL below the threshold become NOTA training examples.
    3. Populate module-level GENRE_LABELS (frequent genres + NOTA at last index).
       Label vectors are (NUM_GENRES+1)-dimensional; NOTA is the last element.
    """
    global GENRE_LABELS, NUM_GENRES, GENRE_TO_IDX

    from collections import Counter  # noqa: PLC0415

    raw: list[dict] = []
    for tsv_path in tsv_files:
        reader = UnifiedReader(Path(tsv_path))
        for marc_record in reader.read_file():
            try:
                data = extract_all_data(marc_record)
            except Exception:
                continue
            genres_in_map = [g for g in (data.genres or []) if g in GENRE_TO_QID]
            if not genres_in_map:
                continue
            title = data.title or ""
            notes_text = " ".join(str(n) for n in (data.notes or [])[:3])
            text = (title + " " + notes_text).strip()
            if not text:
                continue
            raw.append({"text": text, "genres": genres_in_map})

    freq: Counter = Counter(g for s in raw for g in s["genres"])
    frequent_genres = sorted(g for g, cnt in freq.items() if cnt >= min_examples)
    nota_idx = len(frequent_genres)  # NOTA is always the last class
    total_classes = nota_idx + 1

    print(f"Frequent genres (>={min_examples} examples): {len(frequent_genres)} classes")
    for g in frequent_genres:
        print(f"  {freq[g]:4d}  {g}")
    nota_count = sum(1 for s in raw if not any(g in frequent_genres for g in s["genres"]))
    print(f"NOTA training examples (rare-genre records): {nota_count}")

    if not frequent_genres:
        return []

    GENRE_LABELS = frequent_genres + [NOTA_LABEL]
    NUM_GENRES = total_classes
    GENRE_TO_IDX = {g: i for i, g in enumerate(GENRE_LABELS)}

    samples: list[dict] = []
    for s in raw:
        frequent_here = [g for g in s["genres"] if g in set(frequent_genres)]
        label_vector = [0.0] * total_classes
        if frequent_here:
            for g in frequent_here:
                label_vector[frequent_genres.index(g)] = 1.0
            # label_vector[nota_idx] stays 0
        else:
            # All genres are rare → NOTA
            label_vector[nota_idx] = 1.0
        active_genres = frequent_here if frequent_here else [NOTA_LABEL]
        samples.append({"text": s["text"], "label_vector": label_vector, "genres": active_genres})

    return samples


# ── Evaluation ───────────────────────────────────────────────────────


def micro_f1(
    model: GenreClassificationModel,
    loader: DataLoader,
    device: torch.device,
    threshold: float = 0.5,
) -> float:
    model.eval()
    tp = fp = fn = 0
    with torch.no_grad():
        for batch in loader:
            logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
            preds = (torch.sigmoid(logits) >= threshold).cpu().numpy()
            labels = batch["labels"].cpu().numpy().astype(bool)
            tp += int((preds & labels).sum())
            fp += int((preds & ~labels).sum())
            fn += int((~preds & labels).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# ── Training ─────────────────────────────────────────────────────────


def compute_pos_weight(samples: list[dict], device: torch.device) -> torch.Tensor:
    """Compute per-class positive weight = (n_neg / n_pos) to handle imbalance."""
    label_matrix = np.array([s["label_vector"] for s in samples])
    n = len(samples)
    pos_counts = label_matrix.sum(axis=0).clip(min=1)
    neg_counts = n - pos_counts
    weights = neg_counts / pos_counts
    return torch.tensor(weights, dtype=torch.float32, device=device)


def train_fold(
    model: GenreClassificationModel,
    train_loader: DataLoader,
    val_loader: DataLoader,
    device: torch.device,
    epochs: int,
    lr: float,
    weight_decay: float,
    patience: int,
    pos_weight: torch.Tensor,
) -> tuple[float, dict]:
    optimizer = AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=lr,
        weight_decay=weight_decay,
    )
    total_steps = len(train_loader) * epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=max(1, int(0.1 * total_steps)),
        num_training_steps=total_steps,
    )
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    best_f1 = 0.0
    best_state: dict = {}
    no_improve = 0

    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        for batch in train_loader:
            optimizer.zero_grad()
            logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
            loss = loss_fn(logits, batch["labels"].to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            total_loss += loss.item()

        f1 = micro_f1(model, val_loader, device)
        print(f"  Epoch {epoch + 1}/{epochs}  loss={total_loss / len(train_loader):.4f}  val_F1={f1:.4f}")

        if f1 > best_f1:
            best_f1 = f1
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                print(f"  Early stop at epoch {epoch + 1}")
                break

    return best_f1, best_state


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Genre Classifier Training (distant supervision)")
    parser.add_argument(
        "--tsv-files", nargs="+",
        default=[
            "data/tsvs/17th_century_samples.tsv",
            "data/tsvs/top100_richest.tsv",
            "data/tsvs/filtered_manuscripts_after_906a.tsv",
        ],
    )
    parser.add_argument("--output", default="ner/genre_classifier_model.pt")
    parser.add_argument("--model-name", default="dicta-il/dictabert")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--dropout", type=float, default=0.3)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--min-examples", type=int, default=MIN_GENRE_EXAMPLES,
                        help="Minimum training examples to include a genre class")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    device = torch.device(
        "mps" if torch.backends.mps.is_available()
        else "cuda" if torch.cuda.is_available()
        else "cpu",
    )
    print(f"Device: {device}")
    print(f"Genre labels ({NUM_GENRES}): {GENRE_LABELS[:5]} ...")

    print("\nLoading samples from TSV files...")
    samples = load_samples(args.tsv_files, min_examples=args.min_examples)
    print(f"Labeled samples: {len(samples)}")
    if len(samples) < 10:
        print("ERROR: Not enough labeled samples. Check TSV paths and MARC 655 field presence.")
        sys.exit(1)

    # Stratify by most-common genre per sample
    strat_labels = np.array([s["genres"][0] for s in samples])

    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    os.makedirs(str(Path(args.output).parent), exist_ok=True)

    skf = StratifiedKFold(n_splits=args.n_folds, shuffle=True, random_state=args.seed)
    fold_results: list[dict] = []
    best_overall_f1 = 0.0
    best_overall_state: dict = {}

    print(f"\n{'='*60}")
    print("Genre Classifier Training — 5-Fold CV")
    print(f"{'='*60}")

    for fold, (train_idx, val_idx) in enumerate(skf.split(samples, strat_labels)):
        print(f"\nFold {fold + 1}/{args.n_folds}  train={len(train_idx)}  val={len(val_idx)}")

        train_samples_fold = [samples[i] for i in train_idx]
        train_ds = GenreDataset(train_samples_fold, tokenizer, args.max_length)
        val_ds = GenreDataset([samples[i] for i in val_idx], tokenizer, args.max_length)
        train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=args.batch_size)

        pos_weight = compute_pos_weight(train_samples_fold, device)
        model = GenreClassificationModel(args.model_name, NUM_GENRES, args.dropout).to(device)

        f1, state = train_fold(
            model, train_loader, val_loader, device,
            args.epochs, args.lr, args.weight_decay, args.patience, pos_weight,
        )
        fold_results.append({"fold": fold + 1, "f1": f1})
        print(f"Fold {fold + 1}: best micro-F1 = {f1:.4f}")

        if f1 > best_overall_f1:
            best_overall_f1 = f1
            best_overall_state = state

    f1_scores = [r["f1"] for r in fold_results]
    print(f"\n{'='*60}")
    print(f"Mean F1: {np.mean(f1_scores):.4f} ± {np.std(f1_scores):.4f}")
    print(f"Best fold F1: {max(f1_scores):.4f}")
    print(f"{'='*60}")

    torch.save(
        {
            "model_state_dict": best_overall_state,
            "genre_label2id": GENRE_TO_IDX,
            "task": "genre_classification",
            "threshold": args.threshold,
            "best_fold_f1": best_overall_f1,
            "base_model": args.model_name,
            "num_genres": NUM_GENRES,
            "min_genre_examples": args.min_examples if hasattr(args, "min_examples") else MIN_GENRE_EXAMPLES,
        },
        args.output,
    )
    print(f"\nSaved best checkpoint → {args.output}  (F1={best_overall_f1:.4f})")


if __name__ == "__main__":
    main()
