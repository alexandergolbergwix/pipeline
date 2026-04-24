"""Shared biographical-data schema + extractors for the authority-match
review dialog.

Four sources feed into one common dataclass so the review UI can diff
MARC against Mazal / VIAF / KIMA side-by-side without each extractor
reinventing its own shape:

    MARC record   → extract_marc_biodata
    Mazal entry   → extract_mazal_biodata
    VIAF cluster  → extract_viaf_biodata
    KIMA entry    → extract_kima_biodata

Each returns :class:`BioData`. ``BioComparison`` pairs a MARC side with
an authority side for the review dialog.

The extractors are pure — no I/O, no network. Callers must already have
the authority blob in memory (fetched via the corresponding matcher).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BioData:
    """Canonical biographical snapshot for an entity.

    All fields use common keys so cross-source diffing is straightforward.
    Missing info is represented by an empty string / list / dict — never
    ``None`` — so equality comparisons short-circuit cleanly.
    """

    dates: dict[str, str] = field(default_factory=dict)
    """Keys: ``birth``, ``death``, ``floruit``, ``date_range``."""

    places: dict[str, list[str]] = field(default_factory=dict)
    """Keys: ``birth_place``, ``death_place``, ``associated_places``,
    ``country``, ``admin_region``, ``coords``."""

    names: dict[str, list[str]] = field(default_factory=dict)
    """Language-tagged names: ``he``, ``en``, ``lat``, ``ar``, ``und``
    (undetermined). Lists preserve the order encountered."""

    occupations: list[str] = field(default_factory=list)

    notes: list[str] = field(default_factory=list)
    """Free-text notes / biographical details — displayed in the Raw tab."""


@dataclass(frozen=True)
class BioComparison:
    """Side-by-side comparison of MARC-side vs authority-side bio data."""

    marc: BioData
    authority: BioData
    source: str  # "mazal" | "viaf" | "kima" | "marc_field"


# ── Extractor: MARC record ───────────────────────────────────────────────


def extract_marc_biodata(
    record: dict[str, Any] | None,
    row: dict[str, Any] | None = None,
) -> BioData:
    """Pull dates / places / occupations / names from a MARC record, scoped
    to a specific authority-match *row* when one is supplied.

    Reads the real authority-enriched schema produced by
    :func:`converter.transformer.field_handlers.extract_all_data` —
    ``authors``, ``contributors``, ``entities``, ``dates``, ``place``,
    ``related_places``, ``provenance``, ``marc_authority_matches`` —
    not the raw MARC tag names (which don't survive extraction).

    When *row* is provided, the extractor prefers biographical info
    that belongs to the SPECIFIC entity named by ``row['entity_text']``
    (the person/place being matched) over record-level fallbacks.
    """
    if not record and not row:
        return BioData()

    record = record or {}
    row = row or {}
    entity_text = str(row.get("entity_text") or "").strip()
    etype = str(row.get("match_type") or "").strip()
    role = str(row.get("role") or "").strip()

    dates: dict[str, str] = {}
    places: dict[str, list[str]] = {}
    names: dict[str, list[str]] = {}
    occupations: list[str] = []
    notes: list[str] = []

    # 1. Row-level signals (highest priority — this IS the matched entity)
    if entity_text:
        lang = "he" if _has_hebrew(entity_text) else "lat"
        names.setdefault(lang, []).append(entity_text)
    if role:
        occupations.append(role)
    row_dates = str(row.get("dates") or "").strip()
    if row_dates:
        if "-" in row_dates:
            b, _, d = row_dates.partition("-")
            if b.strip():
                dates["birth"] = b.strip()
            if d.strip():
                dates["death"] = d.strip()
        else:
            dates["date_range"] = row_dates

    # 2. Enrich with details from the matching entry in the record
    def _matches(candidate_name: Any) -> bool:
        if not candidate_name or not entity_text:
            return False
        return _has_token_overlap(str(candidate_name), entity_text)

    for key in ("authors", "contributors"):
        for entry in record.get(key) or []:
            if not isinstance(entry, dict):
                continue
            if entity_text and not _matches(entry.get("name")):
                continue
            for nk in ("name", "preferred_name", "hebrew_name", "latin_name"):
                nv = entry.get(nk)
                if nv and str(nv) not in {v for vs in names.values() for v in vs}:
                    lang = "he" if _has_hebrew(str(nv)) else "lat"
                    names.setdefault(lang, []).append(str(nv))
            ed = entry.get("dates") or entry.get("date_range")
            if ed and not dates:
                s = str(ed).strip()
                if "-" in s:
                    b, _, d = s.partition("-")
                    if b.strip():
                        dates["birth"] = b.strip()
                    if d.strip():
                        dates["death"] = d.strip()
                else:
                    dates["date_range"] = s
            er = entry.get("role")
            if er and er not in occupations:
                occupations.append(str(er))

    # 3. NER entities carry role + surrounding context
    for ent in record.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        ename = ent.get("person") or ent.get("text") or ent.get("place")
        if entity_text and not _matches(ename):
            continue
        er = ent.get("role")
        if er and er not in occupations:
            occupations.append(str(er))

    # 4. Place matches — record-level places are the match targets
    if etype == "place" and entity_text:
        for k, label in (("place", "place"),):
            v = record.get(k)
            if v and str(v) == entity_text:
                places.setdefault(label, []).append(str(v))
        for rp in record.get("related_places") or []:
            if isinstance(rp, dict):
                rn = rp.get("name") or rp.get("place")
                if rn and (not entity_text or _matches(rn)):
                    places.setdefault("associated_places", []).append(str(rn))
            elif isinstance(rp, str) and (not entity_text or _matches(rp)):
                places.setdefault("associated_places", []).append(rp)

    # 5. Record-level fallbacks — always add as context
    rec_dates = record.get("dates")
    if isinstance(rec_dates, dict):
        for k in ("year", "start", "end", "range"):
            v = rec_dates.get(k)
            if v and "manuscript_date" not in dates:
                dates["manuscript_date"] = str(v)
                break
    place = record.get("place")
    if place and "manuscript_place" not in places:
        places["manuscript_place"] = [str(place)]
    for lang in record.get("languages") or []:
        if lang:
            notes.append(f"manuscript language: {lang}")
    cn = record.get("_control_number")
    if cn:
        notes.append(f"MARC control number: {cn}")
    if row.get("field_origin"):
        notes.append(f"match field: {row['field_origin']}")
    if row.get("matched_id"):
        notes.append(f"authority id: {row['matched_id']}")

    return BioData(
        dates=dates,
        places=places,
        names=names,
        occupations=occupations,
        notes=notes,
    )


# ── Extractor: Mazal authority record ───────────────────────────────────


def extract_mazal_biodata(entry: dict[str, Any] | None) -> BioData:
    """Build BioData from a Mazal index row (``MazalIndex.get_record``).

    The current Mazal SQLite schema is slim (nli_id, preferred_name_heb,
    preferred_name_lat, dates, entity_type). Extended fields (places,
    occupations) are populated only when Mazal has been rebuilt with
    the v2 rich-schema option or when the caller supplies pre-parsed
    data via ``entry["extended"]``.
    """
    if not entry:
        return BioData()

    names: dict[str, list[str]] = {}
    heb = entry.get("preferred_name_heb")
    lat = entry.get("preferred_name_lat")
    if heb:
        names.setdefault("he", []).append(str(heb))
    if lat:
        names.setdefault("lat", []).append(str(lat))
    for v in entry.get("variants") or []:
        script = "he" if _has_hebrew(str(v)) else "lat"
        names.setdefault(script, []).append(str(v))

    dates: dict[str, str] = {}
    raw_dates = entry.get("dates") or ""
    if raw_dates:
        # Mazal stores "birth-death" e.g. "1138-1204" or just "1204"
        s = str(raw_dates).strip()
        if "-" in s:
            b, _, d = s.partition("-")
            if b.strip():
                dates["birth"] = b.strip()
            if d.strip():
                dates["death"] = d.strip()
        else:
            dates["date_range"] = s

    places: dict[str, list[str]] = {}
    for k in ("birth_place", "death_place", "associated_places"):
        v = entry.get(k)
        if v:
            places[k] = [str(v)] if isinstance(v, str) else [str(x) for x in v]

    occupations = list(entry.get("occupations") or [])
    notes: list[str] = []
    if entry.get("nli_id"):
        notes.append(f"NLI authority: {entry['nli_id']}")
    if entry.get("aleph_id"):
        notes.append(f"Aleph: {entry['aleph_id']}")
    for note in entry.get("notes") or []:
        notes.append(str(note))

    return BioData(
        dates=dates,
        places=places,
        names=names,
        occupations=occupations,
        notes=notes,
    )


# ── Extractor: VIAF cluster JSON ────────────────────────────────────────


def extract_viaf_biodata(cluster: dict[str, Any] | None) -> BioData:
    """Walk a VIAF cluster JSON and surface dates/places/occupations.

    VIAF cluster JSON is deeply nested — we use the conservative walker
    :func:`_iter_nested` so small API changes don't break us.
    """
    if not cluster:
        return BioData()

    # VIAF wraps the cluster in ``ns1:VIAFCluster`` or similar.
    # Normalise: unwrap a single-key top level.
    if isinstance(cluster, dict) and len(cluster) == 1:
        inner = next(iter(cluster.values()))
        if isinstance(inner, dict):
            cluster = inner

    names: dict[str, list[str]] = {}
    # mainHeadings.data → list of {text, sources:{s: ["NLI",...]}}
    for head in _iter_nested(cluster, "mainHeadings", "data"):
        text = _unwrap_text(head.get("text") if isinstance(head, dict) else None)
        if not text:
            continue
        srcs = head.get("sources", {}) if isinstance(head, dict) else {}
        code_list = _iter_inner(srcs, "s")
        # Map provenance to language: NLI → he, LC/LoC/BNF → lat/en, ...
        lang = "lat"
        if any("NLI" in str(c) for c in code_list):
            lang = "he"
        if _has_hebrew(text):
            lang = "he"
        names.setdefault(lang, []).append(text)

    # x400s (alt names)
    for alt in _iter_nested(cluster, "x400s", "x400"):
        if not isinstance(alt, dict):
            continue
        t = _unwrap_text(alt.get("datafield", {}).get("subfield"))
        if t:
            lang = "he" if _has_hebrew(t) else "lat"
            names.setdefault(lang, []).append(t)

    dates: dict[str, str] = {}
    bd = cluster.get("birthDate") or cluster.get("ns1:birthDate")
    dd = cluster.get("deathDate") or cluster.get("ns1:deathDate")
    if bd and str(bd) not in ("0", "00000000"):
        dates["birth"] = str(bd)
    if dd and str(dd) not in ("0", "00000000"):
        dates["death"] = str(dd)

    places: dict[str, list[str]] = {}
    nat = _iter_inner(cluster.get("nationalityOfEntity") or {}, "data")
    for n in nat:
        if isinstance(n, dict):
            t = _unwrap_text(n.get("text"))
            if t:
                places.setdefault("country", []).append(t)
        elif isinstance(n, str):
            places.setdefault("country", []).append(n)

    occupations: list[str] = []
    for occ in _iter_nested(cluster, "occupation", "data"):
        if isinstance(occ, dict):
            t = _unwrap_text(occ.get("text"))
            if t:
                occupations.append(t)
        elif isinstance(occ, str):
            occupations.append(occ)

    notes: list[str] = []
    viaf_id = cluster.get("viafID") or cluster.get("ns1:viafID")
    if viaf_id:
        notes.append(f"VIAF: {viaf_id}")
    for act in _iter_nested(cluster, "fieldOfActivity", "data"):
        t = _unwrap_text(act.get("text") if isinstance(act, dict) else act)
        if t:
            notes.append(f"field: {t}")

    return BioData(
        dates=dates,
        places=places,
        names=names,
        occupations=occupations,
        notes=notes,
    )


# ── Extractor: KIMA place ───────────────────────────────────────────────


def extract_kima_biodata(entry: dict[str, Any] | None) -> BioData:
    if not entry:
        return BioData()

    names: dict[str, list[str]] = {}
    for key, lang in (("name", "en"), ("name_en", "en"), ("name_he", "he"),
                       ("name_ar", "ar")):
        v = entry.get(key)
        if v:
            names.setdefault(lang, []).append(str(v))
    for v in entry.get("variants_he") or []:
        names.setdefault("he", []).append(str(v))
    for v in entry.get("variants_en") or []:
        names.setdefault("en", []).append(str(v))

    places: dict[str, list[str]] = {}
    lat, lon = entry.get("lat"), entry.get("lon")
    if lat is not None and lon is not None:
        places["coords"] = [f"{lat}, {lon}"]
    for k in ("country", "admin_region", "region"):
        v = entry.get(k)
        if v:
            places.setdefault(k, []).append(str(v))

    notes: list[str] = []
    ts = entry.get("time_span")
    if ts:
        notes.append(f"time span: {ts}")
    if entry.get("kima_id"):
        notes.append(f"KIMA: {entry['kima_id']}")

    return BioData(
        dates={},
        places=places,
        names=names,
        occupations=[],
        notes=notes,
    )


# ── Helpers ─────────────────────────────────────────────────────────────

_HEBREW_RANGE = range(0x0590, 0x0600)


def _has_hebrew(text: str) -> bool:
    return any(ord(c) in _HEBREW_RANGE for c in text)


# Name-joining particles that should NOT count as matching tokens.
# Hebrew: "ben"/"bat"/"ibn" (son-of/daughter-of); Latin: "de"/"di"/
# "da"/"von"/"van"/"of"/"the"; short honorifics and initials.
_NAME_STOPWORDS: frozenset[str] = frozenset({
    # Hebrew
    "בן", "בת", "ב'", "ב\"ר", "בר",
    # Latin / Romance / Germanic particles
    "ben", "ibn", "aben", "abu", "abou",
    "de", "di", "da", "du", "des", "del", "della", "delle",
    "von", "van", "vom", "der", "den",
    "le", "la", "les", "el", "al",
    "of", "the", "and",
    # One-letter initials (hebrew + latin)
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j",
    "k", "l", "m", "n", "o", "p", "q", "r", "s", "t",
    "u", "v", "w", "x", "y", "z",
    "א", "ב", "ג", "ד", "ה", "ו", "ז", "ח", "ט", "י",
    "כ", "ל", "מ", "נ", "ס", "ע", "פ", "צ", "ק", "ר", "ש", "ת",
})


def _tokenize_name(s: str) -> set[str]:
    """Tokenise *s* into meaningful name parts, stripping particles +
    punctuation + single-letter initials."""
    import re
    import unicodedata

    n = unicodedata.normalize("NFKC", s).casefold()
    parts = re.split(r"[\s,.;:/()\-\"'\u05BE\u2013\u2014]+", n)
    return {p for p in parts if len(p) >= 2 and p not in _NAME_STOPWORDS}


def _has_token_overlap(a: str, b: str) -> bool:
    """Name-scoped matcher: True if *a* and *b* share enough
    meaningful tokens (after particle-stripping) to plausibly refer to
    the same person. Requires ≥2 overlapping tokens OR the shorter
    side is fully covered by the longer.

    Deliberately strict to avoid sweeping in every ``<given> בן <father>``
    name that shares the surname particle with the query.
    """
    a_tokens = _tokenize_name(a)
    b_tokens = _tokenize_name(b)
    if not a_tokens or not b_tokens:
        return False
    overlap = a_tokens & b_tokens
    if not overlap:
        return False
    shorter = a_tokens if len(a_tokens) <= len(b_tokens) else b_tokens
    # Full containment of the shorter side is sufficient (e.g., "yaaqov"
    # matches "yaaqov ben avraham"). Otherwise require ≥2 overlaps.
    if overlap >= shorter:
        return True
    return len(overlap) >= 2


def _iter_nested(obj: Any, *keys: str) -> list[Any]:
    """Walk dict-of-dict path ``keys`` and return the final list of items.

    Lenient: missing keys → empty list, and a final scalar value is
    wrapped into a single-element list so callers don't have to branch.
    """
    current: Any = obj
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k) or current.get(f"ns1:{k}") or {}
        else:
            return []
    if current is None or current == {}:
        return []
    if isinstance(current, list):
        return current
    return [current]


def _iter_inner(obj: Any, key: str) -> list[Any]:
    """Accept either {key: x} or {key: [x, y]} or bare list/str; always
    return a list."""
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        v = obj.get(key) or obj.get(f"ns1:{key}")
        if v is None:
            return []
        return v if isinstance(v, list) else [v]
    return [obj]


def _unwrap_text(val: Any) -> str:
    """Pull a string out of VIAF's `{"#text": "..."}` wrapping or a bare
    string / list-of-strings."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return str(val.get("#text") or val.get("content") or "").strip()
    if isinstance(val, list):
        return ", ".join(_unwrap_text(x) for x in val if x)
    return str(val)


__all__ = [
    "BioData",
    "BioComparison",
    "extract_marc_biodata",
    "extract_mazal_biodata",
    "extract_viaf_biodata",
    "extract_kima_biodata",
]
