"""NER Model Training with 5-Fold Stratified Cross-Validation.

Generic multi-entity-type NER model for Hebrew manuscripts.
Reuses the DictaBERT encoder with a single token-classification head.

Supports training for:
- Provenance entities (OWNER, DATE, COLLECTION)
- Contents entities (WORK, FOLIO, WORK_AUTHOR)
- Any custom BIO tag set

Based on the Joint Entity-Role Model architecture from
Goldberg, Prebor & Elmalech (2025), simplified to single-head NER
(no role classification head needed when entity type IS the role).

Usage:
    python train_ner_model_kfold.py \\
        --task provenance \\
        --data-file processed-data/provenance_dataset.jsonl \\
        --output-dir provenance_model_kfold

    python train_ner_model_kfold.py \\
        --task contents \\
        --data-file processed-data/contents_dataset.jsonl \\
        --output-dir contents_model_kfold
"""

import argparse
import copy
import json
import os

import numpy as np
import torch
import torch.nn as nn
from seqeval.metrics import (
    classification_report as seqeval_report,
    f1_score as seqeval_f1,
)
from sklearn.model_selection import StratifiedKFold
from torch.optim import AdamW
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm
from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup

# ── Label definitions per task ───────────────────────────────────────

TASK_LABELS: dict[str, dict[str, int]] = {
    "provenance": {
        "O": 0,
        "B-OWNER": 1, "I-OWNER": 2,
        "B-DATE": 3, "I-DATE": 4,
        "B-COLLECTION": 5, "I-COLLECTION": 6,
    },
    "contents": {
        "O": 0,
        "B-WORK": 1, "I-WORK": 2,
        "B-FOLIO": 3, "I-FOLIO": 4,
        "B-WORK_AUTHOR": 5, "I-WORK_AUTHOR": 6,
    },
}


# ── Model ────────────────────────────────────────────────────────────


class NERModel(nn.Module):
    """Token-classification NER model with DictaBERT encoder.

    Single-head architecture: no role classification — the entity type
    in the BIO tag set IS the semantic role.
    """

    def __init__(
        self, bert_model_name: str, num_ner_labels: int, dropout: float = 0.3,
    ) -> None:
        super().__init__()
        self.bert = AutoModel.from_pretrained(bert_model_name)
        hidden_size = self.bert.config.hidden_size

        self.ner_intermediate = nn.Linear(hidden_size, hidden_size // 2)
        self.ner_dropout = nn.Dropout(dropout)
        self.ner_output = nn.Linear(hidden_size // 2, num_ner_labels)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        ner_labels: torch.Tensor | None = None,
    ) -> torch.Tensor | tuple[torch.Tensor]:
        outputs = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        sequence_output = outputs.last_hidden_state

        ner_hidden = torch.relu(self.ner_intermediate(sequence_output))
        ner_hidden = self.ner_dropout(ner_hidden)
        ner_logits = self.ner_output(ner_hidden)

        if ner_labels is not None:
            loss_fct = nn.CrossEntropyLoss(ignore_index=-100)
            active_loss = attention_mask.view(-1) == 1
            active_logits = ner_logits.view(-1, ner_logits.size(-1))
            active_labels = torch.where(
                active_loss,
                ner_labels.view(-1),
                torch.tensor(-100).type_as(ner_labels),
            )
            loss = loss_fct(active_logits, active_labels)
            return (loss,)

        return (ner_logits,)


# ── Dataset ──────────────────────────────────────────────────────────


class NERDataset(Dataset):
    """Token-level NER dataset compatible with the extraction JSONL format."""

    def __init__(
        self,
        samples: list[dict],
        tokenizer: AutoTokenizer,
        ner_label2id: dict[str, int],
        max_length: int = 256,
    ) -> None:
        self.samples = samples
        self.tokenizer = tokenizer
        self.ner_label2id = ner_label2id
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        sample = self.samples[idx]
        tokens = sample["tokens"]
        ner_tags = sample.get("ner_tags", ["O"] * len(tokens))

        encoding = self.tokenizer(
            tokens,
            is_split_into_words=True,
            max_length=self.max_length,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )

        # Align BIO labels to subword tokens
        word_ids = encoding.word_ids()
        aligned_labels: list[int] = []
        previous_word_id = None

        for word_id in word_ids:
            if word_id is None:
                aligned_labels.append(0)  # Special tokens → O
            elif word_id != previous_word_id:
                aligned_labels.append(self.ner_label2id.get(ner_tags[word_id], 0))
            else:
                # Continuation subword: B- → I-
                label = ner_tags[word_id]
                if label.startswith("B-"):
                    aligned_labels.append(
                        self.ner_label2id.get("I-" + label[2:], 0),
                    )
                else:
                    aligned_labels.append(self.ner_label2id.get(label, 0))
            previous_word_id = word_id

        return {
            "input_ids": encoding["input_ids"].squeeze(),
            "attention_mask": encoding["attention_mask"].squeeze(),
            "ner_labels": torch.tensor(aligned_labels, dtype=torch.long),
        }


# ── Training & Evaluation ────────────────────────────────────────────


def train_epoch(
    model: NERModel,
    dataloader: DataLoader,
    optimizer: AdamW,
    scheduler: object,
    device: torch.device,
) -> float:
    """Train for one epoch, return average loss."""
    model.train()
    total_loss = 0.0

    for batch in tqdm(dataloader, desc="Training", leave=False):
        input_ids = batch["input_ids"].to(device)
        attention_mask = batch["attention_mask"].to(device)
        ner_labels = batch["ner_labels"].to(device)

        optimizer.zero_grad()
        (loss,) = model(input_ids, attention_mask, ner_labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()

        total_loss += loss.item()

    return total_loss / len(dataloader)


def evaluate(
    model: NERModel,
    dataloader: DataLoader,
    ner_id2label: dict[int, str],
    device: torch.device,
    verbose: bool = False,
) -> float:
    """Evaluate NER F1 score (entity-level via seqeval)."""
    model.eval()
    all_preds: list[list[str]] = []
    all_labels: list[list[str]] = []

    with torch.no_grad():
        for batch in tqdm(dataloader, desc="Evaluating", leave=False):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            ner_labels_np = batch["ner_labels"].cpu().numpy()

            (ner_logits,) = model(input_ids, attention_mask)
            preds = torch.argmax(ner_logits, dim=-1).cpu().numpy()

            for pred, label, mask in zip(
                preds, ner_labels_np, attention_mask.cpu().numpy(),
            ):
                valid_len = mask.sum()
                pred_tags = [ner_id2label.get(p, "O") for p in pred[:valid_len]]
                true_tags = [ner_id2label.get(l, "O") for l in label[:valid_len]]
                all_preds.append(pred_tags)
                all_labels.append(true_tags)

    f1 = seqeval_f1(all_labels, all_preds)

    if verbose:
        print(f"\nNER F1: {f1:.4f}")
        print(seqeval_report(all_labels, all_preds))

    return f1


def train_fold(
    model: NERModel,
    train_loader: DataLoader,
    val_loader: DataLoader,
    optimizer: AdamW,
    scheduler: object,
    ner_id2label: dict[int, str],
    device: torch.device,
    epochs: int,
    patience: int,
) -> float:
    """Train a single fold. Returns best NER F1."""
    best_f1 = 0.0
    patience_counter = 0
    best_state = None

    for epoch in range(epochs):
        loss = train_epoch(model, train_loader, optimizer, scheduler, device)
        f1 = evaluate(model, val_loader, ner_id2label, device)
        print(f"  Epoch {epoch + 1}/{epochs} — loss: {loss:.4f}, val F1: {f1:.4f}")

        if f1 > best_f1:
            best_f1 = f1
            patience_counter = 0
            best_state = copy.deepcopy(model.state_dict())
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"  Early stopping after {epoch + 1} epochs")
                break

    if best_state:
        model.load_state_dict(best_state)
    return best_f1


# ── Main ─────────────────────────────────────────────────────────────


def load_samples(data_file: str) -> list[dict]:
    """Load JSONL samples."""
    samples: list[dict] = []
    with open(data_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NER Model Training with 5-Fold CV",
    )
    parser.add_argument(
        "--task", type=str, required=True,
        choices=list(TASK_LABELS.keys()),
        help="Task name (determines label set)",
    )
    parser.add_argument("--data-file", type=str, required=True, help="JSONL data file")
    parser.add_argument("--output-dir", type=str, required=True, help="Output directory")
    parser.add_argument("--model-name", type=str, default="dicta-il/dictabert")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--dropout", type=float, default=0.3)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--early-stopping-patience", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-samples", type=int, default=None, help="Limit samples (for testing)")
    args = parser.parse_args()

    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    # Label mappings
    ner_label2id = TASK_LABELS[args.task]
    ner_id2label = {v: k for k, v in ner_label2id.items()}
    num_labels = len(ner_label2id)

    print(f"\n{'='*60}")
    print(f"NER Model Training: {args.task}")
    print(f"{'='*60}")
    print(f"Labels ({num_labels}): {list(ner_label2id.keys())}")
    print(f"Model: {args.model_name}")
    print(f"Data: {args.data_file}")
    print(f"Hyperparameters: lr={args.learning_rate}, bs={args.batch_size}, "
          f"epochs={args.epochs}, folds={args.n_folds}")
    print(f"{'='*60}\n")

    device = torch.device(
        "mps" if torch.backends.mps.is_available()
        else "cuda" if torch.cuda.is_available()
        else "cpu",
    )
    print(f"Device: {device}")

    tokenizer = AutoTokenizer.from_pretrained(args.model_name)

    # Load data
    all_samples = load_samples(args.data_file)
    if args.max_samples:
        all_samples = all_samples[:args.max_samples]
    print(f"Loaded {len(all_samples)} samples")

    # Stratification by entity count (balance single vs multi-entity)
    strat_labels = np.array([
        min(s.get("entity_count", 1), 3) for s in all_samples
    ])

    skf = StratifiedKFold(
        n_splits=args.n_folds, shuffle=True, random_state=args.seed,
    )
    os.makedirs(args.output_dir, exist_ok=True)

    fold_results: list[dict] = []

    for fold, (train_idx, val_idx) in enumerate(
        skf.split(all_samples, strat_labels),
    ):
        print(f"\n{'='*60}")
        print(f"FOLD {fold + 1}/{args.n_folds}")
        print(f"Train: {len(train_idx)}, Val: {len(val_idx)}")
        print(f"{'='*60}")

        train_samples = [all_samples[i] for i in train_idx]
        val_samples = [all_samples[i] for i in val_idx]

        train_ds = NERDataset(train_samples, tokenizer, ner_label2id, args.max_length)
        val_ds = NERDataset(val_samples, tokenizer, ner_label2id, args.max_length)

        train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=args.batch_size)

        model = NERModel(args.model_name, num_labels, args.dropout)
        model.to(device)

        optimizer = AdamW(
            model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay,
        )
        total_steps = len(train_loader) * args.epochs
        scheduler = get_linear_schedule_with_warmup(
            optimizer,
            num_warmup_steps=int(0.1 * total_steps),
            num_training_steps=total_steps,
        )

        best_f1 = train_fold(
            model, train_loader, val_loader, optimizer, scheduler,
            ner_id2label, device, args.epochs, args.early_stopping_patience,
        )

        # Final detailed evaluation
        print(f"\nFold {fold + 1} detailed results:")
        evaluate(model, val_loader, ner_id2label, device, verbose=True)

        fold_results.append({"fold": fold + 1, "ner_f1": best_f1})

        torch.save(
            {
                "fold": fold + 1,
                "model_state_dict": model.state_dict(),
                "ner_f1": best_f1,
                "task": args.task,
                "ner_label2id": ner_label2id,
            },
            f"{args.output_dir}/fold_{fold + 1}_model.pt",
        )
        print(f"Fold {fold + 1}: F1 = {best_f1:.4f}")

    # Summary
    f1_scores = [r["ner_f1"] for r in fold_results]
    mean_f1 = np.mean(f1_scores)
    std_f1 = np.std(f1_scores)

    print(f"\n{'='*60}")
    print(f"5-FOLD CROSS-VALIDATION RESULTS — {args.task.upper()}")
    print(f"{'='*60}")
    for r in fold_results:
        print(f"  Fold {r['fold']}: F1 = {r['ner_f1']:.4f}")
    print(f"\nMean F1: {mean_f1:.4f} +/- {std_f1:.4f}")
    print(f"Best fold: {np.argmax(f1_scores) + 1} (F1 = {max(f1_scores):.4f})")
    print(f"{'='*60}\n")

    summary = {
        "task": args.task,
        "base_model": args.model_name,
        "n_folds": args.n_folds,
        "total_samples": len(all_samples),
        "labels": list(ner_label2id.keys()),
        "fold_results": fold_results,
        "summary": {
            "mean_f1": float(mean_f1),
            "std_f1": float(std_f1),
            "best_fold": int(np.argmax(f1_scores) + 1),
            "best_f1": float(max(f1_scores)),
        },
    }
    with open(f"{args.output_dir}/kfold_results.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"Results saved to {args.output_dir}/kfold_results.json")


if __name__ == "__main__":
    main()
