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


def extract_marc_biodata(record: dict[str, Any] | None) -> BioData:
    """Pull dates / places / occupations / names from a MARC record.

    Reads the fields the pipeline already stores on the authority-
    enriched record: 046, 368, 370, 372, 373, 374, 375, 377, plus
    100/700/710 sub-fields.
    """
    if not record:
        return BioData()

    dates: dict[str, str] = {}
    places: dict[str, list[str]] = {}
    names: dict[str, list[str]] = {}
    occupations: list[str] = []
    notes: list[str] = []

    # 046 — special coded dates
    f046 = record.get("marc_046") or record.get("046") or {}
    if isinstance(f046, dict):
        birth = f046.get("f") or f046.get("birth_date")
        death = f046.get("g") or f046.get("death_date")
        if birth:
            dates["birth"] = str(birth)
        if death:
            dates["death"] = str(death)

    # 370 — associated place (birth / death / residence)
    f370 = record.get("marc_370") or record.get("370") or {}
    if isinstance(f370, dict):
        for code, key in (("a", "birth_place"), ("b", "death_place"),
                           ("c", "country"), ("f", "residence")):
            v = f370.get(code)
            if v:
                places.setdefault(key, []).append(str(v))

    # 374 — occupation
    for fx in (record.get("marc_374") or record.get("374") or []):
        if isinstance(fx, dict):
            v = fx.get("a") or fx.get("occupation")
            if v:
                occupations.append(str(v))
        elif isinstance(fx, str):
            occupations.append(fx)

    # 372 — field of activity
    for fx in (record.get("marc_372") or record.get("372") or []):
        if isinstance(fx, dict):
            v = fx.get("a")
            if v:
                notes.append(f"field of activity: {v}")

    # 375 — gender
    g = record.get("marc_375") or record.get("gender")
    if g:
        notes.append(f"gender: {g}")

    # 377 — language
    for lang in (record.get("marc_377") or record.get("languages") or []):
        if lang:
            notes.append(f"language: {lang}")

    # 100/700/710 names — the primary + added entries
    for tag in ("marc_100", "marc_700", "marc_710"):
        entries = record.get(tag) or []
        if isinstance(entries, dict):
            entries = [entries]
        for e in entries:
            if not isinstance(e, dict):
                continue
            name = e.get("a") or e.get("name")
            d = e.get("d")       # dates on name field
            if name:
                # Heuristic: Hebrew script → "he", Latin → "lat"
                lang = "he" if _has_hebrew(str(name)) else "lat"
                names.setdefault(lang, []).append(str(name))
            if d and "date_range" not in dates:
                dates["date_range"] = str(d)

    # Fallback: authority_matches carried on the record
    for m in (record.get("marc_authority_matches") or []):
        if not isinstance(m, dict):
            continue
        mn = m.get("name")
        if mn:
            lang = "he" if _has_hebrew(str(mn)) else "lat"
            names.setdefault(lang, []).append(str(mn))

    # Record-level fallbacks
    ds = record.get("dates")
    if isinstance(ds, dict):
        for k in ("year", "start", "end"):
            v = ds.get(k)
            if v and "date_range" not in dates:
                dates["date_range"] = str(v)
                break

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
