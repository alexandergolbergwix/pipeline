"""Genre Classifier Training with 5-Fold Stratified CV.

Distant supervision strategy:
  - Labels come from MARC 655 genre/form headings.
  - Training samples require MARC 245 (title), 500 (notes), AND 655 (genres).
  - A record is only used if at least one genre keyword (Hebrew or English
    synonym) appears in the MARC 500 notes — confirming the cataloger described
    the genre explicitly in free text.
  - X = MARC 245 title + the 3-sentence window in MARC 500 around the keyword
    mention (sentence before, matching sentence, sentence after).
  - Y = the MARC 655 genre label(s) for that record.

Architecture:
  - Encoder: dicta-il/dictabert warm-started from NER checkpoint (domain-adapted)
  - Head: Dropout(0.3) → Linear(768, num_genres) → sigmoid
  - Loss: BCEWithLogitsLoss (multi-label, per-class pos_weight)
  - Metric: micro-F1 at threshold 0.5

Usage:
    PYTHONPATH=src:. .venv/bin/python ner/train_genre_classifier.py
"""

from __future__ import annotations

import argparse
import os
import re
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

# Sentinel label for "not one of the top genres" — allows the model to abstain.
NOTA_LABEL: str = "__NOTA__"

# Will be populated after data loading: top genres + NOTA at the last index.
GENRE_LABELS: list[str] = []
NUM_GENRES: int = 0
GENRE_TO_IDX: dict[str, int] = {}

# Hebrew + English keyword synonyms (all morphological forms) for each MARC 655
# genre. A record is included in training only if ≥1 keyword appears in its
# MARC 500 notes. The matching sentence + context become the training input X.
GENRE_KEYWORDS: dict[str, list[str]] = {
    "Piyyutim": [
        "פיוט", "פיוטים", "פיוטי", "פייטן", "פייטנים",
        "יוצר", "יוצרות", "קינה", "קינות", "סליחה", "סליחות",
        "הושענה", "הושענות", "אזהרה", "אזהרות", "זמירה", "זמירות",
        "גשם", "טל", "עקדה", "עקדות", "קרובה", "קרובות",
        "piyyut", "piyyutim", "liturgical poetry", "selichot", "kinot",
    ],
    "Poetry": [
        "שיר", "שירים", "שירה", "שירות", "שירי", "שורה", "שורות",
        "חרוז", "חרוזים", "בית", "בתים", "מחרוזת", "מחרוזות",
        "סונטה", "אודה", "אלגיה", "אפיגרמה", "ספר שירים",
        "poem", "poems", "poetry", "verse", "verses", "stanza", "ode",
    ],
    "Illustrated works (Manuscript)": [
        "ציור", "ציורים", "ציורי", "מאויר", "מאוייר", "מאויירת",
        "איור", "איורים", "תמונה", "תמונות", "מינייאטורה", "מיניאטורה",
        "עיטור", "עיטורים", "קישוט", "קישוטים", "ראשי פרשיות",
        "ראשי פרק", "ראשי תיבות מצוירים", "כרטיסיה מצוירת",
        "דקורציה", "פרונטיספיס", "אות מעוטרת", "אותיות מעוטרות",
        "illuminated", "illustrated", "illustration", "miniature",
        "decoration", "ornament", "gilded", "gold leaf",
    ],
    "Personal correspondence": [
        "מכתב", "מכתבים", "אגרת", "אגרות", "כתב", "כתבים",
        "עלון", "תכתובת", "התכתבות", "שליחת", "נשלח",
        "letter", "letters", "correspondence", "epistle", "epistles",
    ],
    "Autograph manuscripts": [
        "בכתב ידו", "בכתב יד המחבר", "בכתב ידי המחבר", "בכתב יד הרב",
        "בכתב יד הגאון", "אוטוגרף", "כתיבת המחבר", "כתב יד המחבר",
        "חתימת המחבר", "חתימת ידו", "בעצם ידו", "בידי המחבר",
        "autograph", "holograph", "author's hand", "own hand", "autographed",
    ],
    "Censored manuscripts": [
        "צנזורה", "צנזר", "צנזרה", "מצונזר", "מצונזרת",
        "מחיקה", "מחיקות", "נמחק", "נמחקו", "מחוק", "מחוקה",
        "השחרה", "השחרות", "הושחר", "הושחרו",
        "חסר", "חסרים", "נגרע", "נגרעו", "הושמט", "הושמטו",
        "שינוי", "שונה", "שונו", "תיקון צנזורה",
        "censored", "censorship", "expurgated", "deleted", "erased",
        "crossed out", "blotted out", "obliterated",
    ],
    "Literature (Miscellaneous, in manuscript)": [
        "ספרות", "ספרותי", "ספרותית", "יצירה", "יצירות",
        "קובץ", "אסופה", "כתבים שונים", "ספרות מגוונת",
        "ספרות שונה", "ליקוטים", "ספר מגוון",
        "literature", "literary", "miscellaneous", "collection",
    ],
    "Records (Documents)": [
        "רשימה", "רשימות", "תעודה", "תעודות", "מסמך", "מסמכים",
        "שטר", "שטרות", "ארכיון", "כתבי ארכיון", "עדות", "עדויות",
        "רישום", "רישומים", "קינין", "כתב ידי עד",
        "record", "records", "document", "documents", "deed", "deeds",
        "archival", "register",
    ],
    "Family records": [
        "משפחה", "משפחות", "רישומי משפחה", "תולדות משפחה",
        "ייחוס", "יחוס", "יחוסין", "גנאולוגיה", "גנאלוגיה",
        "ספר משפחה", "ספר הייחוס", "דברי הימים של משפחה",
        "שמות המשפחה", "רשימת בני משפחה", "זכרון אבות",
        "family", "genealogy", "lineage", "family tree", "ancestry",
    ],
    "Bibliographies": [
        "ביבליוגרפיה", "ביבליוגרפי", "רשימת ספרים", "רשימת מקורות",
        "ספר ספרים", "מראי מקום", "מפתח", "אינדקס", "רשימת ספרות",
        "bibliography", "bibliographic", "list of books", "index",
        "catalogue", "catalog",
    ],
    "Negotiable instruments": [
        "שטר חוב", "שטרות חוב", "שטר כסף", "שטרי כסף",
        "שטר חליפין", "שטרי חליפין", "ממסר", "שטר פקדון",
        "שטר מסחר", "שטרי מסחר", "שטר בנקאי",
        "negotiable", "bill of exchange", "promissory note",
        "bond", "banknote", "note of hand",
    ],
    "Tales": [
        "סיפור", "סיפורים", "מעשה", "מעשיות", "אגדה", "אגדות",
        "חכמות", "פתגם", "פתגמים", "נובלה", "אפוס", "פבולה",
        "מסופר", "מספר", "ספור", "ספורים",
        "tale", "tales", "story", "stories", "fable", "fables",
        "narrative", "legend", "anecdote",
    ],
    "Community records (Manuscript)": [
        "פנקס", "פנקסים", "פנקס קהילה", "פנקסי קהילה",
        "קהילה", "קהל", "ספר הקהל", "ספר הקהלה",
        "חברה", "חברות", "גמ\"ח", "חברה קדישא",
        "רישומי קהילה", "ספרי קהל", "מנהגי קהל",
        "pinkas", "community records", "communal", "kehilla",
        "community book", "minute book",
    ],
    "Riddles": [
        "חידה", "חידות", "חידתי", "פתרון", "פתרונות",
        "שאלה", "שאלות", "בעיה", "בעיות", "תשאלות",
        "enigma", "riddle", "riddles", "puzzle", "puzzles",
    ],
    "Legislation (Jewish law)": [
        "תקנה", "תקנות", "תקנן", "תוקן", "תיקון תקנה",
        "חוק", "חוקים", "דין", "דינים", "פסק", "פסקי דין",
        "הוראה", "הוראות", "גזרה", "גזרות", "חרם", "חרמות",
        "צו", "צווים", "ביה\"ד", "בית דין", "ספר תקנות",
        "legislation", "regulation", "regulations", "takkanot",
        "takkanah", "decree", "enactment", "ordinance", "statute",
    ],
    "Registers of births, etc.": [
        "לידה", "לידות", "לידת", "ברית", "ברית מילה", "מוהל",
        "נישואים", "נישואין", "חופה", "הכנסה לחופה",
        "פטירה", "פטירות", "תאריך פטירה", "יום פטירה",
        "ספר מולדות", "רישום לידות", "פנקס לידות",
        "birth", "births", "marriage", "marriages", "death",
        "deaths", "circumcision", "vital records",
    ],
    "Gittin": [
        "גט", "גיטין", "גט פיטורין", "שטר גירושין",
        "גירושין", "גירושים", "כריתות", "ספר כריתות",
        "פרוזבול", "שטר שחרור",
        "get", "gittin", "divorce", "bill of divorce",
        "divorce document",
    ],
    "Business records (Manuscript)": [
        "חשבון", "חשבונות", "ספר חשבון", "ספרי חשבונות",
        "פנקס חשבון", "פנקסי חשבון", "עסק", "עסקים",
        "מסחר", "מסחרי", "חנות", "קנין", "קניות",
        "חוב", "חובות", "הכנסה", "הוצאה", "הוצאות",
        "מאזן", "רווח", "הפסד", "נאמן", "שותפות",
        "account", "accounts", "ledger", "business",
        "commerce", "trade", "merchant", "accounting",
    ],
    "Forms (Jewish law)": [
        "נוסח", "נוסחה", "נוסחאות", "נוסחות", "נוסחי",
        "טופס", "טפסים", "שטר שלם", "נוסח שטר",
        "נוסח שבועה", "נוסח חוזה", "הסכם", "חוזה",
        "formulary", "formula", "formulae", "legal form",
        "legal forms", "template", "model document",
    ],
    "Biographies (Manuscript)": [
        "ביוגרפיה", "קורות חיים", "תולדות", "תולדותיו", "תולדותיה",
        "תולדות חייו", "תולדות חיי", "זכרונות", "מזכרות",
        "אוטוביוגרפיה", "פרקי חיים", "שבחים", "מעלות",
        "חייו", "חייה", "סיפור חיים",
        "biography", "memoir", "memoirs", "life story",
        "autobiography", "autobiographical",
    ],
}


# ── Model ────────────────────────────────────────────────────────────


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
        # Freeze embeddings and bottom `freeze_layers` transformer layers
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


def _split_sentences(text: str) -> list[str]:
    """Split notes text into sentences on period/newline/pipe boundaries."""
    parts = re.split(r"(?<=[.!?])\s+|\n+|\|", text)
    return [p.strip() for p in parts if p.strip()]


def _keyword_sentence_context(notes: list[str], genre: str) -> str | None:
    """Return 3-sentence context window around the first keyword match in notes.

    Returns None if no keyword for this genre appears in any note sentence.
    """
    keywords = [kw.lower() for kw in GENRE_KEYWORDS.get(genre, [])]
    if not keywords:
        return None

    sentences: list[str] = []
    for note in notes:
        sentences.extend(_split_sentences(str(note)))

    for i, sent in enumerate(sentences):
        sent_lower = sent.lower()
        if any(kw in sent_lower for kw in keywords):
            start = max(0, i - 1)
            end = min(len(sentences), i + 2)
            return " ".join(sentences[start:end])
    return None


def load_samples(tsv_files: list[str], top_k: int = 20) -> list[dict]:
    """Load labeled samples keeping the top-K genres by frequency + NOTA class.

    Filtering: only records where MARC 245 + 500 + 655 all exist AND at least
    one genre's keyword appears in the MARC 500 notes are used. The training
    text (X) is the MARC 245 title + the 3-sentence context window in MARC 500
    around the keyword mention (sentence before, matching sentence, after).
    """
    global GENRE_LABELS, NUM_GENRES, GENRE_TO_IDX

    from collections import Counter  # noqa: PLC0415

    raw: list[dict] = []
    skipped_no_keyword = 0

    for tsv_path in tsv_files:
        reader = UnifiedReader(Path(tsv_path))
        for marc_record in reader.read_file():
            try:
                data = extract_all_data(marc_record)
            except Exception:
                continue
            genres_in_map = [g for g in (data.genres or []) if g in GENRE_TO_QID]
            if not genres_in_map or not data.notes or not data.title:
                continue

            # Find genres whose keywords appear in 500 notes
            matched_genres: list[str] = []
            context_text: str | None = None
            for genre in genres_in_map:
                ctx = _keyword_sentence_context(data.notes, genre)
                if ctx is not None:
                    matched_genres.append(genre)
                    if context_text is None:
                        context_text = ctx  # use first match's context

            if not matched_genres:
                skipped_no_keyword += 1
                continue

            title = (data.title or "").strip()
            text = (title + " " + context_text).strip()
            raw.append({"text": text, "genres": matched_genres})

    print(f"  Records with keyword match: {len(raw)}")
    print(f"  Records skipped (no keyword in notes): {skipped_no_keyword}")

    freq: Counter = Counter(g for s in raw for g in s["genres"])
    top_genres = [g for g, _ in freq.most_common(top_k)]
    top_genres_set = set(top_genres)
    nota_idx = len(top_genres)
    total_classes = nota_idx + 1

    print(f"\nTop-{top_k} genres by keyword-matched frequency:")
    for g in top_genres:
        print(f"  {freq[g]:6d}  {g}")
    nota_count = sum(1 for s in raw if not any(g in top_genres_set for g in s["genres"]))
    print(f"NOTA examples (genres outside top-{top_k}): {nota_count}")
    print(f"Total labeled samples: {len(raw)}")

    if not top_genres:
        return []

    GENRE_LABELS = top_genres + [NOTA_LABEL]
    NUM_GENRES = total_classes
    GENRE_TO_IDX = {g: i for i, g in enumerate(GENRE_LABELS)}

    samples: list[dict] = []
    for s in raw:
        top_here = [g for g in s["genres"] if g in top_genres_set]
        label_vector = [0.0] * total_classes
        if top_here:
            for g in top_here:
                label_vector[top_genres.index(g)] = 1.0
        else:
            label_vector[nota_idx] = 1.0
        active_genres = top_here if top_here else [NOTA_LABEL]
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


# ── NER encoder warm-start ───────────────────────────────────────────


def load_ner_encoder_weights(model: GenreClassificationModel, ner_checkpoint_path: str) -> None:
    """Copy bert.* weights from a NER checkpoint into the genre model's encoder.

    The NER checkpoint was trained on Hebrew manuscript text, giving the encoder
    domain knowledge before genre classification head training begins.
    """
    ckpt = torch.load(ner_checkpoint_path, map_location="cpu", weights_only=False)
    ner_state = ckpt.get("model_state_dict", {})
    # Strip "bert." prefix: NER checkpoint keys are "bert.embeddings.*",
    # but model.bert expects "embeddings.*"
    bert_weights = {
        k[len("bert."):]: v for k, v in ner_state.items() if k.startswith("bert.")
    }
    missing, unexpected = model.bert.load_state_dict(bert_weights, strict=False)
    print(f"  NER encoder loaded from {ner_checkpoint_path}")
    if missing:
        print(f"  Missing keys: {missing[:3]}{'...' if len(missing) > 3 else ''}")


# ── Training ─────────────────────────────────────────────────────────


def compute_pos_weight(samples: list[dict], device: torch.device) -> torch.Tensor:
    """Compute per-class positive weight = (n_neg / n_pos) to handle imbalance."""
    label_matrix = np.array([s["label_vector"] for s in samples])
    n = len(samples)
    pos_counts = label_matrix.sum(axis=0).clip(min=1)
    neg_counts = n - pos_counts
    weights = neg_counts / pos_counts
    return torch.tensor(weights, dtype=torch.float32, device=device)


def tune_threshold(
    model: GenreClassificationModel,
    loader: DataLoader,
    device: torch.device,
) -> float:
    """Find the sigmoid threshold that maximises micro-F1 on loader."""
    model.eval()
    all_probs: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []
    with torch.no_grad():
        for batch in loader:
            logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
            all_probs.append(torch.sigmoid(logits).cpu().numpy())
            all_labels.append(batch["labels"].cpu().numpy().astype(bool))
    probs = np.vstack(all_probs)
    labels = np.vstack(all_labels)
    best_t, best_f1 = 0.5, 0.0
    for t in np.arange(0.2, 0.81, 0.05):
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
) -> tuple[float, dict]:
    # Differential learning rates: encoder gets lr * encoder_lr_factor (lower),
    # classification head gets full lr.
    optimizer = AdamW(
        [
            {"params": model.bert.parameters(), "lr": lr * encoder_lr_factor},
            {"params": model.classifier.parameters(), "lr": lr},
        ],
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
    parser.add_argument(
        "--ner-checkpoint", default="ner/provenance_ner_model.pt",
        help="NER model checkpoint to warm-start the BERT encoder from (bert.* weights only)",
    )
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-5,
                        help="Head learning rate; encoder gets lr * encoder-lr-factor")
    parser.add_argument("--encoder-lr-factor", type=float, default=0.1,
                        help="Encoder LR = lr * this factor (default 0.1 → 2e-6)")
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--dropout", type=float, default=0.3)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--top-k", type=int, default=20,
                        help="Number of top genres by frequency to use as classes")
    parser.add_argument("--freeze-layers", type=int, default=8,
                        help="Number of bottom BERT layers to freeze (0=full fine-tune, 12=head only)")
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
    samples = load_samples(args.tsv_files, top_k=args.top_k)
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
    best_overall_threshold: float = 0.5

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
        model = GenreClassificationModel(
            args.model_name, NUM_GENRES, args.dropout, freeze_layers=args.freeze_layers,
        )
        if args.ner_checkpoint and Path(args.ner_checkpoint).exists():
            load_ner_encoder_weights(model, args.ner_checkpoint)
        model = model.to(device)

        f1, state = train_fold(
            model, train_loader, val_loader, device,
            args.epochs, args.lr, args.encoder_lr_factor,
            args.weight_decay, args.patience, pos_weight,
        )
        # Tune threshold on validation set for this fold's best model
        model.load_state_dict(state)
        best_threshold = tune_threshold(model, val_loader, device)
        print(f"Fold {fold + 1}: best micro-F1 = {f1:.4f}  best_threshold = {best_threshold}")

        if f1 > best_overall_f1:
            best_overall_f1 = f1
            best_overall_state = state
            best_overall_threshold = best_threshold

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
            "threshold": best_overall_threshold,
            "best_fold_f1": best_overall_f1,
            "base_model": args.model_name,
            "num_genres": NUM_GENRES,
            "top_k": args.top_k,
        },
        args.output,
    )
    print(f"\nSaved best checkpoint → {args.output}  (F1={best_overall_f1:.4f})")


if __name__ == "__main__":
    main()
