"""DictaBERT-based multi-label genre classification head.

Defines GenreClassificationModel only — no training logic.
Imported by both train_genre_classifier.py and the inference wrapper
(converter/authority/genre_classifier.py) so the class is available in
the app bundle without bundling the full training script.
"""

from __future__ import annotations

import torch
import torch.nn as nn
from transformers import AutoModel


class GenreClassificationModel(nn.Module):
    """Partially fine-tuned DictaBERT + multi-label linear classification head.

    Bottom `freeze_layers` transformer layers are frozen (they encode generic
    Hebrew syntax). Top layers + head are fine-tuned with differential LRs.
    """

    def __init__(
        self,
        bert_model_name: str,
        num_genres: int,
        dropout: float = 0.3,
        freeze_layers: int = 8,
    ) -> None:
        super().__init__()
        self.bert = AutoModel.from_pretrained(bert_model_name)
        for param in self.bert.embeddings.parameters():
            param.requires_grad = False
        for layer in self.bert.encoder.layer[:freeze_layers]:
            for param in layer.parameters():
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
        return self.classifier(self.dropout(cls))
