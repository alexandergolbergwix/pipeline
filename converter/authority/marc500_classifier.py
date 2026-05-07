"""MARC 500 colophon sentence classifier inference wrapper.

Classifies individual MARC 500 note sentences as COLOPHON or not.
Used in NerWorker to route colophon sentences to record["colophon_text"],
which feeds P1684 (inscription) in Wikidata.

Model: DictaBERT [CLS] → Dropout(0.3) → Linear(768 → 1) → sigmoid
Single binary head; threshold tuned on the validation set during training.

Absent checkpoint → graceful degradation (returns False, falls back to
keyword rules in field_handlers.py).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BASE_MODEL = "dicta-il/dictabert"
_LABEL2ID = {"COLOPHON": 0}

# Hebrew ownership / acquisition vocabulary that flags a MARC 500
# sentence as describing provenance. Same set lives at
# ``scripts/extract_marc500_sentences.py:_PROVENANCE_KEYWORDS`` (the
# labels for the training corpus). The trained checkpoint is
# single-head (COLOPHON only); this set drives the deterministic
# fallback used by :meth:`Marc500Classifier.is_provenance`.
_PROVENANCE_KEYWORDS: frozenset[str] = frozenset({
    # acquisition
    "קנה", "קניתי", "נרכש", "רכשתי",
    # ownership
    "שייך", "שייכת",
    "בעלות", "בבעלות",
    # sale
    "נמכר", "מכרתי", "נמכרה",
    # signatures / inscriptions of ownership
    "חתמתי", "חתם",
    # inheritance
    "ירשתי", "ירש", "בירושה",
    "ממורשתי", "מורשה",
    # marriage / dowry transfers
    "מוהר", "נדוניה",
    # gift transfers
    "מתנה", "כמתנה", "הוענק",
    # written-for / commissioned-by
    "נכתב עבור", "נכתב בשביל",
    "עבור",
})

# Confidence assigned to a heuristic match. We deliberately set this
# below the COLOPHON threshold so that callers can distinguish
# "model said yes (0.6+)" from "keyword fallback said yes (0.55)".
_PROVENANCE_HEURISTIC_CONF: float = 0.55
_PROVENANCE_HEURISTIC_THRESHOLD: float = 0.5


class Marc500Classifier:
    """Sentence-level binary classifier for MARC 500 colophon detection."""

    def __init__(self, model_path: str, device: str = "auto") -> None:
        import torch  # noqa: PLC0415
        from transformers import AutoTokenizer  # noqa: PLC0415

        ner_dir = str(Path(__file__).resolve().parent.parent.parent / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)
        from marc500_sentence_model import GenreClassificationModel  # noqa: PLC0415

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

        self.label2id: dict[str, int] = checkpoint.get("label2id", _LABEL2ID)
        raw_thr = checkpoint.get("threshold", 0.65)
        # threshold may be stored as float (new) or dict (old multi-class format)
        if isinstance(raw_thr, dict):
            self.threshold: float = raw_thr.get("COLOPHON", 0.65)
        else:
            self.threshold = float(raw_thr)
        self.max_length: int = checkpoint.get("max_length", 64)
        num_classes: int = checkpoint.get("num_classes", 1)

        base_model = os.environ.get("MHM_BUNDLED_DICTABERT", _BASE_MODEL)
        self.tokenizer = AutoTokenizer.from_pretrained(base_model)

        self.model = GenreClassificationModel(base_model, num_classes)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.to(self.device)
        self.model.eval()

        logger.info(
            "Marc500Classifier ready: threshold=%.2f device=%s", self.threshold, self.device,
        )

    def is_colophon(self, sentence: str) -> tuple[bool, float]:
        """Return (above_threshold, confidence) for the input sentence."""
        import torch  # noqa: PLC0415

        text = sentence.strip()
        if not text:
            return (False, 0.0)

        enc = self.tokenizer(
            text,
            max_length=self.max_length,
            truncation=True,
            padding="max_length",
            return_tensors="pt",
        )
        input_ids = enc["input_ids"].to(self.device)
        attn_mask = enc["attention_mask"].to(self.device)

        with torch.no_grad():
            logits = self.model(input_ids, attn_mask)  # (1, 1)
            conf = round(float(torch.sigmoid(logits).cpu().squeeze()), 4)

        return (conf >= self.threshold, conf)

    def is_provenance(self, sentence: str) -> tuple[bool, float]:
        """Return ``(above_threshold, confidence)`` for provenance routing.

        Mirrors the shape of :meth:`is_colophon` so callers can treat
        both heads uniformly. The current checkpoint is single-head
        (COLOPHON only); this method runs a deterministic Hebrew-
        vocabulary check (:data:`_PROVENANCE_KEYWORDS`) and reports
        :data:`_PROVENANCE_HEURISTIC_CONF` on a hit. The heuristic
        confidence is below the COLOPHON model's threshold so callers
        can distinguish "model fired" from "keyword fired".
        """
        text = (sentence or "").strip()
        if not text:
            return (False, 0.0)
        haystack = text.lower()
        if any(kw in haystack for kw in _PROVENANCE_KEYWORDS):
            return (True, _PROVENANCE_HEURISTIC_CONF)
        return (False, 0.0)

    def classify_sentence(self, sentence: str) -> dict[str, tuple[bool, float]]:
        """Return both heads' results in one call."""
        return {
            "COLOPHON": self.is_colophon(sentence),
            "PROVENANCE": self.is_provenance(sentence),
        }
