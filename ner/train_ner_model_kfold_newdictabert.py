"""NER Model Training with 5-Fold Stratified Cross-Validation.

Experimental NeoDictaBERT version of the Hebrew manuscript NER trainer.
Keeps the original train_ner_model_kfold.py untouched while testing
dicta-il/neodictabert-bilingual with the same datasets and NER heads.

Supports training for:
- Provenance entities (OWNER, DATE, COLLECTION)
- Contents entities (WORK, FOLIO, WORK_AUTHOR)
- Any custom BIO tag set

Based on the Joint Entity-Role Model architecture from
Goldberg, Prebor & Elmalech (2025), simplified to single-head NER
(no role classification head needed when entity type IS the role).

Usage:
    python train_ner_model_kfold_newdictabert.py \\
        --task provenance \\
        --data-file processed-data/provenance_dataset.jsonl \\
        --output-dir provenance_model_kfold

    python train_ner_model_kfold_newdictabert.py \\
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
from transformers import (
    AutoConfig,
    AutoModelForTokenClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

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
    """Token-classification NER model using NeoDictaBERT's native head."""

    def __init__(
        self,
        bert_model_name: str,
        num_ner_labels: int,
        dropout: float = 0.3,
        trust_remote_code: bool = False,
        dtype: torch.dtype | None = None,
        attn_implementation: str | None = "sdpa",
        id2label: dict[int, str] | None = None,
        label2id: dict[str, int] | None = None,
    ) -> None:
        super().__init__()
        config = AutoConfig.from_pretrained(
            bert_model_name,
            trust_remote_code=trust_remote_code,
        )
        config.num_labels = num_ner_labels
        config.classifier_dropout = dropout
        if id2label is not None:
            config.id2label = id2label
        if label2id is not None:
            config.label2id = label2id
        if attn_implementation is not None:
            config._attn_implementation = attn_implementation

        model_kwargs: dict[str, object] = {
            "trust_remote_code": trust_remote_code,
            "ignore_mismatched_sizes": True,
            "config": config,
        }
        if dtype is not None:
            model_kwargs["dtype"] = dtype
        if attn_implementation is not None:
            model_kwargs["attn_implementation"] = attn_implementation
        self.model = AutoModelForTokenClassification.from_pretrained(
            bert_model_name,
            **model_kwargs,
        )
        refresh_neobert_rope_buffers(self.model)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        ner_labels: torch.Tensor | None = None,
    ) -> torch.Tensor | tuple[torch.Tensor]:
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=ner_labels,
        )
        if ner_labels is not None:
            return (outputs.loss,)
        return (outputs.logits,)


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
    gradient_accumulation_steps: int,
) -> float:
    """Train for one epoch, return average loss."""
    model.train()
    total_loss = 0.0
    optimizer.zero_grad()

    for step, batch in enumerate(tqdm(dataloader, desc="Training", leave=False), start=1):
        input_ids = batch["input_ids"].to(device)
        attention_mask = batch["attention_mask"].to(device)
        ner_labels = batch["ner_labels"].to(device)

        (loss,) = model(input_ids, attention_mask, ner_labels)
        if not torch.isfinite(loss):
            raise RuntimeError(
                "Non-finite training loss detected. "
                f"loss={loss.item()}, device={device}, "
                f"input_shape={tuple(input_ids.shape)}",
            )
        (loss / gradient_accumulation_steps).backward()

        should_step = (
            step % gradient_accumulation_steps == 0
            or step == len(dataloader)
        )
        if should_step:
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad()

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
            if not torch.isfinite(ner_logits).all():
                raise RuntimeError(
                    "Non-finite evaluation logits detected. "
                    f"device={device}, input_shape={tuple(input_ids.shape)}",
                )
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
    gradient_accumulation_steps: int,
) -> float:
    """Train a single fold. Returns best NER F1."""
    best_f1 = 0.0
    patience_counter = 0
    best_state = None

    for epoch in range(epochs):
        loss = train_epoch(
            model,
            train_loader,
            optimizer,
            scheduler,
            device,
            gradient_accumulation_steps,
        )
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


def resolve_dtype(name: str) -> torch.dtype | None:
    """Resolve an optional model load dtype from the CLI."""
    if name == "default":
        return None
    if name == "float32":
        return torch.float32
    if name == "bfloat16":
        return torch.bfloat16
    if name == "float16":
        return torch.float16
    raise ValueError(f"Unsupported dtype: {name}")


def precompute_rope_freqs(
    dim: int,
    end: int,
    theta: float = 10_000.0,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Recompute NeoBERT rotary embedding buffers in stable float32."""
    half_dim = dim // 2
    idx = torch.arange(0, half_dim, dtype=torch.float32)
    inv_freq = 1.0 / (theta ** ((2.0 * idx) / dim))
    positions = torch.arange(end, dtype=torch.float32)
    angles = torch.outer(positions, inv_freq)
    return angles.cos(), angles.sin()


def refresh_neobert_rope_buffers(model: nn.Module) -> None:
    """Refresh NeoDictaBERT non-persistent RoPE buffers after HF loading.

    The model's custom code registers ``freqs_cos`` and ``freqs_sin`` as
    non-persistent buffers. In some local Transformers loading paths these
    buffers can survive as uninitialized garbage, producing NaNs in the first
    attention layer. Recomputing them from the config makes training stable.
    """
    base_model = getattr(model, "model", model)
    if not all(hasattr(base_model, attr) for attr in ("freqs_cos", "freqs_sin")):
        return

    config = base_model.config
    dim = config.hidden_size // config.num_attention_heads
    max_length = config.max_length
    cos, sin = precompute_rope_freqs(dim, max_length)
    base_model.freqs_cos = cos
    base_model.freqs_sin = sin


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NeoDictaBERT NER Model Training with 5-Fold CV",
    )
    parser.add_argument(
        "--task", type=str, required=True,
        choices=list(TASK_LABELS.keys()),
        help="Task name (determines label set)",
    )
    parser.add_argument("--data-file", type=str, required=True, help="JSONL data file")
    parser.add_argument("--output-dir", type=str, required=True, help="Output directory")
    parser.add_argument(
        "--model-name",
        type=str,
        default="dicta-il/neodictabert-bilingual",
    )
    parser.add_argument(
        "--trust-remote-code",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow custom Hugging Face model code, required by NeoDictaBERT.",
    )
    parser.add_argument(
        "--torch-dtype",
        choices=["default", "float32", "bfloat16", "float16"],
        default="float32",
        help="Optional dtype for loading the encoder weights.",
    )
    parser.add_argument(
        "--attn-implementation",
        choices=["default", "sdpa", "eager"],
        default="sdpa",
        help="Attention backend for NeoDictaBERT. Use sdpa by default; eager is a debugging fallback.",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "mps", "cuda", "cpu"],
        default="auto",
        help="Training device. Use auto for MPS -> CUDA -> CPU.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Micro-batch size. Keep at 1 for NeoDictaBERT stability; use gradient accumulation for larger effective batches.",
    )
    parser.add_argument(
        "--gradient-accumulation-steps",
        type=int,
        default=1,
        help="Accumulate gradients across N micro-batches before an optimizer step.",
    )
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--dropout", type=float, default=0.3)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--early-stopping-patience", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-samples", type=int, default=None, help="Limit samples (for testing)")
    parser.add_argument(
        "--fold-limit",
        type=int,
        default=None,
        help="Run only the first N folds for smoke tests.",
    )
    parser.add_argument(
        "--skip-save-models",
        action="store_true",
        help="Do not write fold checkpoint files, useful for compatibility smoke tests.",
    )
    args = parser.parse_args()
    if args.gradient_accumulation_steps < 1:
        raise ValueError("--gradient-accumulation-steps must be >= 1")

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
    effective_batch_size = args.batch_size * args.gradient_accumulation_steps
    print(f"Hyperparameters: lr={args.learning_rate}, bs={args.batch_size}, "
          f"grad_accum={args.gradient_accumulation_steps}, "
          f"effective_bs={effective_batch_size}, epochs={args.epochs}, "
          f"folds={args.n_folds}")
    print(f"{'='*60}\n")

    if args.device == "auto":
        device = torch.device(
            "mps" if torch.backends.mps.is_available()
            else "cuda" if torch.cuda.is_available()
            else "cpu",
        )
    else:
        device = torch.device(args.device)
    print(f"Device: {device}")

    dtype = resolve_dtype(args.torch_dtype)
    attn_implementation = (
        None if args.attn_implementation == "default" else args.attn_implementation
    )
    tokenizer = AutoTokenizer.from_pretrained(
        args.model_name,
        trust_remote_code=args.trust_remote_code,
    )

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

        model = NERModel(
            args.model_name,
            num_labels,
            args.dropout,
            trust_remote_code=args.trust_remote_code,
            dtype=dtype,
            attn_implementation=attn_implementation,
            id2label=ner_id2label,
            label2id=ner_label2id,
        )
        model.to(device)

        optimizer = AdamW(
            model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay,
        )
        optimizer_steps_per_epoch = int(np.ceil(
            len(train_loader) / args.gradient_accumulation_steps,
        ))
        total_steps = optimizer_steps_per_epoch * args.epochs
        scheduler = get_linear_schedule_with_warmup(
            optimizer,
            num_warmup_steps=int(0.1 * total_steps),
            num_training_steps=total_steps,
        )

        best_f1 = train_fold(
            model, train_loader, val_loader, optimizer, scheduler,
            ner_id2label, device, args.epochs, args.early_stopping_patience,
            args.gradient_accumulation_steps,
        )

        # Final detailed evaluation
        print(f"\nFold {fold + 1} detailed results:")
        evaluate(model, val_loader, ner_id2label, device, verbose=True)

        fold_results.append({"fold": fold + 1, "ner_f1": best_f1})

        if not args.skip_save_models:
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

        if args.fold_limit is not None and fold + 1 >= args.fold_limit:
            print(f"Stopping early after {args.fold_limit} fold(s).")
            break

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
        "trust_remote_code": args.trust_remote_code,
        "torch_dtype": args.torch_dtype,
        "attn_implementation": args.attn_implementation,
        "device": str(device),
        "batch_size": args.batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "effective_batch_size": args.batch_size * args.gradient_accumulation_steps,
        "fold_limit": args.fold_limit,
        "skip_save_models": args.skip_save_models,
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
