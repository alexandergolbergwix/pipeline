"""Extract labeled MARC 500 sentences for the MARC 500 sentence classifier.

Scans TSV files, sentence-splits MARC 500 notes, and labels each sentence as:
  - COLOPHON (1/0): contains extended colophon vocabulary
  - PROVENANCE (1/0): contains ownership vocabulary AND record has MARC 561 data

Output: data/tsvs/marc500_sentences.tsv

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/extract_marc500_sentences.py
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from converter.parser.unified_reader import UnifiedReader  # noqa: E402
from converter.transformer.field_handlers import extract_all_data  # noqa: E402

INPUT_TSVS = [
    "data/tsvs/17th_century_samples.tsv",
    "data/tsvs/top100_richest.tsv",
    "data/tsvs/filtered_manuscripts_after_906a.tsv",
]
OUTPUT = Path("data/tsvs/marc500_sentences.tsv")

# ── Vocabulary sets ───────────────────────────────────────────────────────────

_COLOPHON_KEYWORDS = {
    "קולופון", "colophon",
    "כתב הסופר", "כתב המעתיק",
    "נשלם", "נשלמה", "תמו", "תמה", "תם",
    "וגמרתי", "גמרתי", "וכתבתי", "כתבתי",
    "נכתב ע\"י", "נכתב על ידי", "כתב זה הספר",
    "הסופר", "המעתיק", "כתיבתו", "כתיבתי",
    "נכתב בשנת", "נכתב לכבוד", "נשלם בשנת",
}

_PROVENANCE_KEYWORDS = {
    "קנה", "קניתי", "נרכש", "רכשתי",
    "שייך", "שייכת",
    "נמכר", "מכרתי", "נמכרה",
    "בעלות", "בבעלות",
    "חתמתי", "חתם",
    "ירשתי", "ירש", "בירושה",
    "ממורשתי", "מורשה",
    "מוהר", "נדוניה",
    "מתנה", "כמתנה", "הוענק",
    "נכתב עבור", "נכתב בשביל",
    "עבור",
}


def _contains_any(text: str, keywords: set[str]) -> bool:
    tl = text.lower()
    return any(kw in tl for kw in keywords)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s*", text)
    return [s.strip() for s in parts if len(s.strip()) >= 10]


def main() -> None:
    rows: list[dict] = []
    n_colophon = 0
    n_provenance = 0
    n_negative = 0

    for tsv_path in INPUT_TSVS:
        p = Path(tsv_path)
        if not p.exists():
            print(f"  Skipping missing: {tsv_path}")
            continue
        print(f"Scanning {tsv_path} ...")
        reader = UnifiedReader(p)
        for marc_record in reader.read_file():
            try:
                data = extract_all_data(marc_record)
            except Exception:
                continue

            notes = data.notes or []
            has_provenance_field = bool(data.provenance)
            cn = str(getattr(data, "control_number", "") or "")

            for note in notes:
                for sent in _split_sentences(str(note)):
                    is_col = int(_contains_any(sent, _COLOPHON_KEYWORDS))
                    is_prov = int(
                        _contains_any(sent, _PROVENANCE_KEYWORDS) and has_provenance_field
                    )
                    rows.append({
                        "text": sent,
                        "is_colophon": is_col,
                        "is_provenance": is_prov,
                        "control_number": cn,
                    })
                    if is_col:
                        n_colophon += 1
                    if is_prov:
                        n_provenance += 1
                    if not is_col and not is_prov:
                        n_negative += 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "is_colophon", "is_provenance", "control_number"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nExtracted {len(rows):,} sentences → {OUTPUT}")
    print(f"  COLOPHON positives : {n_colophon:,}")
    print(f"  PROVENANCE positives: {n_provenance:,}")
    print(f"  Negative (neither)  : {n_negative:,}")


if __name__ == "__main__":
    main()
