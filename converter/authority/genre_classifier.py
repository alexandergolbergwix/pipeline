"""Genre classifier inference wrapper.

Loads a trained GenreClassificationModel checkpoint and predicts genres
from manuscript title + notes text. Used as a fallback in item_builder.py
when a record has no MARC 655 genre/form headings.

The model was trained via distant supervision (see ner/train_genre_classifier.py):
MARC 655 labels supervised a frozen DictaBERT encoder + linear head on MARC
245 title + 500 notes text.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BASE_MODEL = "dicta-il/dictabert"


class GenreClassifier:
    """Multi-label genre classifier for Hebrew manuscripts."""

    def __init__(self, model_path: str, device: str = "auto") -> None:
        import torch  # noqa: PLC0415
        from transformers import AutoTokenizer  # noqa: PLC0415

        ner_dir = str(Path(__file__).resolve().parent.parent.parent / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from genre_classifier_model import GenreClassificationModel  # noqa: PLC0415

        if device == "auto":
            _dev = (
                "mps" if torch.backends.mps.is_available()
                else "cuda" if torch.cuda.is_available()
                else "cpu"
            )
            self.device = torch.device(_dev)
        else:
            self.device = torch.device(device)

        checkpoint: dict[str, Any] = torch.load(
            model_path, map_location=self.device, weights_only=False,
        )

        self.genre_label2id: dict[str, int] = checkpoint["genre_label2id"]
        self.genre_id2label: dict[int, str] = {v: k for k, v in self.genre_label2id.items()}
        self.threshold: float = checkpoint.get("threshold", 0.5)
        self.max_length: int = checkpoint.get("max_length", 64)
        num_genres: int = checkpoint.get("num_genres", len(self.genre_label2id))

        base_model = os.environ.get("MHM_BUNDLED_DICTABERT", _BASE_MODEL)
        self.tokenizer = AutoTokenizer.from_pretrained(base_model)

        self.model = GenreClassificationModel(base_model, num_genres)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.to(self.device)
        self.model.eval()

        logger.info(
            "GenreClassifier ready: %d labels, threshold=%.2f, device=%s",
            num_genres, self.threshold, self.device,
        )

    def predict(self, title: str, notes: list[str]) -> list[tuple[str, float]]:
        """Return list of (genre_str, confidence) for genres above threshold.

        For texts longer than the training max_length (64 tokens), a sliding
        window over the token sequence is used: each window is scored
        independently and the probabilities are averaged across windows.
        This prevents information loss from simple truncation.

        Returns an empty list when:
        - The NOTA class is predicted (manuscript genre is outside the trained vocabulary), or
        - No genre exceeds the confidence threshold.

        Args:
            title: Manuscript title (MARC 245).
            notes: List of general note texts (MARC 500).

        Returns:
            List of (genre_key, confidence) tuples matching GENRE_TO_QID keys,
            sorted by confidence descending. Empty list means "abstain".
        """
        import torch  # noqa: PLC0415

        text = (title + " " + " ".join(str(n) for n in notes[:3])).strip()
        if not text:
            return []

        max_length: int = self.max_length
        stride: int = max_length // 2  # 50% overlap between windows

        # Tokenize without truncation to get full token sequence
        enc = self.tokenizer(
            text,
            truncation=False,
            return_tensors="pt",
        )
        input_ids_full = enc["input_ids"][0]  # (total_tokens,)
        attn_full = enc["attention_mask"][0]
        total_tokens = input_ids_full.size(0)

        if total_tokens <= max_length:
            # Short text: single padded inference (common case for 3-sentence windows)
            enc_padded = self.tokenizer(
                text,
                max_length=max_length,
                truncation=True,
                padding="max_length",
                return_tensors="pt",
            )
            windows_ids = enc_padded["input_ids"]
            windows_mask = enc_padded["attention_mask"]
        else:
            # Long text: sliding window with stride, pad last window
            ids_list: list[torch.Tensor] = []
            mask_list: list[torch.Tensor] = []
            start = 0
            while start < total_tokens:
                end = min(start + max_length, total_tokens)
                chunk_ids = input_ids_full[start:end]
                chunk_mask = attn_full[start:end]
                # Pad to max_length if shorter
                pad_len = max_length - chunk_ids.size(0)
                if pad_len > 0:
                    pad_id = self.tokenizer.pad_token_id or 0
                    chunk_ids = torch.cat([chunk_ids, torch.full((pad_len,), pad_id)])
                    chunk_mask = torch.cat([chunk_mask, torch.zeros(pad_len, dtype=torch.long)])
                ids_list.append(chunk_ids)
                mask_list.append(chunk_mask)
                if end >= total_tokens:
                    break
                start += stride
            windows_ids = torch.stack(ids_list)    # (n_windows, max_length)
            windows_mask = torch.stack(mask_list)

        windows_ids = windows_ids.to(self.device)
        windows_mask = windows_mask.to(self.device)

        with torch.no_grad():
            logits = self.model(windows_ids, windows_mask)          # (n_windows, n_classes)
            probs_per_window = torch.sigmoid(logits).cpu()          # (n_windows, n_classes)
            probs = probs_per_window.mean(dim=0).tolist()           # average across windows

        nota_label = "__NOTA__"
        nota_idx = next(
            (i for i, lbl in self.genre_id2label.items() if lbl == nota_label), None
        )
        nota_prob = probs[nota_idx] if nota_idx is not None else 0.0

        results = [
            (self.genre_id2label[i], round(p, 4))
            for i, p in enumerate(probs)
            if p >= self.threshold and self.genre_id2label[i] != nota_label
        ]
        results = sorted(results, key=lambda x: x[1], reverse=True)

        if not results:
            return [("other", round(nota_prob, 4))]
        return results
