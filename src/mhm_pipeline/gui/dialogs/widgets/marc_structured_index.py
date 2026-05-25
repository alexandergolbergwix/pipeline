"""Index of MARC structured-field values, per record.

Used by the AI-verification dialog to flag verdicts that surface
information NOT already in the manuscript's structured catalog
fields. The check fires only when the verdict is "looks right" —
the goal is to highlight where Stage-2 NER materially enriched the
record rather than echoing what the cataloguer already wrote.

The index is built once per dialog refresh from ``marc_extracted.json``
and treated as read-only thereafter.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


_PUNCT_RE = re.compile(r"[\s,.;:\"'()\[\]{}<>!?\-—\/\\]+")


def _normalise(text: str) -> str:
    """Casefold + collapse punctuation so substring matches are tolerant.

    Hebrew script is preserved verbatim — only Latin case + whitespace +
    common ISBD punctuation are normalised away. The result is suitable
    for substring containment checks.
    """
    if not text:
        return ""
    out = text.strip()
    if not out:
        return ""
    out = out.casefold()
    out = _PUNCT_RE.sub(" ", out).strip()
    return out


def _yield_strings(value: Any) -> Iterable[str]:
    """Walk an arbitrary structure yielding every non-empty string leaf."""
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, (int, float, bool)):
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _yield_strings(v)
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _yield_strings(v)
        return


# MARC-derived fields that count as "structured information already in
# the catalog" for novelty purposes. Anything an NER model can plausibly
# repeat — names, titles, places, subjects — must live here so we don't
# falsely flag a repetition as novel.
_STRUCTURED_KEYS: tuple[str, ...] = (
    # Names (MARC 100/110/111/700/710/711/800/810/811)
    "contributors",
    "authors",
    # Subjects (MARC 600/610/611/650/651)
    "subjects",
    # Title block (MARC 245/240/246/247)
    "title",
    "title_variants",
    "uniform_title",
    "alternate_titles",
    # Genre / form (MARC 655)
    "genre_form",
    "genres",
    # Provenance and ownership notes (MARC 561/541/700 with role)
    "acquisition_source",
    "former_owners",
    "ownership_history",
    # Series (MARC 490/830)
    "series",
    # Contents / works listed in MARC 505
    "contents",
    "works",
    # Places (MARC 651/752)
    "places",
    "related_places",
)


def _record_key(record_id: str) -> str:
    """Reduce a manuscript URI / id to a comparable key.

    Stage-3 emits ``record_id`` values shaped like
    ``https://…/manuscript/990000…`` while ``marc_extracted.json``
    carries ``_control_number`` as the bare numeric string. Strip
    everything but the final segment so both shapes meet.
    """
    raw = str(record_id or "").strip()
    if not raw:
        return ""
    return raw.split("/")[-1]


class MarcStructuredIndex:
    """Per-record bag of normalised structured-field strings."""

    def __init__(self) -> None:
        self._by_id: dict[str, set[str]] = {}

    @classmethod
    def load(cls, marc_extracted_path: Path) -> "MarcStructuredIndex":
        """Build the index from ``marc_extracted.json``. Missing file → empty."""
        index = cls()
        path = Path(marc_extracted_path)
        if not path.exists():
            return index
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return index

        records: list[dict[str, Any]]
        if isinstance(data, list):
            records = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            records = [r for r in data.values() if isinstance(r, dict)]
        else:
            return index

        for record in records:
            key = _record_key(str(record.get("_control_number") or ""))
            if not key:
                continue
            bag: set[str] = set()
            for field_key in _STRUCTURED_KEYS:
                if field_key not in record:
                    continue
                for raw_str in _yield_strings(record[field_key]):
                    norm = _normalise(raw_str)
                    if not norm:
                        continue
                    bag.add(norm)
                    # MARC name fields are commonly in inverted "Surname,
                    # Given" form. NER emits "Given Surname". Drop comma-
                    # split tokens into the bag so either ordering of the
                    # name matches as a substring during is_novel().
                    if "," in raw_str:
                        for part in raw_str.split(","):
                            part_norm = _normalise(part)
                            if part_norm and len(part_norm) >= 2:
                                bag.add(part_norm)
            if bag:
                index._by_id[key] = bag
        return index

    def __len__(self) -> int:
        return len(self._by_id)

    def has(self, record_id: str) -> bool:
        """True when the index has any structured data for *record_id*."""
        return _record_key(record_id) in self._by_id

    def is_novel(self, record_id: str, candidate_text: str) -> bool:
        """Return True when *candidate_text* does NOT appear in the record's
        structured fields.

        We do tolerant substring matching in BOTH directions: the
        candidate is "already known" if either the candidate is a
        substring of any structured value OR any structured value is a
        substring of the candidate. This catches "ben Maimon" vs.
        "Moses ben Maimon" repetitions in both directions.

        Returns False when the record itself is unknown — we can't
        prove novelty without a reference, so we err on the safe side.
        """
        key = _record_key(record_id)
        bag = self._by_id.get(key)
        if not bag:
            return False
        needle = _normalise(candidate_text)
        if not needle:
            return False
        for entry in bag:
            if not entry:
                continue
            if needle in entry or entry in needle:
                return False
        return True


__all__ = ["MarcStructuredIndex"]
