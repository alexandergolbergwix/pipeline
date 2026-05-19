#!/usr/bin/env python3
"""Export enriched training-data files from the original training extractors.

This script reuses the same extraction logic that generated the training data
for:
1. Provenance NER (MARC 561)
2. Contents NER (MARC 505 + 500 cross-reference)
3. MARC 500 sentence classifier

The exported files keep the original MARC source fields, record 001, and a
human-consumable ``y`` target column.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Final

import pandas as pd

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from converter.parser.unified_reader import UnifiedReader  # noqa: E402
from converter.transformer.field_handlers import extract_all_data  # noqa: E402
from ner.extract_contents_entities import ContentsEntityExtractor  # noqa: E402
from ner.extract_provenance_entities import ProvenanceEntityExtractor  # noqa: E402
from scripts.extract_marc500_sentences import (  # noqa: E402
    INPUT_TSVS,
    _COLOPHON_KEYWORDS,
    _PROVENANCE_KEYWORDS,
    _contains_any,
    _split_sentences,
)


def _json_cell(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def _input_csv() -> Path:
    preferred = _REPO_ROOT / "processed-data" / "filtered_data.csv"
    fallback = _REPO_ROOT / "ner" / "processed-data" / "filtered_data.csv"
    if preferred.exists():
        return preferred
    if fallback.exists():
        return fallback
    raise FileNotFoundError("Could not find filtered_data.csv for training export")


def export_provenance(output_dir: Path) -> Path:
    input_file = _input_csv()
    extractor = ProvenanceEntityExtractor(input_file)
    df = pd.read_csv(input_file, low_memory=False)

    rows: list[dict[str, str]] = []
    for idx in range(len(df)):
        row = df.iloc[idx]
        samples = extractor.extract_from_record(row)
        if not samples:
            continue

        record_001 = _clean_cell(row.get("001"))
        marc_561a = extractor.clean_marc_text(_clean_cell(row.get("561$a")))
        marc_100a = _clean_cell(row.get("100$a"))
        marc_100e = _clean_cell(row.get("100$e"))
        marc_700a = _clean_cell(row.get("700$a"))
        marc_700e = _clean_cell(row.get("700$e"))

        for sample in samples:
            rows.append({
                "record_001": record_001,
                "source_field": _clean_cell(sample.get("source_field")),
                "marc_561a": marc_561a,
                "marc_100a": marc_100a,
                "marc_100e": marc_100e,
                "marc_700a": marc_700a,
                "marc_700e": marc_700e,
                "segment_text": _clean_cell(sample.get("notes_text")),
                "tokens_json": _json_cell(sample.get("tokens", [])),
                "y": _json_cell(sample.get("ner_tags", [])),
                "entities_json": _json_cell(sample.get("entities", [])),
            })

    output_path = output_dir / "provenance_training_with_marc.tsv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def export_contents(output_dir: Path) -> Path:
    input_file = _input_csv()
    extractor = ContentsEntityExtractor(input_file)
    df = pd.read_csv(input_file, low_memory=False)

    rows: list[dict[str, str]] = []
    for idx in range(len(df)):
        row = df.iloc[idx]
        samples = extractor.extract_from_record(row)
        if not samples:
            continue

        record_001 = _clean_cell(row.get("001"))
        marc_505a = extractor.clean_marc_text(_clean_cell(row.get("505$a")))
        marc_500a = extractor.clean_marc_text(_clean_cell(row.get("500$a")))

        for sample in samples:
            rows.append({
                "record_001": record_001,
                "source_field": _clean_cell(sample.get("source_field")),
                "marc_505a": marc_505a,
                "marc_500a": marc_500a,
                "segment_text": _clean_cell(sample.get("notes_text")),
                "tokens_json": _json_cell(sample.get("tokens", [])),
                "y": _json_cell(sample.get("ner_tags", [])),
                "entities_json": _json_cell(sample.get("entities", [])),
            })

    output_path = output_dir / "contents_training_with_marc.tsv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def export_marc500(output_dir: Path) -> Path:
    rows: list[dict[str, str]] = []

    for tsv_path in INPUT_TSVS:
        path = _REPO_ROOT / tsv_path
        if not path.exists():
            continue
        reader = UnifiedReader(path)
        for marc_record in reader.read_file():
            try:
                data = extract_all_data(marc_record)
            except Exception:
                continue

            control_number = _clean_cell(
                getattr(data, "control_number", "") or getattr(marc_record, "control_number", "")
            )
            notes = [str(note) for note in (data.notes or []) if note]
            full_marc_500a = " | ".join(notes)
            marc_561a = _clean_cell(data.provenance)
            has_provenance_field = bool(data.provenance)

            for note in notes:
                for sent in _split_sentences(note):
                    is_colophon = int(_contains_any(sent, _COLOPHON_KEYWORDS))
                    is_provenance = int(
                        _contains_any(sent, _PROVENANCE_KEYWORDS) and has_provenance_field
                    )
                    rows.append({
                        "record_001": control_number,
                        "source_field": "500$a",
                        "marc_500a": full_marc_500a,
                        "marc_561a": marc_561a,
                        "sentence_text": sent,
                        "y": _json_cell({
                            "is_colophon": is_colophon,
                            "is_provenance": is_provenance,
                        }),
                        "y_is_colophon": str(is_colophon),
                        "y_is_provenance": str(is_provenance),
                    })

    output_path = output_dir / "marc500_sentence_training_with_marc.tsv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def main() -> None:
    output_dir = _REPO_ROOT / "processed-data" / "training_exports"
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs = [
        export_provenance(output_dir),
        export_contents(output_dir),
        export_marc500(output_dir),
    ]

    for output in outputs:
        print(output)


if __name__ == "__main__":
    main()
