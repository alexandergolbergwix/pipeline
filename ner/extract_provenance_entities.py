#!/usr/bin/env python3
"""Extract provenance entities from MARC 561 fields using distant supervision.

Three complementary strategies for generating BIO-tagged training data:
A. Structured cross-reference: match owners from 700$a (role=בעלים קודמים) in 561$a
B. Rule-based pattern extraction: Hebrew cataloging conventions (ציון בעלים, אוסף, etc.)
C. Latin censor name extraction: "Censor:" followed by Latin names

Entity types: OWNER, DATE, COLLECTION
Output: JSONL files compatible with the existing NER training pipeline.

Methodology follows Goldberg, Prebor & Elmalech (2025) — distant supervision
from MARC structured fields, adapted for provenance entities.
"""

import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("provenance_extraction.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ── Owner role terms (from relator_consolidation_mapping.json) ───────
OWNER_ROLES = {
    "בעלים קודמים", "previous owner", "(בעלים קודמים?)",
    "former owner", "owner", "בעלים",
}

# ── Pattern constants ────────────────────────────────────────────────

# Owner name patterns in 561$a text
# Hebrew abbreviations contain internal quotes (בכ"ר, זצ"ל, נר"ו) so we
# match until a quote followed by a sentence delimiter (.| or end).
_Q = r'"(.+?)"(?=\s*[.|,\s]|$)'  # Quoted name with possible internal quotes
OWNER_NAME_PATTERNS = [
    # "ציון בעלים:" / "ציוני בעלים:" followed by quoted name
    re.compile(r'ציון(?:י)?\s+בעלים[^:]*:\s*' + _Q),
    # "חותמת בעלים:" followed by quoted name
    re.compile(r'חותמת\s+בעלים[^:]*:\s*' + _Q),
    # "חתימת בעלים:" followed by quoted name
    re.compile(r'חתימת\s+בעלים[^:]*:\s*' + _Q),
    # "הערת בעלים:" followed by quoted name
    re.compile(r'הערת\s+בעלים[^:]*:\s*' + _Q),
    # "רשימת בעלים:" followed by quoted name
    re.compile(r'רשימת\s+בעלים[^:]*:\s*' + _Q),
    # "(חתום):" followed by quoted name
    re.compile(r'\(חתום\)\s*:\s*' + _Q),
    # "מקנת כספי" followed by name text (until period or pipe)
    re.compile(r'מקנת\s+כספ[יו]\s+(?:הצעיר\s+)?([^.|]{3,80})'),
]

# Unquoted owner patterns (lower confidence)
OWNER_NAME_PATTERNS_UNQUOTED = [
    # "ציון בעלים:" followed by unquoted name (until pipe or period)
    re.compile(r'ציון(?:י)?\s+בעלים[^:]*:\s+([^\s"|][^|."]{3,50})'),
    # "חותמת בעלים:" unquoted
    re.compile(r'חותמת\s+בעלים[^:]*:\s+([^\s"|][^|."]{3,50})'),
]

# Collection patterns
COLLECTION_PATTERNS = [
    re.compile(r'אוסף\s+([^\s.|]+(?:\s+[^\s.|]+){0,3})'),
    re.compile(r'מאוסף\s+([^\s.|]+(?:\s+[^\s.|]+){0,3})'),
]

# Hebrew date patterns
DATE_PATTERNS = [
    # "משנת XXXX" or "שנת XXXX" (Gregorian or Hebrew year)
    re.compile(r'(?:מ?שנת|בשנת)\s+(\d{4})'),
    re.compile(r'(?:מ?שנת|בשנת)\s+(ה?[תשרקצפעסנמלכיטחזוהדגבא]["\u05F4\'][א-ת]["\u05F4\']?[א-ת]?)'),
    # Gregorian year range: "1600-1650"
    re.compile(r'\b(\d{4})\s*[-–]\s*(\d{4})\b'),
    # Standalone Gregorian year in context
    re.compile(r'\b(\d{4})\b'),
]

# Latin censor pattern
CENSOR_PATTERN = re.compile(
    r'[Cc]ensor:\s*([\w\s.\[\]]+?)(?:,\s*(\d{4}))?(?:\.|$)',
)


class ProvenanceEntityExtractor:
    """Extract and match provenance entities from MARC 561 records."""

    def __init__(self, input_file: Path) -> None:
        self.input_file = input_file
        self.stats: dict[str, Any] = {
            "total_rows": 0,
            "rows_with_561": 0,
            "samples_created": 0,
            "entities_by_type": Counter(),
            "entities_by_strategy": Counter(),
        }

    def clean_marc_text(self, text: str) -> str:
        """Clean MARC field text.

        Handles CSV double-quote escaping: ``""text""`` → ``"text"``.
        """
        if pd.isna(text) or not isinstance(text, str):
            return ""
        # Remove outer wrapping quotes only
        text = text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        # Unescape CSV double-quotes but preserve single quotes used in Hebrew
        text = text.replace('""', '"')
        text = re.sub(r"\|\|", "|", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def normalize_hebrew(self, text: str) -> str:
        """Normalize Hebrew text for matching (remove nikud, quotes)."""
        if not text:
            return ""
        text = re.sub(r"[\u0591-\u05C7]", "", text)
        text = re.sub(r"\s+", " ", text)
        text = text.replace('"', "").replace("'", "").replace("`", "")
        return text.strip()

    def tokenize(self, text: str) -> list[str]:
        """Simple whitespace tokenization for Hebrew text."""
        return text.split()

    def find_span_in_tokens(
        self, tokens: list[str], entity_text: str,
    ) -> tuple[int, int]:
        """Find entity text span in tokenized text.

        Returns (start_token_idx, end_token_idx) or (-1, -1) if not found.
        """
        entity_norm = self.normalize_hebrew(entity_text)
        entity_words = entity_norm.split()
        if not entity_words:
            return (-1, -1)

        tokens_norm = [self.normalize_hebrew(t) for t in tokens]

        for i in range(len(tokens_norm) - len(entity_words) + 1):
            match = True
            for j, ew in enumerate(entity_words):
                tn = tokens_norm[i + j]
                if ew not in tn and tn not in ew:
                    match = False
                    break
            if match:
                return (i, i + len(entity_words))

        # Fuzzy: try matching first 3 chars of each entity word
        if len(entity_words) >= 2:
            for i in range(len(tokens_norm) - len(entity_words) + 1):
                match = True
                for j, ew in enumerate(entity_words):
                    tn = tokens_norm[i + j]
                    if len(ew) >= 3 and len(tn) >= 3:
                        if ew[:3] != tn[:3]:
                            match = False
                            break
                    elif ew != tn:
                        match = False
                        break
                if match:
                    return (i, i + len(entity_words))

        return (-1, -1)

    # ── Strategy A: Structured cross-reference ───────────────────────

    def extract_owners_structured(self, row: pd.Series, text_561: str) -> list[dict]:
        """Match structured owner names (from 700$a with owner role) in 561 text."""
        entities: list[dict] = []
        text_561_norm = self.normalize_hebrew(text_561)

        # Check 700$a with 700$e = owner role
        for person_field, role_field in [("100$a", "100$e"), ("700$a", "700$e")]:
            if person_field not in row.index or pd.isna(row[person_field]):
                continue
            if role_field not in row.index or pd.isna(row[role_field]):
                continue

            persons = str(row[person_field]).split("|")
            roles = str(row[role_field]).split("|")

            for idx, person_raw in enumerate(persons):
                role_raw = roles[idx].strip() if idx < len(roles) else ""
                if role_raw.lower() not in OWNER_ROLES and role_raw not in OWNER_ROLES:
                    continue

                person_clean = self.clean_marc_text(person_raw)
                person_norm = self.normalize_hebrew(person_clean)
                if not person_norm or len(person_norm) < 3:
                    continue

                if person_norm in text_561_norm:
                    entities.append({
                        "text": person_clean,
                        "type": "OWNER",
                        "strategy": "structured",
                        "confidence": 0.95,
                    })
                    self.stats["entities_by_strategy"]["structured"] += 1

        return entities

    # ── Strategy B: Rule-based pattern extraction ────────────────────

    def extract_owners_pattern(self, text_561: str) -> list[dict]:
        """Extract owner names using Hebrew cataloging patterns."""
        entities: list[dict] = []
        seen_names: set[str] = set()

        # High-confidence quoted patterns
        for pattern in OWNER_NAME_PATTERNS:
            for match in pattern.finditer(text_561):
                name = match.group(1).strip().strip(",. ")
                name_norm = self.normalize_hebrew(name)
                if name_norm and len(name_norm) >= 3 and name_norm not in seen_names:
                    seen_names.add(name_norm)
                    entities.append({
                        "text": name,
                        "type": "OWNER",
                        "strategy": "pattern_quoted",
                        "confidence": 0.90,
                    })
                    self.stats["entities_by_strategy"]["pattern_quoted"] += 1

        # Lower-confidence unquoted patterns
        for pattern in OWNER_NAME_PATTERNS_UNQUOTED:
            for match in pattern.finditer(text_561):
                name = match.group(1).strip().strip(",. ")
                name_norm = self.normalize_hebrew(name)
                if name_norm and len(name_norm) >= 3 and name_norm not in seen_names:
                    seen_names.add(name_norm)
                    entities.append({
                        "text": name,
                        "type": "OWNER",
                        "strategy": "pattern_unquoted",
                        "confidence": 0.75,
                    })
                    self.stats["entities_by_strategy"]["pattern_unquoted"] += 1

        return entities

    def extract_collections(self, text_561: str) -> list[dict]:
        """Extract collection names (אוסף X pattern)."""
        entities: list[dict] = []
        seen: set[str] = set()

        for pattern in COLLECTION_PATTERNS:
            for match in pattern.finditer(text_561):
                name = match.group(1).strip().strip(",. ")
                name_norm = self.normalize_hebrew(name)
                if name_norm and len(name_norm) >= 2 and name_norm not in seen:
                    seen.add(name_norm)
                    entities.append({
                        "text": name,
                        "type": "COLLECTION",
                        "strategy": "pattern",
                        "confidence": 0.92,
                    })
                    self.stats["entities_by_strategy"]["collection_pattern"] += 1

        return entities

    def extract_dates(self, text_561: str) -> list[dict]:
        """Extract dates from provenance text."""
        entities: list[dict] = []
        seen: set[str] = set()

        for pattern in DATE_PATTERNS:
            for match in pattern.finditer(text_561):
                date_text = match.group(0).strip()
                if date_text not in seen and len(date_text) >= 3:
                    seen.add(date_text)
                    entities.append({
                        "text": date_text,
                        "type": "DATE",
                        "strategy": "pattern",
                        "confidence": 0.85,
                    })
                    self.stats["entities_by_strategy"]["date_pattern"] += 1

        return entities

    # ── Strategy C: Latin censor names ───────────────────────────────

    def extract_censors(self, text_561: str) -> list[dict]:
        """Extract Latin censor names and dates."""
        entities: list[dict] = []

        for match in CENSOR_PATTERN.finditer(text_561):
            name = match.group(1).strip().strip(",. ")
            if name and len(name) >= 3:
                entities.append({
                    "text": name,
                    "type": "OWNER",
                    "strategy": "censor",
                    "confidence": 0.90,
                })
                self.stats["entities_by_strategy"]["censor"] += 1

            date_str = match.group(2)
            if date_str:
                entities.append({
                    "text": date_str,
                    "type": "DATE",
                    "strategy": "censor_date",
                    "confidence": 0.95,
                })

        return entities

    # ── Main extraction ──────────────────────────────────────────────

    def create_bio_sample(
        self, text_561: str, entities: list[dict],
    ) -> dict | None:
        """Create a BIO-tagged sample from 561 text and extracted entities.

        Splits text at pipe boundaries. Creates one sample per segment
        that contains at least one entity.
        """
        samples = []
        segments = text_561.split("|")

        for segment in segments:
            segment = segment.strip()
            if not segment or len(segment) < 10:
                continue

            tokens = self.tokenize(segment)
            if len(tokens) < 3 or len(tokens) > 150:
                continue

            ner_tags = ["O"] * len(tokens)
            occupied: set[int] = set()
            entities_found: list[dict] = []

            for entity in entities:
                start, end = self.find_span_in_tokens(tokens, entity["text"])
                if start < 0:
                    continue

                # Check no overlap
                span_positions = set(range(start, end))
                if span_positions & occupied:
                    continue

                occupied |= span_positions
                ner_tags[start] = f"B-{entity['type']}"
                for i in range(start + 1, end):
                    ner_tags[i] = f"I-{entity['type']}"
                entities_found.append(entity)

            if not entities_found:
                continue

            for ent in entities_found:
                self.stats["entities_by_type"][ent["type"]] += 1

            samples.append({
                "tokens": tokens,
                "ner_tags": ner_tags,
                "entities": entities_found,
                "entity_count": len(entities_found),
                "notes_text": segment,
                "source_field": "561$a",
            })

        return samples if samples else None

    def extract_from_record(self, row: pd.Series) -> list[dict]:
        """Extract provenance entities from a single MARC record."""
        if "561$a" not in row.index or pd.isna(row["561$a"]):
            return []

        text_561 = self.clean_marc_text(str(row["561$a"]))
        if not text_561 or len(text_561) < 10:
            return []

        self.stats["rows_with_561"] += 1

        # Combine all strategies
        all_entities: list[dict] = []
        all_entities.extend(self.extract_owners_structured(row, text_561))
        all_entities.extend(self.extract_owners_pattern(text_561))
        all_entities.extend(self.extract_collections(text_561))
        all_entities.extend(self.extract_dates(text_561))
        all_entities.extend(self.extract_censors(text_561))

        if not all_entities:
            return []

        # Deduplicate by normalized text
        seen: set[str] = set()
        deduped: list[dict] = []
        for ent in all_entities:
            key = self.normalize_hebrew(ent["text"]) + "|" + ent["type"]
            if key not in seen:
                seen.add(key)
                deduped.append(ent)

        # Create BIO samples (one per pipe-separated segment)
        result = self.create_bio_sample(text_561, deduped)
        return result or []

    def process_dataset(self, max_rows: int | None = None) -> list[dict]:
        """Process the entire MARC dataset."""
        logger.info("Loading %s...", self.input_file)
        df = pd.read_csv(self.input_file, low_memory=False)
        logger.info("Loaded %d records", len(df))

        all_samples: list[dict] = []
        limit = max_rows or len(df)

        for idx in range(min(limit, len(df))):
            row = df.iloc[idx]
            self.stats["total_rows"] += 1
            samples = self.extract_from_record(row)
            all_samples.extend(samples)

            if (idx + 1) % 10000 == 0:
                logger.info(
                    "Processed %d/%d rows, %d samples so far",
                    idx + 1, limit, len(all_samples),
                )

        self.stats["samples_created"] = len(all_samples)
        return all_samples

    def save_results(self, samples: list[dict], output_dir: Path) -> None:
        """Save extracted samples to JSONL and statistics to JSON."""
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save samples
        output_file = output_dir / "provenance_dataset.jsonl"
        with open(output_file, "w", encoding="utf-8") as f:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")
        logger.info("Saved %d samples to %s", len(samples), output_file)

        # Save statistics
        stats_file = output_dir / "provenance_extraction_stats.json"
        serializable_stats = {
            k: dict(v) if isinstance(v, (Counter, defaultdict)) else v
            for k, v in self.stats.items()
        }
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump(serializable_stats, f, ensure_ascii=False, indent=2)
        logger.info("Stats: %s", json.dumps(serializable_stats, indent=2))


def main() -> None:
    """Run provenance entity extraction on MARC data."""
    input_file = Path("processed-data/filtered_data.csv")
    if not input_file.exists():
        input_file = Path("ner/processed-data/filtered_data.csv")
    if not input_file.exists():
        logger.error("Input file not found: %s", input_file)
        sys.exit(1)

    extractor = ProvenanceEntityExtractor(input_file)
    samples = extractor.process_dataset()
    extractor.save_results(samples, Path("processed-data"))


if __name__ == "__main__":
    main()
