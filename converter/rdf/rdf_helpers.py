"""Shared RDF graph helpers — label hygiene, role normalisation, geo sanity."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..config.vocabularies import ROLE_MAPPINGS

_INSTITUTIONAL_KEYWORDS: frozenset[str] = frozenset({
    "library",
    "collection",
    "archive",
    "archives",
    "museum",
    "university",
    "institute",
    "foundation",
    "trust",
    "seminary",
    "academy",
    "society",
    "בית",
    "ספרייה",
    "אוסף",
    "מכון",
    "אוניברסיטה",
    "bodleian",
    "palatina",
})


def clean_marc_label(text: str) -> str:
    """Strip MARC ISBD quote artifacts and surrounding whitespace."""
    if not text:
        return ""
    cleaned = text.strip().strip("\"'").strip()
    cleaned = cleaned.replace('""', '"')
    while cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) > 1:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def normalize_role(role: str | None) -> str:
    """Map MARC relator strings to canonical pipeline role tokens."""
    if not role:
        return "contributor"
    raw = clean_marc_label(str(role)).lower().strip().rstrip(".")
    if not raw:
        return "contributor"
    mapped = ROLE_MAPPINGS.get(raw)
    if mapped:
        return mapped
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return slug or "contributor"


def is_institutional_name(name: str) -> bool:
    """Heuristic: corporate holder / library names should be E74_Group."""
    lowered = clean_marc_label(name).lower()
    if not lowered:
        return False
    return any(kw in lowered for kw in _INSTITUTIONAL_KEYWORDS)


def infer_person_type(person_data: dict[str, Any]) -> str:
    """Return ``organization`` when the record should emit E74_Group."""
    explicit = str(person_data.get("type") or "").lower()
    if explicit in {"organization", "org", "corporate", "institution"}:
        return "organization"
    marc_field = str(person_data.get("field") or "")
    if marc_field in {"110", "710", "610", "810"}:
        return "organization"
    name = str(person_data.get("name") or "")
    if is_institutional_name(name):
        return "organization"
    return "person"


def is_plausible_coords(lat: float | int | str | None, lon: float | int | str | None) -> bool:
    """Reject swapped / garbage geocodes before writing WGS84 triples."""
    try:
        lat_f = float(lat)  # type: ignore[arg-type]
        lon_f = float(lon)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return False
    if abs(lat_f) < 0.01 and abs(lon_f) < 0.01:
        return False
    return True


def names_overlap(a: str, b: str) -> bool:
    """Case-insensitive bidirectional substring match for authority merge."""
    left = clean_marc_label(a).casefold()
    right = clean_marc_label(b).casefold()
    if not left or not right:
        return False
    return left == right or left in right or right in left


def person_dict_key(person: dict[str, Any]) -> str:
    return clean_marc_label(str(person.get("name") or "")).casefold()


def ensure_person_in_list(
    people: list[dict[str, Any]],
    name: str,
    *,
    role: str = "contributor",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find or append a person dict; return the mutable entry."""
    key = clean_marc_label(name).casefold()
    for person in people:
        if person_dict_key(person) == key:
            return person
    entry: dict[str, Any] = {
        "name": clean_marc_label(name),
        "role": normalize_role(role),
    }
    if extra:
        entry.update(extra)
    people.append(entry)
    return people[-1]
