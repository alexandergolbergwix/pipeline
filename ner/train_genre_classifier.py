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
  - Loss: Focal Loss (γ=2.0, per-class pos_weight) — handles severe class imbalance
  - Metric: micro-F1 at tuned threshold (scanned on val set per fold)
  - Classes with fewer than --min-class-size training examples are dropped.

Usage:
    PYTHONPATH=src:. .venv/bin/python ner/train_genre_classifier.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import re
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
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

from genre_classifier_model import GenreClassificationModel  # noqa: E402


# ── Dataset ──────────────────────────────────────────────────────────


class GenreDataset(Dataset):
    """Multi-label genre classification dataset (tokenized text)."""

    def __init__(
        self,
        samples: list[dict],
        tokenizer: AutoTokenizer,
        max_length: int = 128,
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


class EmbeddingDataset(Dataset):
    """Dataset of precomputed CLS embeddings — no BERT at training time."""

    def __init__(self, embeddings: torch.Tensor, labels: torch.Tensor) -> None:
        self.embeddings = embeddings  # (N, 768) on CPU
        self.labels = labels          # (N, num_genres) on CPU

    def __len__(self) -> int:
        return len(self.embeddings)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        return {"embedding": self.embeddings[idx], "labels": self.labels[idx]}


def precompute_embeddings(
    bert_model: nn.Module,
    samples: list[dict],
    tokenizer: AutoTokenizer,
    device: torch.device,
    max_length: int = 128,
    batch_size: int = 128,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Run BERT once over all samples, return (embeddings, labels) on CPU.

    This converts the slow per-batch BERT forward pass into a single one-time
    cost. Training then only trains the tiny linear head on static 768-dim vectors.
    """
    bert_model.eval()
    all_cls: list[torch.Tensor] = []
    all_labels: list[torch.Tensor] = []

    ds = GenreDataset(samples, tokenizer, max_length)
    loader = DataLoader(ds, batch_size=batch_size, shuffle=False)

    print(f"  Precomputing embeddings for {len(samples)} samples...", flush=True)
    with torch.no_grad():
        for batch in loader:
            out = bert_model(
                input_ids=batch["input_ids"].to(device),
                attention_mask=batch["attention_mask"].to(device),
            )
            cls = out.last_hidden_state[:, 0, :].cpu()
            all_cls.append(cls)
            all_labels.append(batch["labels"])

    print("  Done.", flush=True)
    return torch.cat(all_cls, dim=0), torch.cat(all_labels, dim=0)


# ── Data loading ─────────────────────────────────────────────────────


def _split_sentences(text: str) -> list[str]:
    """Split notes text into sentences on period/newline/pipe boundaries."""
    parts = re.split(r"(?<=[.!?])\s+|\n+|\|", text)
    return [p.strip() for p in parts if p.strip()]


def _keyword_matches(sentence_lower: str, keyword: str) -> bool:
    """True if keyword appears as a whole token in the sentence.

    For ASCII keywords: uses \b word boundaries (reliable).
    For Hebrew keywords: checks space-delimited tokens (Hebrew \b is unreliable
    because prefixes like ו/ב/ל/ה are attached without spaces in standard text,
    so we check if the keyword equals any whitespace-split token in the sentence).
    """
    if keyword.isascii():
        return bool(re.search(r"\b" + re.escape(keyword) + r"\b", sentence_lower))
    # Hebrew: split on whitespace and punctuation, check for exact token match
    tokens = re.split(r"[\s\u05be\u05c0\u05c3,.:;!?()\[\]{}'\"]+", sentence_lower)
    return keyword in tokens


def _keyword_sentence_context(notes: list[str], genre: str) -> str | None:
    """Return 3-sentence context window around the first keyword match in notes.

    Returns None if no keyword for this genre appears in any note sentence.
    Uses whole-word matching to avoid false positives from substrings.
    """
    keywords = [kw.lower() for kw in GENRE_KEYWORDS.get(genre, [])]
    if not keywords:
        return None

    sentences: list[str] = []
    for note in notes:
        sentences.extend(_split_sentences(str(note)))

    for i, sent in enumerate(sentences):
        sent_lower = sent.lower()
        if any(_keyword_matches(sent_lower, kw) for kw in keywords):
            start = max(0, i - 1)
            end = min(len(sentences), i + 2)
            return " ".join(sentences[start:end])
    return None


def _cache_key(tsv_files: list[str], top_k: int, min_class_size: int, exclude_genres: set[str]) -> str:
    """Stable hash of loading parameters — used to invalidate the sample cache."""
    sig = json.dumps({
        "tsv_files": sorted(tsv_files),
        "top_k": top_k,
        "min_class_size": min_class_size,
        "exclude_genres": sorted(exclude_genres),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(sig.encode()).hexdigest()[:12]


def load_samples(
    tsv_files: list[str],
    top_k: int = 20,
    min_class_size: int = 50,
    exclude_genres: set[str] | None = None,
    cache_dir: str = "ner",
) -> list[dict]:
    """Load labeled samples keeping the top-K genres by frequency + NOTA class.

    Classes with fewer than min_class_size keyword-matched examples are dropped
    from the label set (they add noise without enough signal to learn from).

    Filtering: only records where MARC 245 + 500 + 655 all exist AND at least
    one genre's keyword appears in the MARC 500 notes are used.
    """
    global GENRE_LABELS, NUM_GENRES, GENRE_TO_IDX

    from collections import Counter  # noqa: PLC0415

    global GENRE_LABELS, NUM_GENRES, GENRE_TO_IDX

    _excluded = exclude_genres or set()
    key = _cache_key(tsv_files, top_k, min_class_size, _excluded)
    cache_path = Path(cache_dir) / f"genre_samples_cache_{key}.pkl"

    if cache_path.exists():
        print(f"  Loading from cache: {cache_path}")
        with open(cache_path, "rb") as f:
            cached = pickle.load(f)
        GENRE_LABELS[:] = cached["genre_labels"]
        NUM_GENRES = cached["num_genres"]
        GENRE_TO_IDX.update(cached["genre_to_idx"])
        return cached["samples"]

    # Fast path: if one of the tsv_files is the pre-extracted genre_samples.tsv,
    # load text+genres directly (no MARC parsing).
    pre_extracted = [f for f in tsv_files if Path(f).name == "genre_samples.tsv"]
    marc_tsvs = [f for f in tsv_files if Path(f).name != "genre_samples.tsv"]

    raw: list[dict] = []
    skipped_no_keyword = 0

    for pre_path in pre_extracted:
        import csv as _csv  # noqa: PLC0415
        with open(pre_path, encoding="utf-8", newline="") as f:
            for row in _csv.DictReader(f, delimiter="\t"):
                genres_in_map = [
                    g for g in row["genres"].split(";")
                    if g in GENRE_TO_QID and g not in _excluded
                ]
                if genres_in_map:
                    raw.append({"text": row["text"], "genres": genres_in_map})

    for tsv_path in marc_tsvs:
        reader = UnifiedReader(Path(tsv_path))
        for marc_record in reader.read_file():
            try:
                data = extract_all_data(marc_record)
            except Exception:
                continue
            genres_in_map = [
                g for g in (data.genres or [])
                if g in GENRE_TO_QID and g not in _excluded
            ]
            if not genres_in_map or not data.notes or not data.title:
                continue

            matched_genres: list[str] = []
            context_text: str | None = None
            for genre in genres_in_map:
                ctx = _keyword_sentence_context(data.notes, genre)
                if ctx is not None:
                    matched_genres.append(genre)
                    if context_text is None:
                        context_text = ctx

            if not matched_genres:
                skipped_no_keyword += 1
                continue

            title = (data.title or "").strip()
            text = (title + " " + context_text).strip()
            raw.append({"text": text, "genres": matched_genres})

    print(f"  Records with keyword match: {len(raw)}")
    print(f"  Records skipped (no keyword in notes): {skipped_no_keyword}")

    freq: Counter = Counter(g for s in raw for g in s["genres"])

    # Select top-K genres with enough training examples
    top_genres = [
        g for g, cnt in freq.most_common(top_k)
        if cnt >= min_class_size
    ]
    top_genres_set = set(top_genres)

    print(f"\nTop-{top_k} genres (min_class_size={min_class_size}):")
    for g in top_genres:
        print(f"  {freq[g]:6d}  {g}")
    dropped = [(g, cnt) for g, cnt in freq.most_common(top_k) if cnt < min_class_size]
    if dropped:
        print(f"  Dropped (< {min_class_size} examples): {[g for g, _ in dropped]}")

    nota_count = sum(1 for s in raw if not any(g in top_genres_set for g in s["genres"]))
    print(f"NOTA examples (genres outside accepted set): {nota_count}")
    print(f"Total labeled samples: {len(raw)}")

    if not top_genres:
        return []

    nota_idx = len(top_genres)
    total_classes = nota_idx + 1
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

    # Save cache for next run
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump({
            "samples": samples,
            "genre_labels": GENRE_LABELS,
            "num_genres": NUM_GENRES,
            "genre_to_idx": GENRE_TO_IDX,
        }, f)
    print(f"  Cache saved → {cache_path}")

    return samples


# ── Evaluation ───────────────────────────────────────────────────────


def _get_logits(model: GenreClassificationModel, batch: dict, device: torch.device, precomputed: bool) -> torch.Tensor:
    if precomputed:
        return model.classifier(model.dropout(batch["embedding"].to(device)))
    return model(batch["input_ids"].to(device), batch["attention_mask"].to(device))


def micro_f1(
    model: GenreClassificationModel,
    loader: DataLoader,
    device: torch.device,
    threshold: float = 0.5,
    precomputed: bool = False,
) -> float:
    model.eval()
    tp = fp = fn = 0
    with torch.no_grad():
        for batch in loader:
            logits = _get_logits(model, batch, device, precomputed)
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


def per_class_f1(
    model: GenreClassificationModel,
    loader: DataLoader,
    device: torch.device,
    threshold: float = 0.5,
    labels_list: list[str] | None = None,
    precomputed: bool = False,
) -> None:
    """Print per-class precision/recall/F1 for debugging."""
    model.eval()
    all_preds: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []
    with torch.no_grad():
        for batch in loader:
            logits = _get_logits(model, batch, device, precomputed)
            all_preds.append((torch.sigmoid(logits) >= threshold).cpu().numpy())
            all_labels.append(batch["labels"].cpu().numpy().astype(bool))
    preds = np.vstack(all_preds)
    labels = np.vstack(all_labels)
    n_classes = preds.shape[1]
    print(f"\n  {'Class':<40} {'P':>6} {'R':>6} {'F1':>6} {'Support':>8}")
    print(f"  {'-'*70}")
    for i in range(n_classes):
        tp = int((preds[:, i] & labels[:, i]).sum())
        fp = int((preds[:, i] & ~labels[:, i]).sum())
        fn = int((~preds[:, i] & labels[:, i]).sum())
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        support = int(labels[:, i].sum())
        label = labels_list[i] if labels_list else str(i)
        print(f"  {label:<40} {p:>6.3f} {r:>6.3f} {f1:>6.3f} {support:>8}")


# ── NER encoder warm-start ───────────────────────────────────────────


def load_ner_encoder_weights(model: GenreClassificationModel, ner_checkpoint_path: str) -> None:
    """Copy bert.* weights from a NER checkpoint into the genre model's encoder."""
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


# ── Loss ─────────────────────────────────────────────────────────────


def focal_loss(
    logits: torch.Tensor,
    labels: torch.Tensor,
    pos_weight: torch.Tensor,
    gamma: float = 2.0,
) -> torch.Tensor:
    """Binary focal loss for multi-label classification.

    Focal weighting downweights easy examples and focuses training on hard
    examples, significantly improving performance on imbalanced datasets.
    For positive examples: weight = (1-p)^gamma (easy=high p → low weight).
    For negative examples: weight = p^gamma     (easy=low p → low weight).
    """
    bce = F.binary_cross_entropy_with_logits(
        logits, labels, pos_weight=pos_weight, reduction="none",
    )
    with torch.no_grad():
        probs = torch.sigmoid(logits)
        focal_weight = torch.where(labels == 1, (1.0 - probs) ** gamma, probs ** gamma)
    return (focal_weight * bce).mean()


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
    precomputed: bool = False,
) -> float:
    """Find the sigmoid threshold that maximises micro-F1 on loader."""
    model.eval()
    all_probs: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []
    with torch.no_grad():
        for batch in loader:
            logits = _get_logits(model, batch, device, precomputed)
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
    focal_gamma: float,
    precomputed: bool = False,
) -> tuple[float, dict]:
    # When embeddings are precomputed, only head params are trained.
    # Otherwise use differential LRs: encoder gets lr * encoder_lr_factor.
    if precomputed:
        optimizer = AdamW(
            list(model.dropout.parameters()) + list(model.classifier.parameters()),
            lr=lr,
            weight_decay=weight_decay,
        )
    else:
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

    best_f1 = 0.0
    best_state: dict = {}
    no_improve = 0

    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        for batch in train_loader:
            optimizer.zero_grad()
            if precomputed:
                logits = model.classifier(model.dropout(batch["embedding"].to(device)))
            else:
                logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
            loss = focal_loss(logits, batch["labels"].to(device), pos_weight, gamma=focal_gamma)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            total_loss += loss.item()

        # Tune threshold for each epoch's eval
        best_t = tune_threshold(model, val_loader, device, precomputed=precomputed)
        f1 = micro_f1(model, val_loader, device, threshold=best_t, precomputed=precomputed)
        print(f"  Epoch {epoch + 1}/{epochs}  loss={total_loss / len(train_loader):.4f}"
              f"  val_F1={f1:.4f}  thr={best_t:.2f}", flush=True)

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
            "data/tsvs/genre_samples.tsv",  # pre-extracted fast path (if exists)
            "data/tsvs/17th_century_samples.tsv",
            "data/tsvs/top100_richest.tsv",
        ],
    )
    parser.add_argument("--output", default="ner/genre_classifier_model.pt")
    parser.add_argument("--model-name", default="dicta-il/dictabert")
    parser.add_argument(
        "--ner-checkpoint", default="ner/provenance_ner_model.pt",
        help="NER model checkpoint to warm-start the BERT encoder from (bert.* weights only)",
    )
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-5,
                        help="Head learning rate; encoder gets lr * encoder-lr-factor")
    parser.add_argument("--encoder-lr-factor", type=float, default=0.1,
                        help="Encoder LR = lr * this factor (default 0.1 → 2e-6)")
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--dropout", type=float, default=0.3)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=20,
                        help="Number of top genres by frequency to consider")
    parser.add_argument("--min-class-size", type=int, default=50,
                        help="Drop classes with fewer than this many training examples")
    parser.add_argument("--freeze-layers", type=int, default=8,
                        help="Number of bottom BERT layers to freeze (0=full fine-tune, 12=head only)")
    parser.add_argument("--focal-gamma", type=float, default=2.0,
                        help="Focal loss gamma parameter (0=BCE, 2=standard focal)")
    parser.add_argument(
        "--exclude-genres", nargs="*", default=[],
        help="Genre names to exclude from training (e.g. 'Literature (Miscellaneous, in manuscript)')",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--start-fold", type=int, default=1,
        help="Resume from this fold number (1-based). Folds before this are loaded from "
             "ner/genre_classifier_fold_N.pt checkpoints saved by a previous run.",
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
    print(f"Focal gamma: {args.focal_gamma}  min_class_size: {args.min_class_size}")
    if args.exclude_genres:
        print(f"Excluded genres: {args.exclude_genres}")

    print("\nLoading samples from TSV files...")
    samples = load_samples(
        args.tsv_files,
        top_k=args.top_k,
        min_class_size=args.min_class_size,
        exclude_genres=set(args.exclude_genres),
        cache_dir=str(Path(args.output).parent),
    )
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
    best_overall_fold: int = 0

    print(f"\n{'='*60}")
    print(f"Genre Classifier Training — {args.n_folds}-Fold CV")
    print(f"  epochs={args.epochs}  batch={args.batch_size}  max_len={args.max_length}")
    print(f"  lr={args.lr}  enc_lr_factor={args.encoder_lr_factor}  freeze={args.freeze_layers}")
    print(f"  focal_gamma={args.focal_gamma}  patience={args.patience}")
    print(f"{'='*60}")

    use_precomputed = (args.freeze_layers >= 12)

    if use_precomputed:
        print("\nLoading BERT encoder for embedding precomputation...")
        bert_encoder = GenreClassificationModel(
            args.model_name, NUM_GENRES, args.dropout, freeze_layers=12,
        )
        if args.ner_checkpoint and Path(args.ner_checkpoint).exists():
            load_ner_encoder_weights(bert_encoder, args.ner_checkpoint)
        bert_encoder = bert_encoder.to(device)
        print("Precomputing all embeddings (one-time BERT forward pass)...")
        all_embeddings, all_labels_t = precompute_embeddings(
            bert_encoder.bert, samples, tokenizer, device,
            max_length=args.max_length, batch_size=args.batch_size * 2,
        )
        del bert_encoder

    for fold, (train_idx, val_idx) in enumerate(skf.split(samples, strat_labels)):
        fold_num = fold + 1
        fold_ckpt = Path(args.output).parent / f"genre_classifier_fold_{fold_num}.pt"

        # Skip folds already completed in a previous interrupted run
        if fold_num < args.start_fold:
            if fold_ckpt.exists():
                saved = torch.load(fold_ckpt, map_location="cpu", weights_only=False)
                f1 = saved["best_fold_f1"]
                best_threshold = saved["threshold"]
                fold_results.append({"fold": fold_num, "f1": f1, "threshold": best_threshold})
                if f1 > best_overall_f1:
                    best_overall_f1 = f1
                    best_overall_state = saved["model_state_dict"]
                    best_overall_threshold = best_threshold
                    best_overall_fold = fold_num
                print(f"\nFold {fold_num}/{args.n_folds}  [loaded from checkpoint: F1={f1:.4f}]")
            else:
                print(f"\nFold {fold_num}/{args.n_folds}  [skipped — checkpoint not found at {fold_ckpt}]")
            continue

        print(f"\nFold {fold_num}/{args.n_folds}  train={len(train_idx)}  val={len(val_idx)}")

        train_samples_fold = [samples[i] for i in train_idx]
        pos_weight = compute_pos_weight(train_samples_fold, device)

        if use_precomputed:
            train_emb = all_embeddings[torch.tensor(train_idx)]
            val_emb = all_embeddings[torch.tensor(val_idx)]
            train_lbl = all_labels_t[torch.tensor(train_idx)]
            val_lbl = all_labels_t[torch.tensor(val_idx)]
            train_ds = EmbeddingDataset(train_emb, train_lbl)
            val_ds = EmbeddingDataset(val_emb, val_lbl)
            train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
            val_loader = DataLoader(val_ds, batch_size=args.batch_size * 4)
        else:
            train_ds = GenreDataset(train_samples_fold, tokenizer, args.max_length)
            val_ds = GenreDataset([samples[i] for i in val_idx], tokenizer, args.max_length)
            train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
            val_loader = DataLoader(val_ds, batch_size=args.batch_size * 2)

        model = GenreClassificationModel(
            args.model_name, NUM_GENRES, args.dropout,
            freeze_layers=12 if use_precomputed else args.freeze_layers,
        )
        if args.ner_checkpoint and Path(args.ner_checkpoint).exists():
            load_ner_encoder_weights(model, args.ner_checkpoint)
        model = model.to(device)

        f1, state = train_fold(
            model, train_loader, val_loader, device,
            args.epochs, args.lr, args.encoder_lr_factor,
            args.weight_decay, args.patience, pos_weight,
            focal_gamma=args.focal_gamma, precomputed=use_precomputed,
        )

        model.load_state_dict(state)
        best_threshold = tune_threshold(model, val_loader, device, precomputed=use_precomputed)
        print(f"Fold {fold_num}: best micro-F1 = {f1:.4f}  threshold = {best_threshold}")
        per_class_f1(model, val_loader, device, threshold=best_threshold,
                     labels_list=GENRE_LABELS, precomputed=use_precomputed)

        fold_results.append({"fold": fold_num, "f1": f1, "threshold": best_threshold})

        # Save fold checkpoint so training can be resumed after interruption
        torch.save(
            {
                "model_state_dict": state,
                "genre_label2id": GENRE_TO_IDX,
                "task": "genre_classification",
                "threshold": best_threshold,
                "best_fold_f1": f1,
                "base_model": args.model_name,
                "num_genres": NUM_GENRES,
                "max_length": args.max_length,
                "fold": fold_num,
            },
            fold_ckpt,
        )
        print(f"  Fold checkpoint saved → {fold_ckpt}")

        if f1 > best_overall_f1:
            best_overall_f1 = f1
            best_overall_state = state
            best_overall_threshold = best_threshold
            best_overall_fold = fold + 1

    f1_scores = [r["f1"] for r in fold_results]
    print(f"\n{'='*60}")
    print(f"Mean F1: {np.mean(f1_scores):.4f} ± {np.std(f1_scores):.4f}")
    print(f"Best fold F1: {max(f1_scores):.4f}  (fold {best_overall_fold})")
    print(f"Best threshold: {best_overall_threshold}")
    print(f"{'='*60}")

    torch.save(
        {
            "model_state_dict": best_overall_state,
            "genre_label2id": GENRE_TO_IDX,
            "task": "genre_classification",
            "threshold": best_overall_threshold,
            "best_fold_f1": best_overall_f1,
            "mean_fold_f1": float(np.mean(f1_scores)),
            "base_model": args.model_name,
            "num_genres": NUM_GENRES,
            "top_k": args.top_k,
            "min_class_size": args.min_class_size,
            "focal_gamma": args.focal_gamma,
            "max_length": args.max_length,
        },
        args.output,
    )
    print(f"\nSaved best checkpoint → {args.output}  (F1={best_overall_f1:.4f})")


if __name__ == "__main__":
    main()
