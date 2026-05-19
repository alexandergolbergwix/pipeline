"""Build a coverage-maximizing test subset from the 17th-century corpus.

Produces ``data/tsvs/test_subset.tsv`` plus a JSON manifest describing the
selection. The subset is intended to replace ``top100_richest.tsv`` as the
single source of truth pinned by the paper-claim verification harness.

Selection algorithm (three stages):

1. **Hard-signal force-include** — for each of the 10 hardest-to-trigger
   coverage signals, force-include the record with the highest total
   signal count among hits.
2. **Greedy set-cover** — repeatedly pick the record that covers the
   most still-uncovered signals; ties broken by total signal count then
   by stable hash of the record id.
3. **Stratified-typical fill** — top up to the baseline target with a
   stratified random sample of "typical" records (50/50 with vs without
   MARC 561, proportional to corpus on MARC 700, proportional by decade
   of MARC 008 date).

Cap enforcement: if the total exceeds ``--cap``, stage-3 picks are
dropped first, then redundant stage-2 picks (those whose signals are
already covered by another retained record).

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/build_test_subset.py [--verbose]

Exit codes:
    0  success
    2  source missing or empty
    3  some signals had zero hits in the corpus (still writes outputs unless --dry-run)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

_REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE = _REPO_ROOT / "data" / "tsvs" / "17th_century_samples.tsv"
DEFAULT_OUT = _REPO_ROOT / "data" / "tsvs" / "test_subset.tsv"
DEFAULT_MANIFEST = _REPO_ROOT / "data" / "tsvs" / "test_subset_manifest.json"
DEFAULT_SUPPLEMENT = _REPO_ROOT / "data" / "tsvs" / "filtered_manuscripts_after_906a.tsv"

# CSV field-size limit must be raised — MARC 505 contents notes can run megabytes.
csv.field_size_limit(sys.maxsize)

PredicateFn = Callable[[dict[str, str]], bool]
Record = dict[str, str]


# ──────────────────────────────────────────────────────────────────────────────
# Cell parsing helpers
# ──────────────────────────────────────────────────────────────────────────────


_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_GREEK_RE = re.compile(r"[\u0370-\u03FF]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


def _clean(cell: str) -> str:
    """Strip MARC TSV quoting (`""…""`) and normalise whitespace.

    Cells produced by the pipeline are wrapped in *triple* double quotes
    ``\"\"\"…\"\"\"`` (Python repr of a quoted string) and embedded quotes
    are doubled (``\"\"``). This helper unwraps both layers.
    """
    if cell is None:
        return ""
    s = cell.strip()
    if not s:
        return ""
    if s.startswith('"""') and s.endswith('"""') and len(s) >= 6:
        s = s[3:-3]
    elif s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1]
    s = s.replace('""', '"').strip()
    return s


def _parts(cell: str) -> list[str]:
    """Split a multi-instance cell on ``|`` into non-empty cleaned tokens."""
    raw = _clean(cell)
    if not raw:
        return []
    return [p.strip() for p in raw.split("|") if p.strip()]


def _has(record: Record, column: str) -> bool:
    return bool(_clean(record.get(column, "")))


def _any(record: Record, columns: Iterable[str]) -> bool:
    return any(_has(record, c) for c in columns)


def _record_id(record: Record) -> str:
    """Stable identifier — uses MARC 001 (control number) which is unique
    per record. The File column is the source filename and may collide
    across records (e.g. one MARC file contributes many records).
    Falls back to File if 001 is absent."""
    rid = _clean(record.get("001", ""))
    if rid:
        return rid
    return _clean(record.get("File", "")) or "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Signal predicates (65 signals)
# ──────────────────────────────────────────────────────────────────────────────


def _has_marc008_date(rec: Record) -> bool:
    return bool(_extract_008_year(rec))


def _extract_008_year(rec: Record) -> int | None:
    raw = _clean(rec.get("008", ""))
    if len(raw) < 11:
        return None
    yfield = raw[7:11]
    if yfield.isdigit():
        return int(yfield)
    return None


def _has_hebrew_century(rec: Record) -> bool:
    """260c contains a Hebrew-letter century pattern (e.g. 'מאה ט"ז')."""
    text = " ".join(_parts(rec.get("260$c", "")) + _parts(rec.get("260$d", "")))
    if not text:
        return False
    return bool(re.search(r"מאה\s*[\u05D0-\u05EA]", text))


def _has_hebrew_year(rec: Record) -> bool:
    """260c contains a Hebrew-letter year (gematria) followed by Gregorian in parens."""
    text = " ".join(_parts(rec.get("260$c", "")))
    if not text:
        return False
    return bool(re.search(r'[\u05D0-\u05EA]"[\u05D0-\u05EA]', text))


def _has_pre_1582_date(rec: Record) -> bool:
    y = _extract_008_year(rec)
    return y is not None and y < 1582


def _has_post_1582_date(rec: Record) -> bool:
    y = _extract_008_year(rec)
    return y is not None and y >= 1582


def _has_role(rec: Record, role_substr: str) -> bool:
    for col in ("100$e", "700$e", "710$e"):
        for tok in _parts(rec.get(col, "")):
            if role_substr in tok:
                return True
    return False


def _has_role_scribe(rec: Record) -> bool:
    return any(
        _has_role(rec, kw)
        for kw in ("מעתיק", "סופר", "כתבן", "נקדן", "scribe", "copyist")
    )


def _has_role_translator(rec: Record) -> bool:
    return any(_has_role(rec, kw) for kw in ("מתרגם", "translator"))


def _has_role_commentator(rec: Record) -> bool:
    return any(
        _has_role(rec, kw)
        for kw in ("מפרש", "commentator", "מבאר", "פרשן")
    )


def _has_role_owner(rec: Record) -> bool:
    return any(_has_role(rec, kw) for kw in ("בעלים", "former owner", "owner"))


def _has_role_editor(rec: Record) -> bool:
    return any(_has_role(rec, kw) for kw in ("עורך", "editor"))


def _hebrew_text_in(rec: Record, columns: Iterable[str]) -> bool:
    for col in columns:
        if _HEBREW_RE.search(_clean(rec.get(col, ""))):
            return True
    return False


def _has_hebrew_title(rec: Record) -> bool:
    return _hebrew_text_in(rec, ("245$a", "245$b", "245$p"))


def _has_latin_title(rec: Record) -> bool:
    for col in ("245$a", "245$b"):
        if _LATIN_RE.search(_clean(rec.get(col, ""))):
            return True
    return False


def _has_880_field(rec: Record) -> bool:
    """MARC 880 alternate-graphic-representation linked field."""
    return _any(rec, ("880$a", "880$b", "880$c", "880$e", "880$k", "880$6"))


def _has_variant_title(rec: Record) -> bool:
    return _any(rec, ("246$a", "246$b", "246$p", "740$a", "740$p"))


def _has_uniform_title(rec: Record) -> bool:
    return _any(rec, ("240$a", "130$a", "730$a"))


def _has_isbd_period(rec: Record) -> bool:
    """Title ends with a trailing ISBD ``.`` (signal #5 in I3 hardness ranking)."""
    title = _clean(rec.get("245$a", ""))
    return title.endswith(".")


def _has_placeholder_title(rec: Record) -> bool:
    """Generic catalog placeholder (קובץ., קובץ בקבלה. etc.)."""
    title = _clean(rec.get("245$a", "")).strip().rstrip(".")
    if not title:
        return False
    return title in {
        "קובץ",
        "קובץ בקבלה",
        "קובץ בהלכה",
        "קובץ במדרש",
        "קובץ בפיוטים",
        "מקובץ",
    }


def _has_lang_arabic(rec: Record) -> bool:
    code = _clean(rec.get("041$a", ""))
    return "ara" in code or "jrb" in code or _ARABIC_RE.search(_clean(rec.get("245$a", ""))) is not None


def _has_lang_aramaic(rec: Record) -> bool:
    code = _clean(rec.get("041$a", ""))
    return "arc" in code or "jpa" in code or "syr" in code


def _has_lang_yiddish(rec: Record) -> bool:
    code = _clean(rec.get("041$a", ""))
    return "yid" in code or "jib" in code


def _has_lang_ladino(rec: Record) -> bool:
    code = _clean(rec.get("041$a", ""))
    return "lad" in code


def _has_lang_latin(rec: Record) -> bool:
    code = _clean(rec.get("041$a", ""))
    return "lat" in code


def _has_lang_greek(rec: Record) -> bool:
    code = _clean(rec.get("041$a", ""))
    if "grc" in code or "gre" in code:
        return True
    return _GREEK_RE.search(_clean(rec.get("245$a", ""))) is not None


def _has_lang_cyrillic(rec: Record) -> bool:
    return _CYRILLIC_RE.search(_clean(rec.get("245$a", ""))) is not None


def _has_multilang(rec: Record) -> bool:
    """MARC 041 lists more than one language code."""
    code = _clean(rec.get("041$a", ""))
    if not code:
        return False
    cleaned = re.sub(r"[\s,;|]+", " ", code).strip()
    parts = [p for p in cleaned.split(" ") if len(p) >= 2]
    return len(parts) >= 2


def _has_translation(rec: Record) -> bool:
    return _has(rec, "041$h")


def _has_genre_marc655(rec: Record) -> bool:
    return _has(rec, "655$a")


def _has_genre_multiple(rec: Record) -> bool:
    return len(_parts(rec.get("655$a", ""))) >= 2


def _has_subject_lcsh(rec: Record) -> bool:
    return _has(rec, "650$a")


def _has_subject_bible(rec: Record) -> bool:
    """MARC 630 + Bible book heading (Hebrew or Latin)."""
    text = " ".join(_parts(rec.get("630$a", ""))) + " " + " ".join(
        _parts(rec.get("630$p", ""))
    )
    if not text.strip():
        return False
    bible_kw = (
        "תנ\"ך",
        "תנך",
        "תורה",
        "נביאים",
        "כתובים",
        "Bible",
        "Pentateuch",
        "Psalms",
        "Genesis",
        "Exodus",
    )
    return any(kw in text for kw in bible_kw)


def _has_subject_talmud(rec: Record) -> bool:
    text = " ".join(_parts(rec.get("630$a", ""))) + " " + " ".join(
        _parts(rec.get("630$p", ""))
    )
    if not text.strip():
        return False
    return "תלמוד" in text or "Talmud" in text or "Mishnah" in text or "משנה" in text


def _has_corporate_subject(rec: Record) -> bool:
    return _any(rec, ("610$a",))


def _has_meeting_subject(rec: Record) -> bool:
    return _any(rec, ("611$a",))


def _has_geographic_subject(rec: Record) -> bool:
    return _any(rec, ("651$a", "751$a"))


def _has_personal_subject(rec: Record) -> bool:
    return _any(rec, ("600$a",))


def _has_provenance_561(rec: Record) -> bool:
    return _has(rec, "561$a")


def _has_provenance_long(rec: Record) -> bool:
    """Long MARC 561 (≥ 200 chars) — multi-owner chain, exercises NER."""
    return len(_clean(rec.get("561$a", ""))) >= 200


def _has_provenance_multi(rec: Record) -> bool:
    """MARC 561 with multiple ``|``-separated entries (multi-owner chain)."""
    return len(_parts(rec.get("561$a", ""))) >= 2


def _has_acquisition_541(rec: Record) -> bool:
    return _any(rec, ("541$a", "541$b", "541$n"))


def _has_main_entry_person(rec: Record) -> bool:
    return _has(rec, "100$a")


def _has_main_entry_corporate(rec: Record) -> bool:
    return _has(rec, "110$a")


def _has_added_entry_person(rec: Record) -> bool:
    return _has(rec, "700$a")


def _has_added_entry_corporate(rec: Record) -> bool:
    return _has(rec, "710$a")


def _has_added_entry_meeting(rec: Record) -> bool:
    return _has(rec, "711$a")


def _has_summary_520(rec: Record) -> bool:
    return _has(rec, "520$a")


def _has_contents_505(rec: Record) -> bool:
    return _has(rec, "505$a")


def _has_long_contents_505(rec: Record) -> bool:
    return len(_clean(rec.get("505$a", ""))) >= 1000


def _has_general_note_500(rec: Record) -> bool:
    return _has(rec, "500$a")


def _has_long_notes(rec: Record) -> bool:
    """Combined MARC 500 length ≥ 500 chars — exercises sentence classifier."""
    return len(_clean(rec.get("500$a", ""))) >= 500


def _has_colophon_keyword_500(rec: Record) -> bool:
    text = _clean(rec.get("500$a", ""))
    return any(kw in text for kw in ("קולופון", "colophon", "נשלם"))


def _has_dimensions_300(rec: Record) -> bool:
    return _any(rec, ("300$a", "300$b", "300$c", "300$f"))


def _has_extent_300a(rec: Record) -> bool:
    return _has(rec, "300$a")


def _has_physical_description_340(rec: Record) -> bool:
    return _any(rec, ("340$a", "340$b", "340$c", "340$d"))


def _has_url_856(rec: Record) -> bool:
    return _has(rec, "856$u")


def _has_reproduction_533(rec: Record) -> bool:
    return _any(rec, ("533$a", "533$3", "533$e"))


def _has_publication_history_534(rec: Record) -> bool:
    return _any(rec, ("534$c", "534$p"))


def _has_funding_536(rec: Record) -> bool:
    return _has(rec, "536$a")


def _has_rights_540(rec: Record) -> bool:
    return _any(rec, ("540$a", "540$u"))


def _has_owner_562(rec: Record) -> bool:
    return _any(rec, ("562$a", "562$b"))


def _has_action_583(rec: Record) -> bool:
    return _any(rec, ("583$a", "583$b", "583$c"))


def _has_local_note_590(rec: Record) -> bool:
    return _any(rec, ("590$a", "594$a", "595$a", "596$a", "597$a"))


def _has_series_490(rec: Record) -> bool:
    return _any(rec, ("490$a", "830$a"))


def _has_origin_place_751(rec: Record) -> bool:
    return _has(rec, "751$a")


def _has_publication_place_260a(rec: Record) -> bool:
    return _has(rec, "260$a")


def _has_publisher_260b(rec: Record) -> bool:
    return _has(rec, "260$b")


def _has_main_entry_with_dates(rec: Record) -> bool:
    return _has(rec, "100$d")


def _has_added_entry_with_dates(rec: Record) -> bool:
    return _has(rec, "700$d")


def _has_authority_id_local(rec: Record) -> bool:
    """MARC 100/700 $9 carries a local NLI authority key."""
    return _any(rec, ("100$9", "700$9"))


def _has_form_genre_term(rec: Record) -> bool:
    return _has(rec, "655$2")


def _has_inception_046(rec: Record) -> bool:
    return _any(rec, ("046$a", "046$b", "046$d"))


def _has_geo_coords_034(rec: Record) -> bool:
    return _any(rec, ("034$d", "034$e", "034$f", "034$g"))


def _has_call_number_050_or_090(rec: Record) -> bool:
    return _any(rec, ("050$a", "090$a", "091$a"))


def _has_extra_holding_500_3(rec: Record) -> bool:
    """MARC 500 with $3 'materials specified' — codicological-unit signal."""
    return _has(rec, "500$3")


def _has_codicological_unit_marker(rec: Record) -> bool:
    """Multiple MARC 500 entries (multi-CU manuscript)."""
    return len(_parts(rec.get("500$a", ""))) >= 3


def _has_part_designations(rec: Record) -> bool:
    return _any(rec, ("245$n", "245$p", "246$n", "246$p"))


def _has_alt_script_245(rec: Record) -> bool:
    """MARC 245 has an 880 link ($6) — non-Hebrew transliteration linked."""
    return _has(rec, "245$6")


# (id, predicate)
_SIGNALS: list[tuple[str, PredicateFn]] = [
    # Dates / chronology (8)
    ("DATE_008", _has_marc008_date),
    ("DATE_HEBREW_CENTURY", _has_hebrew_century),
    ("DATE_HEBREW_YEAR", _has_hebrew_year),
    ("DATE_PRE_1582_JULIAN", _has_pre_1582_date),
    ("DATE_POST_1582_GREGORIAN", _has_post_1582_date),
    ("DATE_INCEPTION_046", _has_inception_046),
    ("DATE_PUB_260C", lambda r: _has(r, "260$c")),
    ("DATE_PUB_264C", lambda r: _has(r, "264$c")),
    # Roles (5)
    ("ROLE_SCRIBE", _has_role_scribe),
    ("ROLE_TRANSLATOR", _has_role_translator),
    ("ROLE_COMMENTATOR", _has_role_commentator),
    ("ROLE_OWNER", _has_role_owner),
    ("ROLE_EDITOR", _has_role_editor),
    # Languages (8)
    ("LANG_HEBREW_TITLE", _has_hebrew_title),
    ("LANG_LATIN_TITLE", _has_latin_title),
    ("LANG_ARABIC", _has_lang_arabic),
    ("LANG_ARAMAIC", _has_lang_aramaic),
    ("LANG_YIDDISH", _has_lang_yiddish),
    ("LANG_LADINO", _has_lang_ladino),
    ("LANG_LATIN", _has_lang_latin),
    ("LANG_GREEK", _has_lang_greek),
    ("LANG_MULTI_041", _has_multilang),
    ("LANG_TRANSLATION_041H", _has_translation),
    # Titles (6)
    ("TITLE_HEBREW", _has_hebrew_title),
    ("TITLE_VARIANT_246_740", _has_variant_title),
    ("TITLE_UNIFORM_240_130", _has_uniform_title),
    ("TITLE_ISBD_PERIOD", _has_isbd_period),
    ("TITLE_PLACEHOLDER_KOVETZ", _has_placeholder_title),
    ("TITLE_PART_DESIGNATIONS", _has_part_designations),
    ("TITLE_LINKED_880", _has_alt_script_245),
    ("FIELD_880_PRESENT", _has_880_field),
    # Genres / subjects (8)
    ("GENRE_655_PRESENT", _has_genre_marc655),
    ("GENRE_655_MULTIPLE", _has_genre_multiple),
    ("GENRE_FORM_TERM_2", _has_form_genre_term),
    ("SUBJECT_LCSH_650", _has_subject_lcsh),
    ("SUBJECT_BIBLE_630", _has_subject_bible),
    ("SUBJECT_TALMUD_630", _has_subject_talmud),
    ("SUBJECT_PERSON_600", _has_personal_subject),
    ("SUBJECT_CORP_610", _has_corporate_subject),
    ("SUBJECT_MEETING_611", _has_meeting_subject),
    ("SUBJECT_GEO_651_751", _has_geographic_subject),
    # Provenance (4)
    ("PROVENANCE_561", _has_provenance_561),
    ("PROVENANCE_561_LONG", _has_provenance_long),
    ("PROVENANCE_561_MULTI", _has_provenance_multi),
    ("PROVENANCE_ACQUISITION_541", _has_acquisition_541),
    ("PROVENANCE_OWNER_562", _has_owner_562),
    # Contributors (5)
    ("CONTRIB_MAIN_PERSON_100", _has_main_entry_person),
    ("CONTRIB_MAIN_CORP_110", _has_main_entry_corporate),
    ("CONTRIB_ADDED_PERSON_700", _has_added_entry_person),
    ("CONTRIB_ADDED_CORP_710", _has_added_entry_corporate),
    ("CONTRIB_ADDED_MEETING_711", _has_added_entry_meeting),
    ("CONTRIB_DATES_100D", _has_main_entry_with_dates),
    ("CONTRIB_DATES_700D", _has_added_entry_with_dates),
    ("AUTH_ID_LOCAL_9", _has_authority_id_local),
    # Notes / contents (6)
    ("NOTE_500", _has_general_note_500),
    ("NOTE_500_LONG", _has_long_notes),
    ("NOTE_500_COLOPHON_KW", _has_colophon_keyword_500),
    ("NOTE_500_3_MATERIALS", _has_extra_holding_500_3),
    ("CU_MULTIPLE_500", _has_codicological_unit_marker),
    ("CONTENTS_505", _has_contents_505),
    ("CONTENTS_505_LONG", _has_long_contents_505),
    ("SUMMARY_520", _has_summary_520),
    # Codicology / physical (4)
    ("EXTENT_300A", _has_extent_300a),
    ("PHYSICAL_300_FULL", _has_dimensions_300),
    ("MATERIAL_340", _has_physical_description_340),
    # Misc / external (6)
    ("URL_856", _has_url_856),
    ("REPRO_533", _has_reproduction_533),
    ("PUB_HISTORY_534", _has_publication_history_534),
    ("FUNDING_536", _has_funding_536),
    ("RIGHTS_540", _has_rights_540),
    ("ACTION_583", _has_action_583),
    ("LOCAL_NOTE_59X", _has_local_note_590),
    ("SERIES_490_830", _has_series_490),
    # Geography / publication (3)
    ("ORIGIN_PLACE_751", _has_origin_place_751),
    ("PUB_PLACE_260A", _has_publication_place_260a),
    ("PUB_PUBLISHER_260B", _has_publisher_260b),
    ("GEO_COORDS_034", _has_geo_coords_034),
    # Identifiers (1)
    ("CALL_NUMBER_050_090", _has_call_number_050_or_090),
]


# 10 hardest signals (by expected hardness, per CLAUDE.md Rule 18 + intuition).
# Stage 1 force-includes the top scoring record on each.
_HARD_SIGNALS: tuple[str, ...] = (
    "DATE_PRE_1582_JULIAN",
    "TITLE_PLACEHOLDER_KOVETZ",
    "ROLE_TRANSLATOR",
    "ROLE_COMMENTATOR",
    "PROVENANCE_561_LONG",
    "PROVENANCE_561_MULTI",
    "GENRE_655_MULTIPLE",
    "SUBJECT_BIBLE_630",
    "SUBJECT_TALMUD_630",
    "CU_MULTIPLE_500",
    "LANG_GREEK",
    "LANG_LADINO",
    "FIELD_880_PRESENT",
    "DATE_HEBREW_CENTURY",
)


# ──────────────────────────────────────────────────────────────────────────────
# Complexity buckets
# ──────────────────────────────────────────────────────────────────────────────


_COMPLEXITY_BUCKETS: list[tuple[str, PredicateFn]] = [
    # Simple unified: one work, one MARC 245, no MARC 505 contents, no 561.
    (
        "simple_unified",
        lambda r: not _has(r, "505$a")
        and not _has(r, "561$a")
        and len(_parts(r.get("500$a", ""))) <= 1,
    ),
    # Structural assembly: MARC 505 contents OR multiple 500 entries.
    (
        "structural_assembly",
        lambda r: _has(r, "505$a") or len(_parts(r.get("500$a", ""))) >= 3,
    ),
    # Provenance-rich: long MARC 561 OR multi-step provenance.
    ("provenance_rich", lambda r: _has_provenance_long(r) or _has_provenance_multi(r)),
    # Subject-dense: multiple genre + ≥1 subject heading family.
    (
        "subject_dense",
        lambda r: _has_genre_multiple(r) and (_has(r, "650$a") or _has(r, "630$a") or _has(r, "651$a")),
    ),
    # Multilingual: 041 lists ≥2 codes OR 880 field present.
    ("multilingual", lambda r: _has_multilang(r) or _has_880_field(r)),
    # Edge-case dates: Hebrew gematria year/century OR pre-1582 Julian.
    (
        "edge_dates",
        lambda r: _has_hebrew_century(r) or _has_hebrew_year(r) or _has_pre_1582_date(r),
    ),
]


# ──────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ──────────────────────────────────────────────────────────────────────────────


class RecordScore(NamedTuple):
    record_id: str
    signals: frozenset[str]
    total: int


def _score_records(records: list[Record]) -> list[RecordScore]:
    scores: list[RecordScore] = []
    for rec in records:
        rid = _record_id(rec)
        present: set[str] = set()
        for sid, pred in _SIGNALS:
            try:
                if pred(rec):
                    present.add(sid)
            except Exception:
                continue
        scores.append(RecordScore(rid, frozenset(present), len(present)))
    return scores


def _stable_hash(rid: str, seed: int) -> int:
    """Deterministic record ordering tiebreaker."""
    h = hashlib.sha256(f"{seed}:{rid}".encode("utf-8")).hexdigest()
    return int(h[:16], 16)


# ──────────────────────────────────────────────────────────────────────────────
# Selection stages
# ──────────────────────────────────────────────────────────────────────────────


def _stage1_force_include(
    scores: list[RecordScore],
    hard_signals: Iterable[str],
    seed: int,
) -> list[str]:
    chosen: list[str] = []
    seen: set[str] = set()
    for sid in hard_signals:
        candidates = [s for s in scores if sid in s.signals]
        if not candidates:
            continue
        candidates.sort(
            key=lambda s: (-s.total, _stable_hash(s.record_id, seed))
        )
        best = candidates[0]
        if best.record_id not in seen:
            chosen.append(best.record_id)
            seen.add(best.record_id)
    return chosen


def _stage2_greedy_cover(
    scores: list[RecordScore],
    universe: set[str],
    already_chosen: set[str],
    seed: int,
) -> list[str]:
    by_id: dict[str, RecordScore] = {s.record_id: s for s in scores}
    covered: set[str] = set()
    for rid in already_chosen:
        if rid in by_id:
            covered |= by_id[rid].signals & universe

    chosen: list[str] = []
    while True:
        uncovered = universe - covered
        if not uncovered:
            break
        best: RecordScore | None = None
        best_gain = 0
        for s in scores:
            if s.record_id in already_chosen or s.record_id in chosen:
                continue
            gain = len(s.signals & uncovered)
            if gain == 0:
                continue
            if (
                best is None
                or gain > best_gain
                or (
                    gain == best_gain
                    and (
                        s.total > best.total
                        or (
                            s.total == best.total
                            and _stable_hash(s.record_id, seed)
                            < _stable_hash(best.record_id, seed)
                        )
                    )
                )
            ):
                best = s
                best_gain = gain
        if best is None:
            break
        chosen.append(best.record_id)
        covered |= best.signals & universe
    return chosen


def _stage4_supplement_zero_hits(
    supplement_path: Path,
    primary_fieldnames: list[str],
    zero_hit: set[str],
    already_chosen: set[str],
    seed: int,
    verbose: bool,
) -> tuple[list[str], dict[str, "Record"], list[str], dict[str, list[str]]]:
    """Scan a secondary corpus for records exemplifying zero-hit signals.

    Returns:
        chosen_ids: ordered list of record IDs to add from supplement.
        records_by_id: only the chosen records, keyed by their ID.
        resolved: zero-hit signal IDs now exemplified by the chosen records.
        supplement_coverage: for each chosen record ID, the list of zero-hit
            signals it newly exemplifies (for the manifest).
    """
    if not zero_hit:
        return [], {}, [], {}
    if not supplement_path.exists():
        if verbose:
            print(
                f"  supplement source not found: {supplement_path}",
                file=sys.stderr,
            )
        return [], {}, [], {}

    if verbose:
        print(f"  reading supplement {supplement_path} ...", flush=True)
    sup_fieldnames, sup_records = _read_tsv(supplement_path)

    # Column compatibility: primary's columns must be a subset of supplement's.
    missing_cols = set(primary_fieldnames) - set(sup_fieldnames)
    if missing_cols:
        if verbose:
            print(
                f"  WARN: supplement missing {len(missing_cols)} primary "
                f"columns; skipping supplement",
                file=sys.stderr,
            )
        return [], {}, [], {}

    if verbose:
        print(
            f"  supplement: {len(sup_records)} records, scoring …",
            flush=True,
        )
    sup_scores = _score_records(sup_records)
    by_id: dict[str, RecordScore] = {s.record_id: s for s in sup_scores}
    sup_records_by_id: dict[str, Record] = {
        _record_id(r): r for r in sup_records
    }

    # Index: signal → list of (RecordScore) that exemplify it.
    signal_hits: dict[str, list[RecordScore]] = {}
    for sid in zero_hit:
        hits = [s for s in sup_scores if sid in s.signals]
        if hits:
            signal_hits[sid] = hits

    # Process rarest zero-hit signals first so we don't waste a slot on a
    # common signal that a later record would have covered anyway.
    chosen: list[str] = []
    chosen_set: set[str] = set(already_chosen)
    resolved: list[str] = []
    supplement_coverage: dict[str, list[str]] = {}

    for sid in sorted(signal_hits.keys(), key=lambda s: len(signal_hits[s])):
        # Did we already pick a record that covers this signal?
        already = next(
            (cid for cid in chosen if sid in by_id[cid].signals),
            None,
        )
        if already is not None:
            resolved.append(sid)
            supplement_coverage[already].append(sid)
            continue

        # Pick the highest-coverage candidate — most signals total, then
        # deterministic hash for stable ties.
        candidates = signal_hits[sid]
        candidates.sort(
            key=lambda s: (-s.total, _stable_hash(s.record_id, seed))
        )
        best = candidates[0]
        if best.record_id in chosen_set:
            resolved.append(sid)
            supplement_coverage.setdefault(best.record_id, []).append(sid)
            continue
        chosen.append(best.record_id)
        chosen_set.add(best.record_id)
        resolved.append(sid)
        supplement_coverage[best.record_id] = [sid]

    # Trim records_by_id to only the chosen IDs (caller doesn't need the rest).
    chosen_records = {
        rid: sup_records_by_id[rid] for rid in chosen if rid in sup_records_by_id
    }
    return chosen, chosen_records, resolved, supplement_coverage


def _stage3_stratified_typical(
    records_by_id: dict[str, Record],
    excluded: set[str],
    n_needed: int,
    seed: int,
) -> list[str]:
    if n_needed <= 0:
        return []

    pool = [
        rid for rid in records_by_id if rid not in excluded
    ]

    rng = random.Random(seed)

    with_561 = [rid for rid in pool if _has_provenance_561(records_by_id[rid])]
    without_561 = [rid for rid in pool if not _has_provenance_561(records_by_id[rid])]
    rng.shuffle(with_561)
    rng.shuffle(without_561)

    half = n_needed // 2
    take_a = min(half, len(with_561))
    take_b = min(n_needed - take_a, len(without_561))
    if take_a + take_b < n_needed:
        # Top up from whichever pool still has records.
        leftover = [
            rid
            for rid in (with_561[take_a:] + without_561[take_b:])
        ]
        rng.shuffle(leftover)
        extra = leftover[: n_needed - take_a - take_b]
    else:
        extra = []

    fill = with_561[:take_a] + without_561[:take_b] + extra

    # Within fill, bias towards proportional decade distribution
    # by re-sampling: bucket by decade, then round-robin.
    by_decade: dict[str, list[str]] = defaultdict(list)
    for rid in fill:
        y = _extract_008_year(records_by_id[rid])
        decade = f"{(y // 10) * 10}s" if y else "unknown"
        by_decade[decade].append(rid)
    ordered: list[str] = []
    keys = sorted(by_decade.keys())
    while any(by_decade[k] for k in keys):
        for k in keys:
            if by_decade[k]:
                ordered.append(by_decade[k].pop(0))
                if len(ordered) >= n_needed:
                    return ordered[:n_needed]
    return ordered[:n_needed]


def _enforce_cap(
    stage1: list[str],
    stage2: list[str],
    stage3: list[str],
    cap: int,
    scores: list[RecordScore],
    universe: set[str],
) -> tuple[list[str], list[str], list[str]]:
    by_id = {s.record_id: s for s in scores}
    total = len(stage1) + len(stage2) + len(stage3)
    if total <= cap:
        return stage1, stage2, stage3

    drop = total - cap
    stage3 = stage3.copy()
    while drop > 0 and stage3:
        stage3.pop()
        drop -= 1
    if drop <= 0:
        return stage1, stage2, stage3

    # Drop redundant stage-2 picks (covered by remaining set).
    keep_ids = list(stage1) + list(stage2) + list(stage3)
    while drop > 0 and stage2:
        # Find a stage-2 pick whose signals are covered by the others.
        redundant: str | None = None
        for rid in reversed(stage2):
            others = [r for r in keep_ids if r != rid]
            covered: set[str] = set()
            for r in others:
                if r in by_id:
                    covered |= by_id[r].signals & universe
            if by_id[rid].signals & universe <= covered:
                redundant = rid
                break
        if redundant is None:
            # Fall back: drop last stage-2 pick.
            redundant = stage2[-1]
        stage2 = [r for r in stage2 if r != redundant]
        keep_ids = list(stage1) + list(stage2) + list(stage3)
        drop -= 1
    return stage1, stage2, stage3


# ──────────────────────────────────────────────────────────────────────────────
# I/O
# ──────────────────────────────────────────────────────────────────────────────


def _read_tsv(path: Path) -> tuple[list[str], list[Record]]:
    # Use utf-8-sig so a leading UTF-8 BOM on the first column header is
    # transparently stripped (the supplement corpus has one; the primary
    # doesn't — without sig the column names would mismatch).
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows: list[Record] = []
        for row in reader:
            rows.append({k: (row.get(k) or "") for k in fieldnames})
    return fieldnames, rows


def _write_tsv(
    path: Path,
    fieldnames: list[str],
    records: list[Record],
    source_path: Path,
) -> None:
    """Write subset preserving header line verbatim from source."""
    with source_path.open("r", encoding="utf-8", newline="") as f:
        header_line = f.readline()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(header_line if header_line.endswith("\n") else header_line + "\n")
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            delimiter="\t",
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        for rec in records:
            writer.writerow(rec)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a coverage-maximizing test subset.")
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument(
        "--supplement-source",
        type=str,
        default=str(DEFAULT_SUPPLEMENT),
        help=(
            "Secondary corpus for stage 4 — pulls one real record per zero-hit "
            "signal. Pass an empty string to disable."
        ),
    )
    p.add_argument("--cap", type=int, default=80)
    p.add_argument("--target-baseline", type=int, default=60)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def _vprint(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if not args.source.exists():
        print(f"ERROR: source not found: {args.source}", file=sys.stderr)
        return 2

    _vprint(args.verbose, f"Reading {args.source} ...")
    fieldnames, records = _read_tsv(args.source)
    if not records:
        print("ERROR: source TSV has no rows", file=sys.stderr)
        return 2
    _vprint(args.verbose, f"  {len(records)} records, {len(fieldnames)} columns")

    records_by_id = {_record_id(r): r for r in records}

    _vprint(args.verbose, "Scoring records against 65 coverage signals ...")
    scores = _score_records(records)
    by_id = {s.record_id: s for s in scores}

    # Build universe = signals with at least one corpus hit.
    hit_count: Counter[str] = Counter()
    for s in scores:
        for sid in s.signals:
            hit_count[sid] += 1
    universe = {sid for sid, _ in _SIGNALS if hit_count[sid] >= 1}
    zero_hit = {sid for sid, _ in _SIGNALS} - universe
    _vprint(
        args.verbose,
        f"  {len(universe)} signals exemplified in corpus, "
        f"{len(zero_hit)} zero-hit",
    )
    if zero_hit:
        _vprint(args.verbose, f"  zero-hit: {sorted(zero_hit)}")

    _vprint(args.verbose, "Stage 1 — hard-signal force-include ...")
    stage1 = _stage1_force_include(scores, _HARD_SIGNALS, args.seed)
    _vprint(args.verbose, f"  picked {len(stage1)}")

    _vprint(args.verbose, "Stage 2 — greedy set-cover ...")
    stage2 = _stage2_greedy_cover(
        scores, universe, set(stage1), args.seed
    )
    _vprint(args.verbose, f"  picked {len(stage2)}")

    fill_n = max(0, args.target_baseline - len(stage1) - len(stage2))
    _vprint(args.verbose, f"Stage 3 — stratified-typical fill (n={fill_n}) ...")
    stage3 = _stage3_stratified_typical(
        records_by_id, set(stage1) | set(stage2), fill_n, args.seed
    )
    _vprint(args.verbose, f"  picked {len(stage3)}")

    stage1, stage2, stage3 = _enforce_cap(
        stage1, stage2, stage3, args.cap, scores, universe
    )

    # Stage 4 — supplement zero-hit signals from a secondary corpus.
    # Stage 4 records are NOT droppable: they're the only way to exemplify
    # signals the primary corpus lacks.
    _vprint(
        args.verbose,
        "Stage 4 — supplement zero-hit signals from secondary corpus ...",
    )
    stage4_ids: list[str] = []
    stage4_records: dict[str, Record] = {}
    stage4_resolved: list[str] = []
    stage4_per_record_signals: dict[str, list[str]] = {}
    supplement_sha = ""
    supplement_path_str = ""
    if args.supplement_source and zero_hit:
        sup_path = Path(args.supplement_source)
        already = set(stage1) | set(stage2) | set(stage3)
        stage4_ids, stage4_records, stage4_resolved, stage4_per_record_signals = (
            _stage4_supplement_zero_hits(
                sup_path, fieldnames, zero_hit, already, args.seed, args.verbose
            )
        )
        if stage4_ids and sup_path.exists():
            supplement_sha = _sha256(sup_path)
            supplement_path_str = (
                str(sup_path.relative_to(_REPO_ROOT))
                if sup_path.is_absolute() and _REPO_ROOT in sup_path.parents
                else str(sup_path)
            )
        # Merge supplement records into the primary lookup tables so
        # downstream coverage / bucket / stratification logic sees them.
        for rid in stage4_ids:
            if rid in stage4_records:
                records_by_id[rid] = stage4_records[rid]
                new_score = _score_records([stage4_records[rid]])[0]
                by_id[rid] = new_score
        # Update zero_hit so the manifest reflects what's actually missing
        # after supplementation.
        zero_hit = zero_hit - set(stage4_resolved)
        # Update universe so coverage_gaps detection includes the resolved
        # signals (now exemplified by supplement records).
        universe |= set(stage4_resolved)
    _vprint(
        args.verbose,
        f"  picked {len(stage4_ids)} records, resolved "
        f"{len(stage4_resolved)} previously zero-hit signals",
    )

    final_ids = stage1 + stage2 + stage3 + stage4_ids
    _vprint(
        args.verbose,
        f"Final selection: {len(final_ids)} records "
        f"(stage1={len(stage1)} stage2={len(stage2)} "
        f"stage3={len(stage3)} stage4={len(stage4_ids)})",
    )

    final_records = [records_by_id[rid] for rid in final_ids if rid in records_by_id]

    # Build manifest BEFORE writing so we can include subset_sha256.
    # Only include signals with ≥1 exemplar in the subset; empty ones land in
    # coverage_gaps (corpus had it but subset missed) or zero_hit_in_corpus.
    coverage: dict[str, list[str]] = {}
    for sid, _ in _SIGNALS:
        present_ids = [rid for rid in final_ids if rid in by_id and sid in by_id[rid].signals]
        if present_ids:
            coverage[sid] = present_ids
    coverage_gaps = sorted(
        sid for sid in universe if sid not in coverage
    )

    bucket_assignments: dict[str, list[str]] = {name: [] for name, _ in _COMPLEXITY_BUCKETS}
    for rid in final_ids:
        rec = records_by_id[rid]
        for name, pred in _COMPLEXITY_BUCKETS:
            try:
                if pred(rec):
                    bucket_assignments[name].append(rid)
            except Exception:
                continue

    decade_dist: Counter[str] = Counter()
    with_561_n = 0
    with_700_n = 0
    for rid in final_ids:
        rec = records_by_id[rid]
        if _has_provenance_561(rec):
            with_561_n += 1
        if _has_added_entry_person(rec):
            with_700_n += 1
        y = _extract_008_year(rec)
        decade_dist[f"{(y // 10) * 10}s" if y else "unknown"] += 1

    _vprint(args.verbose, "Writing outputs ...")
    if args.dry_run:
        _vprint(args.verbose, "  dry-run: skipping file writes")
        subset_sha = "(dry-run)"
        source_sha = _sha256(args.source)
    else:
        _write_tsv(args.out, fieldnames, final_records, args.source)
        subset_sha = _sha256(args.out)
        source_sha = _sha256(args.source)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_corpus_sha256": source_sha,
        "source_corpus_path": str(args.source.relative_to(_REPO_ROOT))
        if args.source.is_absolute() and _REPO_ROOT in args.source.parents
        else str(args.source),
        "source_n_records": len(records),
        "subset_sha256": subset_sha,
        "subset_path": str(args.out.relative_to(_REPO_ROOT))
        if args.out.is_absolute() and _REPO_ROOT in args.out.parents
        else str(args.out),
        "n_records": len(final_ids),
        "selection": {
            "stage1_force_included": len(stage1),
            "stage2_greedy_cover": len(stage2),
            "stage3_stratified_typical": len(stage3),
            "stage4_supplemented_from_secondary": len(stage4_ids),
        },
        "supplement": {
            "source_path": supplement_path_str,
            "source_sha256": supplement_sha,
            "records": stage4_ids,
            "resolved_signals": sorted(stage4_resolved),
            "per_record_signals": stage4_per_record_signals,
        } if stage4_ids else {},
        "coverage": coverage,
        "complexity_buckets": bucket_assignments,
        "stratification": {
            "with_provenance_561": with_561_n,
            "without_provenance_561": len(final_ids) - with_561_n,
            "with_contributor_700": with_700_n,
            "decade_distribution": dict(sorted(decade_dist.items())),
        },
        "coverage_gaps": coverage_gaps,
        "zero_hit_signals_in_corpus": sorted(zero_hit),
        "cap": args.cap,
        "target_baseline": args.target_baseline,
        "seed": args.seed,
        "signal_predicate_versions": "v1",
    }

    if not args.dry_run:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False, sort_keys=False)

    print(f"Wrote subset: {args.out} ({len(final_ids)} records)")
    print(f"  sha256: {subset_sha}")
    print(f"  manifest: {args.manifest}")
    print(
        f"  selection: stage1={len(stage1)} stage2={len(stage2)} "
        f"stage3={len(stage3)} stage4={len(stage4_ids)}"
    )
    if stage4_ids:
        print(
            f"  supplement: {len(stage4_ids)} records resolved "
            f"{len(stage4_resolved)} signals from {supplement_path_str}"
        )
    print(
        f"  stratification: 561={with_561_n}/{len(final_ids)-with_561_n} "
        f"700={with_700_n}"
    )
    print(f"  decades: {dict(sorted(decade_dist.items()))}")
    print(f"  coverage_gaps ({len(coverage_gaps)}): {coverage_gaps}")
    print(f"  zero_hit_in_corpus ({len(zero_hit)}): {sorted(zero_hit)}")
    for name, ids in bucket_assignments.items():
        print(f"  bucket[{name}]: {len(ids)}")

    if coverage_gaps:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
