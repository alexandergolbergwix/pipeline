"""Generic NER inference pipeline for provenance and contents models.

Loads a trained NERModel checkpoint and extracts entities from Hebrew text.
Supports any BIO tag set (provenance: OWNER/DATE/COLLECTION;
contents: WORK/FOLIO/WORK_AUTHOR).

Usage::

    from ner.ner_inference_pipeline import NERInferencePipeline

    pipeline = NERInferencePipeline(
        model_path="ner/provenance_model_kfold/fold_1_model.pt",
    )
    entities = pipeline.process_text('ציון בעלים: "יעקב בן שלמה"')
    # [{"text": "יעקב בן שלמה", "type": "OWNER", "start": 14, "end": 27, "confidence": 0.92}]
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class NERInferencePipeline:
    """Generic NER inference pipeline for trained NERModel checkpoints."""

    _BASE_MODEL = "dicta-il/dictabert"
    _DROPOUT = 0.3

    def __init__(self, model_path: str, device: str = "auto") -> None:
        import torch  # noqa: PLC0415
        from transformers import AutoTokenizer  # noqa: PLC0415

        # Ensure ner/ is importable
        ner_dir = str(Path(__file__).parent)
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from train_ner_model_kfold import NERModel  # noqa: PLC0415

        # Device selection: MPS → CUDA → CPU
        if device == "auto":
            _dev = (
                "mps" if torch.backends.mps.is_available()
                else "cuda" if torch.cuda.is_available()
                else "cpu"
            )
            self.device = torch.device(_dev)
        else:
            self.device = torch.device(device)

        # Load checkpoint to get task metadata
        weights_path = self._resolve_weights(model_path)
        logger.info("Loading NER model from %s", weights_path)
        checkpoint = torch.load(
            weights_path, map_location=self.device, weights_only=False,
        )

        # Extract label mapping from checkpoint
        self.ner_label2id: dict[str, int] = checkpoint.get("ner_label2id", {})
        self.task: str = checkpoint.get("task", "unknown")
        if not self.ner_label2id:
            raise ValueError(
                f"Checkpoint {weights_path} missing 'ner_label2id'. "
                "Re-train with the updated train_ner_model_kfold.py.",
            )
        self.ner_id2label: dict[int, str] = {v: k for k, v in self.ner_label2id.items()}
        num_labels = len(self.ner_label2id)

        # Load tokenizer
        base_model = os.environ.get("MHM_BUNDLED_DICTABERT", self._BASE_MODEL)
        self.tokenizer = AutoTokenizer.from_pretrained(base_model)

        # Build model and load weights
        self.model = NERModel(
            bert_model_name=base_model,
            num_ner_labels=num_labels,
            dropout=self._DROPOUT,
        )
        state_dict = checkpoint.get("model_state_dict", checkpoint)
        head_only = checkpoint.get("head_only", False)
        if head_only:
            # Head-only checkpoint: load NER head weights on top of fresh DictaBERT
            self.model.load_state_dict(state_dict, strict=False)
        else:
            self.model.load_state_dict(state_dict)
        self.model.to(self.device)
        self.model.eval()

        logger.info(
            "NER pipeline ready: task=%s, labels=%d, device=%s, head_only=%s",
            self.task, num_labels, self.device, head_only,
        )

    @classmethod
    def from_shared_base(
        cls, bert_model: object, tokenizer: object,
        checkpoint_path: str, device: str = "auto",
    ) -> "NERInferencePipeline":
        """Create pipeline sharing an existing DictaBERT encoder.

        Avoids loading DictaBERT multiple times when running several NER
        models. The shared bert_model's parameters are copied into the new
        NERModel, so each model still has independent NER head weights.
        """
        import torch  # noqa: PLC0415

        ner_dir = str(Path(__file__).parent)
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from train_ner_model_kfold import NERModel  # noqa: PLC0415

        instance = cls.__new__(cls)

        if device == "auto":
            _dev = (
                "mps" if torch.backends.mps.is_available()
                else "cuda" if torch.cuda.is_available()
                else "cpu"
            )
            instance.device = torch.device(_dev)
        else:
            instance.device = torch.device(device)

        instance.tokenizer = tokenizer

        weights_path = cls._resolve_weights(checkpoint_path)
        checkpoint = torch.load(
            weights_path, map_location=instance.device, weights_only=False,
        )
        instance.ner_label2id = checkpoint.get("ner_label2id", {})
        instance.task = checkpoint.get("task", "unknown")
        instance.ner_id2label = {v: k for k, v in instance.ner_label2id.items()}
        num_labels = len(instance.ner_label2id)

        # Build model with a dummy base (will be replaced)
        base_name = os.environ.get("MHM_BUNDLED_DICTABERT", cls._BASE_MODEL)
        instance.model = NERModel(base_name, num_labels, cls._DROPOUT)

        # Share the BERT encoder from the existing model
        instance.model.bert = bert_model

        # Load NER head weights
        state_dict = checkpoint.get("model_state_dict", checkpoint)
        instance.model.load_state_dict(state_dict, strict=False)
        instance.model.to(instance.device)
        instance.model.eval()

        logger.info(
            "NER pipeline (shared base) ready: task=%s, labels=%d",
            instance.task, num_labels,
        )
        return instance

    @staticmethod
    def _resolve_weights(model_path: str) -> str:
        """Resolve model path to a local weights file."""
        local = Path(model_path)
        if local.is_file():
            return str(local)
        if local.is_dir():
            # Pick the best fold model
            for name in ("pytorch_model.bin", "best_model.pt"):
                candidate = local / name
                if candidate.exists():
                    return str(candidate)
            # Find fold models and pick the one with highest fold number
            fold_files = sorted(local.glob("fold_*_model.pt"))
            if fold_files:
                return str(fold_files[-1])
            raise FileNotFoundError(f"No model weights in {model_path}")
        raise FileNotFoundError(f"Model path not found: {model_path}")

    def process_text(self, text: str) -> list[dict[str, Any]]:
        """Extract entities from Hebrew text.

        Returns list of dicts with keys: text, type, start, end, confidence.
        """
        import torch  # noqa: PLC0415

        tokens = text.split()
        if not tokens:
            return []

        encoding = self.tokenizer(
            tokens,
            is_split_into_words=True,
            return_tensors="pt",
            truncation=True,
            max_length=256,
        )
        input_ids = encoding["input_ids"].to(self.device)
        attention_mask = encoding["attention_mask"].to(self.device)

        with torch.no_grad():
            (ner_logits,) = self.model(input_ids, attention_mask)

        # Get predictions and confidence
        probs = torch.softmax(ner_logits[0], dim=-1)
        preds = torch.argmax(probs, dim=-1).cpu().tolist()
        max_probs = probs.max(dim=-1).values.cpu().tolist()

        # Align subword predictions to word level
        word_ids = encoding.word_ids(batch_index=0)
        aligned_preds: list[int] = []
        aligned_confs: list[float] = []
        prev_word_id = None
        for i, word_id in enumerate(word_ids):
            if word_id is None:
                continue
            if word_id != prev_word_id:
                aligned_preds.append(preds[i])
                aligned_confs.append(max_probs[i])
            prev_word_id = word_id

        # Build entity spans from BIO tags
        entities: list[dict[str, Any]] = []
        current_tokens: list[str] = []
        current_type: str = ""
        current_start: int = 0
        current_confs: list[float] = []
        search_from: int = 0

        def _flush() -> None:
            if current_tokens:
                entity_text = " ".join(current_tokens)
                avg_conf = sum(current_confs) / len(current_confs)
                entities.append({
                    "text": entity_text,
                    "type": current_type,
                    "start": current_start,
                    "end": current_start + len(entity_text),
                    "confidence": round(avg_conf, 4),
                })

        for idx, (token, pred, conf) in enumerate(
            zip(tokens, aligned_preds, aligned_confs),
        ):
            label = self.ner_id2label.get(pred, "O")

            if label.startswith("B-"):
                _flush()
                current_tokens = [token]
                current_type = label[2:]  # e.g., "OWNER"
                current_start = text.find(token, search_from)
                current_confs = [conf]
                search_from = current_start + len(token) if current_start >= 0 else search_from
            elif label.startswith("I-") and current_tokens and label[2:] == current_type:
                current_tokens.append(token)
                current_confs.append(conf)
            else:
                _flush()
                current_tokens = []
                current_type = ""
                current_confs = []
                if idx < len(tokens):
                    pos = text.find(token, search_from)
                    if pos >= 0:
                        search_from = pos + len(token)

        _flush()
        return entities

    def process_batch(self, texts: list[str]) -> list[list[dict[str, Any]]]:
        """Process multiple texts sequentially."""
        return [self.process_text(t) for t in texts]
