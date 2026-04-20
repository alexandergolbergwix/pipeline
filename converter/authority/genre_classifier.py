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

        # Make train_genre_classifier importable for GenreClassificationModel
        ner_dir = str(Path(__file__).resolve().parent.parent.parent / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from train_genre_classifier import GenreClassificationModel  # noqa: PLC0415

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

        Args:
            title: Manuscript title (MARC 245).
            notes: List of general note texts (MARC 500).

        Returns:
            List of (genre_key, confidence) tuples matching GENRE_TO_QID keys,
            sorted by confidence descending.
        """
        import torch  # noqa: PLC0415

        text = (title + " " + " ".join(str(n) for n in notes[:3])).strip()
        if not text:
            return []

        enc = self.tokenizer(
            text,
            max_length=256,
            truncation=True,
            padding="max_length",
            return_tensors="pt",
        )
        input_ids = enc["input_ids"].to(self.device)
        attention_mask = enc["attention_mask"].to(self.device)

        with torch.no_grad():
            logits = self.model(input_ids, attention_mask)
            probs = torch.sigmoid(logits[0]).cpu().tolist()

        results = [
            (self.genre_id2label[i], round(p, 4))
            for i, p in enumerate(probs)
            if p >= self.threshold
        ]
        return sorted(results, key=lambda x: x[1], reverse=True)
