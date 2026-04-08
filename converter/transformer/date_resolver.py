"""Resolve Hebrew, Gregorian, and mixed-format date strings to year ranges.

This module converts the wide variety of date notations found in Hebrew
manuscript MARC records into normalised ``DateRange`` tuples.  Every
public function is a pure function with no side effects, making the
module trivially testable and safe to call from any thread.

Supported formats (ordered by specificity):
  - Seleucid era:           "א'תתקס\"א לשטרות (1650)"
  - Hebrew year with Gregorian in parens: "ת\"ן (1690)."
  - Hebrew approximate:     "1570 בערך-1643", "נפטר 1628 בערך"
  - Hebrew active period:   "פעל במאה הי\"ז", "פעיל 1407"
  - Hebrew century:         "המאה ה-17", "המאה הי\"א"
  - English approximate:    "approximately 1570-approximately 1639"
  - English century:        "17th cent.", "16th-17th cent."
  - Gregorian uncertain:    "1570?-1667", "?1270-?1340"
  - Gregorian open-ended:   "1661-"
  - Gregorian range:        "1542-1620"
  - Gregorian single:       "1650"
  - Standalone Hebrew year:  "ת\"ג", "שס\"ד"
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

# ── Data structures ──────────────────────────────────────────────────────────


class DateRange(NamedTuple):
    """A resolved year range with metadata."""

    year_start: int | None = None
    year_end: int | None = None
    approximate: bool = False
    source_format: str = "unknown"


_EMPTY = DateRange(None, None, False, "unresolved")

# ── Gematria engine ──────────────────────────────────────────────────────────

GEMATRIA: dict[str, int] = {
    "א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9,
    "י": 10, "כ": 20, "ך": 20, "ל": 30, "מ": 40, "ם": 40, "נ": 50, "ן": 50,
    "ס": 60, "ע": 70, "פ": 80, "ף": 80, "צ": 90, "ץ": 90,
    "ק": 100, "ר": 200, "ש": 300, "ת": 400,
}

# Characters to strip from Hebrew date text before gematria calculation
_STRIP_CHARS = set("\"'״׳\u05F3\u05F4\u05BC\u05BD\u0027\u0022 ")

# Hebrew letter range for regex (alef–tav including final forms)
_HEB = r"\u05D0-\u05EA"


def _strip_marks(text: str) -> str:
    """Remove geresh, gershayim, dagesh, and combining marks from text."""
    # First strip known punctuation
    cleaned = "".join(c for c in text if c not in _STRIP_CHARS)
    # Then remove Unicode combining characters (niqqud etc.)
    return "".join(
        c for c in unicodedata.normalize("NFD", cleaned)
        if unicodedata.category(c) != "Mn"
    )


def hebrew_letters_to_gematria(text: str) -> int:
    """Sum the gematria values of all Hebrew letters in *text*.

    Non-Hebrew characters (punctuation, spaces, geresh/gershayim) are ignored.

    >>> hebrew_letters_to_gematria('תנ"ד')
    454
    >>> hebrew_letters_to_gematria("שסד")
    364
    """
    cleaned = _strip_marks(text)
    return sum(GEMATRIA.get(c, 0) for c in cleaned)


def hebrew_year_to_gregorian(gematria_value: int, era: str = "minor") -> int:
    """Convert a Hebrew year's gematria value to a Gregorian year.

    Args:
        gematria_value: Numeric value from gematria (e.g. 454 for תנ"ד).
        era: ``"minor"`` for standard Hebrew (adds 5000 then subtracts 3760),
             ``"seleucid"`` for Seleucid/Sheṭarot (subtracts 312).

    Returns:
        The approximate Gregorian year.  May be off by ±1 due to the
        Tishrei/January boundary.

    >>> hebrew_year_to_gregorian(454)
    1694
    >>> hebrew_year_to_gregorian(1964, era="seleucid")
    1652
    """
    if era == "seleucid":
        return gematria_value - 312
    # Minor era: value represents hundreds/tens/units of the Hebrew year
    # (the thousands digit — 5 — is omitted by convention).
    return gematria_value + 1240  # equivalent to +5000 -3760


def _century_to_range(century: int) -> tuple[int, int]:
    """Map a century number to its Gregorian year range.

    >>> _century_to_range(17)
    (1601, 1700)
    """
    return ((century - 1) * 100 + 1, century * 100)


# ── Hebrew ordinal gematria (for century numbers) ───────────────────────────

def _hebrew_ordinal_to_int(text: str) -> int | None:
    """Convert a Hebrew ordinal like יז (17) or יא (11) to an integer.

    Returns None if the text contains no Hebrew letters.
    """
    val = hebrew_letters_to_gematria(text)
    return val if val > 0 else None


# ── Individual format parsers ────────────────────────────────────────────────
# Each returns Optional[DateRange].  They are tried in specificity order
# by the resolve() dispatcher.

# Pre-compiled patterns (module-level for performance)
_RE_SELEUCID = re.compile(
    rf"([{_HEB}\"״\u05F4'\u05F3]+)\s*לשטרות"
)
_RE_HEBREW_YEAR_WITH_GREG = re.compile(
    rf"([{_HEB}\"״\u05F4'\u05F3]{{2,8}})\s*\(?(\d{{3,4}})\)?"
)
_RE_HEBREW_APPROX_RANGE = re.compile(
    r"(\d{3,4})\s*בערך\s*[-–]\s*(\d{3,4})"
)
_RE_HEBREW_APPROX_RANGE2 = re.compile(
    r"בערך\s*(\d{3,4})\s*[-–]\s*(\d{3,4})"
)
_RE_HEBREW_DIED = re.compile(r"נפטר\s+(\d{3,4})")
_RE_HEBREW_BORN = re.compile(r"נולד\s+(\d{3,4})")
_RE_HEBREW_APPROX_SINGLE = re.compile(r"(\d{3,4})\s*בערך")
_RE_HEBREW_ACTIVE_CENTURY = re.compile(
    rf"(?:פעל|פעיל)\s+(?:ב)?מאה\s+ה[-]?([{_HEB}\"״\u05F4'\u05F3]+|\d{{1,2}})"
)
_RE_HEBREW_ACTIVE_YEAR = re.compile(r"(?:פעל|פעיל)\s+(\d{3,4})")
_RE_HEBREW_CENTURY_NUM = re.compile(r"מאה\s+ה[-]?(\d{1,2})")
_RE_HEBREW_CENTURY_LETTERS = re.compile(
    rf"מאה\s+ה[-]?([{_HEB}\"״\u05F4'\u05F3]{{1,5}})"
)
_RE_ENG_APPROX = re.compile(
    r"(?:approximately|approx\.?|ca\.?)\s*(\d{3,4})\s*[-–]\s*"
    r"(?:approximately|approx\.?|ca\.?)\s*(\d{3,4})",
    re.IGNORECASE,
)
_RE_ENG_CA = re.compile(
    r"(?:ca\.?|c\.)\s*(\d{3,4})", re.IGNORECASE,
)
_RE_ENG_CENTURY_RANGE = re.compile(
    r"(\d{1,2})(?:th|st|nd|rd)\s*[-–]\s*(\d{1,2})(?:th|st|nd|rd)\s*cent",
    re.IGNORECASE,
)
_RE_ENG_CENTURY = re.compile(
    r"(\d{1,2})(?:th|st|nd|rd)\s*cent", re.IGNORECASE,
)
_RE_GREG_UNCERTAIN = re.compile(
    r"\??\s*(\d{3,4})\s*\??\s*[-–]\s*\??\s*(\d{3,4})\s*\??"
)
_RE_GREG_OPEN = re.compile(r"(\d{3,4})\s*[-–]\s*$")
_RE_GREG_RANGE = re.compile(r"(\d{3,4})\s*[-–]\s*(\d{3,4})")
_RE_GREG_SINGLE = re.compile(r"\b(\d{4})\b")
_RE_HEBREW_STANDALONE = re.compile(
    rf"([{_HEB}]{{1,5}})"
)


def _parse_seleucid_era(s: str) -> DateRange | None:
    """Parse Seleucid-era dates marked with לשטרות."""
    if "לשטרות" not in s:
        return None
    # If there's a Gregorian year in parentheses, trust it
    greg_match = re.search(r"\((\d{3,4})\)", s)
    if greg_match:
        year = int(greg_match.group(1))
        return DateRange(year, year, False, "seleucid_era")
    # Otherwise compute from gematria
    m = _RE_SELEUCID.search(s)
    if not m:
        return None
    heb_text = m.group(1)
    gval = hebrew_letters_to_gematria(heb_text)
    # Seleucid dates often have a leading א (=1000)
    if "א" in _strip_marks(heb_text)[:1] or gval > 900:
        gval += 1000
    year = hebrew_year_to_gregorian(gval, era="seleucid")
    if 100 < year < 2100:
        return DateRange(year, year, True, "seleucid_era")
    return None


def _parse_publication_hebrew_year(s: str) -> DateRange | None:
    """Parse Hebrew year with Gregorian in parentheses: ת"ן (1690)."""
    greg_match = re.search(r"\((\d{3,4})\)", s)
    if not greg_match:
        return None
    # Must also have Hebrew letters nearby (at least 1 letter — handles ת"ן)
    heb_letters = re.search(rf"[{_HEB}]", s)
    if not heb_letters:
        return None
    year = int(greg_match.group(1))
    return DateRange(year, year, False, "publication_hebrew_year")


def _parse_hebrew_approximate(s: str) -> DateRange | None:
    """Parse dates with בערך (approximately), נפטר (died), נולד (born)."""
    # Range with בערך: "1570 בערך-1643" or "בערך 1570-1643"
    m = _RE_HEBREW_APPROX_RANGE.search(s) or _RE_HEBREW_APPROX_RANGE2.search(s)
    if m:
        return DateRange(int(m.group(1)), int(m.group(2)), True, "hebrew_approximate")

    # "נפטר 1628 בערך" (died ~1628)
    m = _RE_HEBREW_DIED.search(s)
    if m and "בערך" in s:
        year = int(m.group(1))
        return DateRange(None, year, True, "hebrew_approximate")

    # "נולד 1540 בערך" (born ~1540)
    m = _RE_HEBREW_BORN.search(s)
    if m and "בערך" in s:
        year = int(m.group(1))
        return DateRange(year, None, True, "hebrew_approximate")

    # נפטר/נולד without בערך
    m = _RE_HEBREW_DIED.search(s)
    if m:
        year = int(m.group(1))
        return DateRange(None, year, False, "hebrew_approximate")
    m = _RE_HEBREW_BORN.search(s)
    if m:
        year = int(m.group(1))
        return DateRange(year, None, False, "hebrew_approximate")

    # Single year with בערך: "1570 בערך"
    if "בערך" in s:
        m = _RE_HEBREW_APPROX_SINGLE.search(s)
        if m:
            year = int(m.group(1))
            return DateRange(year, year, True, "hebrew_approximate")

    return None


def _parse_hebrew_active(s: str) -> DateRange | None:
    """Parse Hebrew activity periods: פעל במאה הי\"ז, פעיל 1407."""
    if "פעל" not in s and "פעיל" not in s:
        return None

    # Century range in active context: "פעל במאה ה-16-17"
    range_match = re.search(
        rf"(?:פעל|פעיל)\s+(?:ב)?מאה\s+ה[-]?(\d{{1,2}})\s*[-–]\s*(\d{{1,2}})", s,
    )
    if range_match:
        c1, c2 = int(range_match.group(1)), int(range_match.group(2))
        s1, _ = _century_to_range(c1)
        _, e2 = _century_to_range(c2)
        return DateRange(s1, e2, True, "hebrew_active")

    # "פעל במאה הי\"ז" → century from ordinal
    m = _RE_HEBREW_ACTIVE_CENTURY.search(s)
    if m:
        raw = m.group(1)
        if raw.isdigit():
            century = int(raw)
        else:
            century = _hebrew_ordinal_to_int(raw)
        if century and 1 <= century <= 25:
            start, end = _century_to_range(century)
            return DateRange(start, end, True, "hebrew_active")
    # "פעיל 1407" → specific year
    m = _RE_HEBREW_ACTIVE_YEAR.search(s)
    if m:
        year = int(m.group(1))
        return DateRange(year, year, True, "hebrew_active")
    return None


def _parse_hebrew_century(s: str) -> DateRange | None:
    """Parse Hebrew century references: המאה ה-17, המאה הי\"א."""
    if "מאה" not in s:
        return None

    # Two-century range: "המאה ה-16-17" or "המאה ה-16-המאה ה-17"
    range_match = re.search(r"מאה\s+ה[-]?(\d{1,2})\s*[-–]\s*(\d{1,2})", s)
    if range_match:
        c1, c2 = int(range_match.group(1)), int(range_match.group(2))
        s1, _ = _century_to_range(c1)
        _, e2 = _century_to_range(c2)
        return DateRange(s1, e2, True, "hebrew_century")

    # Arabic numeral: "המאה ה-17"
    m = _RE_HEBREW_CENTURY_NUM.search(s)
    if m:
        century = int(m.group(1))
        start, end = _century_to_range(century)
        return DateRange(start, end, True, "hebrew_century")

    # Hebrew ordinal: "המאה הי\"ז"
    m = _RE_HEBREW_CENTURY_LETTERS.search(s)
    if m:
        century = _hebrew_ordinal_to_int(m.group(1))
        if century and 1 <= century <= 25:
            start, end = _century_to_range(century)
            return DateRange(start, end, True, "hebrew_century")

    return None


def _parse_english_approximate(s: str) -> DateRange | None:
    """Parse English approximate dates: approximately, ca., c."""
    m = _RE_ENG_APPROX.search(s)
    if m:
        return DateRange(int(m.group(1)), int(m.group(2)), True, "english_approximate")
    m = _RE_ENG_CA.search(s)
    if m:
        year = int(m.group(1))
        return DateRange(year, year, True, "english_approximate")
    return None


def _parse_english_century(s: str) -> DateRange | None:
    """Parse English century references: 17th cent., 16th-17th cent."""
    m = _RE_ENG_CENTURY_RANGE.search(s)
    if m:
        c1, c2 = int(m.group(1)), int(m.group(2))
        s1, _ = _century_to_range(c1)
        _, e2 = _century_to_range(c2)
        return DateRange(s1, e2, True, "english_century")
    m = _RE_ENG_CENTURY.search(s)
    if m:
        century = int(m.group(1))
        start, end = _century_to_range(century)
        return DateRange(start, end, True, "english_century")
    return None


def _parse_gregorian_uncertain_range(s: str) -> DateRange | None:
    """Parse Gregorian ranges with question marks: 1570?-1667, ?1270-?1340."""
    if "?" not in s:
        return None
    m = _RE_GREG_UNCERTAIN.search(s)
    if m:
        return DateRange(int(m.group(1)), int(m.group(2)), True, "gregorian_uncertain_range")
    return None


def _parse_gregorian_open(s: str) -> DateRange | None:
    """Parse open-ended Gregorian dates: 1661-."""
    m = _RE_GREG_OPEN.search(s.rstrip(". "))
    if m:
        return DateRange(int(m.group(1)), None, False, "gregorian_open")
    return None


def _parse_gregorian_range(s: str) -> DateRange | None:
    """Parse standard Gregorian ranges: 1542-1620."""
    if "?" in s:
        return None  # Let uncertain parser handle these
    m = _RE_GREG_RANGE.search(s)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        if 100 < y1 < 2100 and 100 < y2 < 2100:
            return DateRange(y1, y2, False, "gregorian_range")
    return None


def _parse_gregorian_single(s: str) -> DateRange | None:
    """Parse a standalone 4-digit Gregorian year: 1650."""
    m = _RE_GREG_SINGLE.search(s)
    if m:
        year = int(m.group(1))
        if 100 < year < 2100:
            approx = "?" in s or "[" in s
            return DateRange(year, year, approx, "gregorian_single")
    return None


def _parse_standalone_hebrew_year(s: str) -> DateRange | None:
    """Parse standalone Hebrew year letters: ת\"ג, שס\"ד, תש\"פ.

    Only used as a last-resort fallback when no other parser matched.
    """
    # Strip everything except Hebrew letters
    cleaned = _strip_marks(s)
    # Must have at least 2 Hebrew letters
    heb_only = "".join(c for c in cleaned if c in GEMATRIA)
    if len(heb_only) < 2:
        return None
    gval = hebrew_letters_to_gematria(heb_only)
    if gval < 50:
        return None  # Too small to be a year
    year = hebrew_year_to_gregorian(gval)
    if 100 < year < 2100:
        return DateRange(year, year, True, "standalone_hebrew_year")
    return None


# ── Top-level dispatcher ────────────────────────────────────────────────────

# Ordered from most specific to least specific
_PARSERS = [
    _parse_seleucid_era,
    _parse_publication_hebrew_year,
    _parse_hebrew_approximate,
    _parse_hebrew_active,
    _parse_hebrew_century,
    _parse_english_approximate,
    _parse_english_century,
    _parse_gregorian_uncertain_range,
    _parse_gregorian_open,
    _parse_gregorian_range,
    _parse_gregorian_single,
    _parse_standalone_hebrew_year,
]


def resolve(date_string: str) -> DateRange:
    """Resolve any date string to a ``DateRange``.

    Tries parsers in specificity order (most distinctive first).
    Returns a ``DateRange`` with ``None`` fields if nothing matched.

    >>> resolve("1542-1620")
    DateRange(year_start=1542, year_end=1620, approximate=False, source_format='gregorian_range')
    >>> resolve('ת"ן (1690).')
    DateRange(year_start=1690, year_end=1690, approximate=False, source_format='publication_hebrew_year')
    >>> resolve("המאה ה-17")
    DateRange(year_start=1601, year_end=1700, approximate=True, source_format='hebrew_century')
    """
    if not date_string or not date_string.strip():
        return _EMPTY
    s = date_string.strip().rstrip(".")
    for parser in _PARSERS:
        result = parser(s)
        if result is not None:
            return result
    return _EMPTY


# ── Convenience functions ────────────────────────────────────────────────────


def resolve_person_dates(date_string: str) -> dict[str, int | None]:
    """Resolve a person's date string to birth/death/active years.

    Returns a dict with keys ``birth_year``, ``death_year``, ``active_year``
    (any may be ``None``).  Handles all formats supported by ``resolve()``.

    >>> resolve_person_dates("1542-1620")
    {'birth_year': 1542, 'death_year': 1620, 'active_year': None}
    >>> resolve_person_dates("נפטר 1628 בערך")
    {'birth_year': None, 'death_year': 1628, 'active_year': None}
    >>> resolve_person_dates("המאה ה-17")
    {'birth_year': None, 'death_year': None, 'active_year': 1650}
    """
    result: dict[str, int | None] = {
        "birth_year": None,
        "death_year": None,
        "active_year": None,
    }
    if not date_string:
        return result

    dr = resolve(date_string)
    if dr.year_start is None and dr.year_end is None:
        return result

    fmt = dr.source_format

    # Century or active-period → active_year at midpoint
    if fmt in ("hebrew_century", "english_century", "hebrew_active"):
        if dr.year_start and dr.year_end:
            result["active_year"] = (dr.year_start + dr.year_end) // 2
        elif dr.year_start:
            result["active_year"] = dr.year_start
        return result

    # Death-only or birth-only (נפטר / נולד patterns)
    if dr.year_start is not None and dr.year_end is None:
        # Could be open-ended birth (1661-) or born-only
        result["birth_year"] = dr.year_start
    elif dr.year_start is None and dr.year_end is not None:
        result["death_year"] = dr.year_end
    elif dr.year_start is not None and dr.year_end is not None:
        if dr.year_start == dr.year_end:
            # Single year → treat as active
            result["active_year"] = dr.year_start
        else:
            result["birth_year"] = dr.year_start
            result["death_year"] = dr.year_end

    return result


def dates_overlap(
    range_a: DateRange, range_b: DateRange, tolerance: int = 5,
) -> bool:
    """Check whether two ``DateRange`` values overlap within *tolerance* years.

    Useful for verifying that a candidate authority record's dates are
    compatible with the dates from a MARC record.

    >>> a = DateRange(1540, 1620)
    >>> b = DateRange(1543, 1630)
    >>> dates_overlap(a, b)
    True
    >>> c = DateRange(1200, 1260)
    >>> dates_overlap(a, c)
    False
    """
    a_start = range_a.year_start
    a_end = range_a.year_end
    b_start = range_b.year_start
    b_end = range_b.year_end

    # If either side has no dates at all, cannot disprove overlap → True
    if (a_start is None and a_end is None) or (b_start is None and b_end is None):
        return True

    # Fill gaps: if only one boundary is known, extend generously
    # (a birth-only date should overlap with a death-only date in a plausible range)
    if a_start is None:
        a_start = (a_end or 0) - 120  # generous lifespan
    if a_end is None:
        a_end = (a_start or 0) + 120
    if b_start is None:
        b_start = (b_end or 0) - 120
    if b_end is None:
        b_end = (b_start or 0) + 120

    # Standard overlap test with tolerance
    return a_start - tolerance <= b_end and b_start - tolerance <= a_end
