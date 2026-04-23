"""Extract keyword-matched genre training samples from large TSVs into a compact file.

Scans all 123k records once, keeps records with MARC 655 + keyword in MARC 500,
writes (text, genres) pairs to data/tsvs/genre_samples.tsv.

After running this once, training uses only genre_samples.tsv (~25k rows)
instead of re-scanning 123k records every run.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/extract_genre_samples.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

sys.path.insert(0, str(_REPO_ROOT / "ner"))

from converter.parser.unified_reader import UnifiedReader  # noqa: E402
from converter.transformer.field_handlers import extract_all_data  # noqa: E402
from converter.wikidata.property_mapping import GENRE_TO_QID  # noqa: E402
from train_genre_classifier import GENRE_KEYWORDS, _keyword_sentence_context  # noqa: E402

INPUT_TSVS = [
    "data/tsvs/17th_century_samples.tsv",
    "data/tsvs/top100_richest.tsv",
    "data/tsvs/filtered_manuscripts_after_906a.tsv",
]
OUTPUT = Path("data/tsvs/genre_samples.tsv")


def main() -> None:
    rows: list[dict] = []
    skipped_no_genre = 0
    skipped_no_keyword = 0

    for tsv_path in INPUT_TSVS:
        if not Path(tsv_path).exists():
            print(f"  Skipping missing: {tsv_path}")
            continue
        print(f"Scanning {tsv_path} ...")
        reader = UnifiedReader(Path(tsv_path))
        for marc_record in reader.read_file():
            try:
                data = extract_all_data(marc_record)
            except Exception:
                continue

            genres_in_map = [g for g in (data.genres or []) if g in GENRE_TO_QID]
            if not genres_in_map or not data.notes or not data.title:
                skipped_no_genre += 1
                continue

            matched_genres: list[str] = []
            context_text: str | None = None
            for genre in genres_in_map:
                ctx = _keyword_sentence_context(data.notes, genre)
                if ctx is not None:
                    matched_genres.append(genre)
                    if context_text is None:
                        context_text = ctx

            if not matched_genres or context_text is None:
                skipped_no_keyword += 1
                continue

            title = (data.title or "").strip()
            text = (title + " " + context_text).strip()
            rows.append({"text": text, "genres": ";".join(matched_genres)})

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "genres"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone.")
    print(f"  Matched:            {len(rows):,}")
    print(f"  Skipped (no genre): {skipped_no_genre:,}")
    print(f"  Skipped (no kw):    {skipped_no_keyword:,}")
    print(f"  Saved → {OUTPUT}")


if __name__ == "__main__":
    main()
