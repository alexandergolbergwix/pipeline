"""Tests for converter.transformer.date_resolver — Hebrew/Gregorian date parsing.

Every function in date_resolver is pure (no I/O, no side effects),
so these tests are deterministic and fast.
"""

from __future__ import annotations

import pytest
from converter.transformer.date_resolver import (
    DateRange,
    dates_overlap,
    hebrew_letters_to_gematria,
    hebrew_year_to_gregorian,
    resolve,
    resolve_person_dates,
)

# ── Gematria ─────────────────────────────────────────────────────────────────


class TestGematria:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("א", 1),
            ("י", 10),
            ("ק", 100),
            ("ת", 400),
            ("תנד", 454),
            ('תנ"ד', 454),
            ("שסד", 364),
            ('שס"ד', 364),
            ('ת"ג', 403),
            ('תכ"א', 421),
            ('שע"ז', 377),
            ("תשפו", 786),
            ('תשפ"ו', 786),
            # Final forms
            ("ך", 20),
            ("ם", 40),
            ("ן", 50),
            ("ף", 80),
            ("ץ", 90),
            # Empty / no Hebrew
            ("", 0),
            ("hello", 0),
            ("1234", 0),
        ],
    )
    def test_gematria_values(self, text: str, expected: int) -> None:
        assert hebrew_letters_to_gematria(text) == expected

    def test_strips_geresh_and_gershayim(self) -> None:
        assert hebrew_letters_to_gematria('ת"ג') == hebrew_letters_to_gematria("תג")
        assert hebrew_letters_to_gematria("ת׳ג") == hebrew_letters_to_gematria("תג")


# ── Hebrew year conversion ───────────────────────────────────────────────────


class TestHebrewYearConversion:
    @pytest.mark.parametrize(
        "gematria, expected_gregorian",
        [
            (454, 1694),  # תנ"ד
            (364, 1604),  # שס"ד
            (403, 1643),  # ת"ג
            (421, 1661),  # תכ"א
            (377, 1617),  # שע"ז
            (786, 2026),  # תשפ"ו
            (360, 1600),  # ש"ס
        ],
    )
    def test_minor_era(self, gematria: int, expected_gregorian: int) -> None:
        assert hebrew_year_to_gregorian(gematria) == expected_gregorian

    @pytest.mark.parametrize(
        "gematria, expected_gregorian",
        [
            (1964, 1652),  # א'תתקס"ג
            (1939, 1627),  # א'תתקל"ח
        ],
    )
    def test_seleucid_era(self, gematria: int, expected_gregorian: int) -> None:
        assert hebrew_year_to_gregorian(gematria, era="seleucid") == expected_gregorian


# ── resolve() — all 12 format types ─────────────────────────────────────────


class TestResolve:
    # Gregorian formats
    @pytest.mark.parametrize(
        "text, expected_start, expected_end, expected_format",
        [
            ("1542-1620", 1542, 1620, "gregorian_range"),
            ("1138-1204", 1138, 1204, "gregorian_range"),
            ("882-942", 882, 942, "gregorian_range"),
        ],
    )
    def test_gregorian_range(
        self,
        text: str,
        expected_start: int,
        expected_end: int,
        expected_format: str,
    ) -> None:
        dr = resolve(text)
        assert dr.year_start == expected_start
        assert dr.year_end == expected_end
        assert dr.source_format == expected_format
        assert dr.approximate is False

    @pytest.mark.parametrize(
        "text, expected_start, expected_end",
        [
            ("1570?-1667", 1570, 1667),
            ("?1270-?1340", 1270, 1340),
            ("1525?-1572", 1525, 1572),
        ],
    )
    def test_gregorian_uncertain_range(
        self,
        text: str,
        expected_start: int,
        expected_end: int,
    ) -> None:
        dr = resolve(text)
        assert dr.year_start == expected_start
        assert dr.year_end == expected_end
        assert dr.source_format == "gregorian_uncertain_range"
        assert dr.approximate is True

    def test_gregorian_open(self) -> None:
        dr = resolve("1661-")
        assert dr == DateRange(1661, None, False, "gregorian_open")

    def test_gregorian_single(self) -> None:
        dr = resolve("1650")
        assert dr == DateRange(1650, 1650, False, "gregorian_single")

    # Hebrew century
    @pytest.mark.parametrize(
        "text, expected_start, expected_end",
        [
            ("המאה ה-17", 1601, 1700),
            ("המאה ה-10", 901, 1000),
            ('המאה הי"ז', 1601, 1700),
            ('המאה הי"א', 1001, 1100),
            ("המאה ה-16-17", 1501, 1700),
        ],
    )
    def test_hebrew_century(
        self,
        text: str,
        expected_start: int,
        expected_end: int,
    ) -> None:
        dr = resolve(text)
        assert dr.year_start == expected_start
        assert dr.year_end == expected_end
        assert dr.source_format == "hebrew_century"
        assert dr.approximate is True

    # Hebrew active
    @pytest.mark.parametrize(
        "text, expected_start, expected_end",
        [
            ('פעל במאה הי"ז', 1601, 1700),
            ("פעיל 1407", 1407, 1407),
            ("פעל במאה ה-16-17", 1501, 1700),
        ],
    )
    def test_hebrew_active(
        self,
        text: str,
        expected_start: int | None,
        expected_end: int | None,
    ) -> None:
        dr = resolve(text)
        assert dr.year_start == expected_start
        assert dr.year_end == expected_end
        assert dr.approximate is True

    # Hebrew approximate
    @pytest.mark.parametrize(
        "text, expected_start, expected_end",
        [
            ("1570 בערך-1643", 1570, 1643),
            ("בערך 1490-1577", 1490, 1577),
            ("נפטר 1628 בערך", None, 1628),
            ("נולד 1540 בערך", 1540, None),
            ("1570 בערך", 1570, 1570),
        ],
    )
    def test_hebrew_approximate(
        self,
        text: str,
        expected_start: int | None,
        expected_end: int | None,
    ) -> None:
        dr = resolve(text)
        assert dr.year_start == expected_start
        assert dr.year_end == expected_end
        assert dr.approximate is True

    # English approximate
    def test_english_approximate_range(self) -> None:
        dr = resolve("approximately 1570-approximately 1639")
        assert dr == DateRange(1570, 1639, True, "english_approximate")

    def test_english_ca(self) -> None:
        dr = resolve("ca. 1650")
        assert dr == DateRange(1650, 1650, True, "english_approximate")

    # English century
    @pytest.mark.parametrize(
        "text, expected_start, expected_end",
        [
            ("17th cent.", 1601, 1700),
            ("16th-17th cent.", 1501, 1700),
        ],
    )
    def test_english_century(
        self,
        text: str,
        expected_start: int,
        expected_end: int,
    ) -> None:
        dr = resolve(text)
        assert dr.year_start == expected_start
        assert dr.year_end == expected_end
        assert dr.source_format == "english_century"

    # Publication Hebrew year
    @pytest.mark.parametrize(
        "text, expected_year",
        [
            ('ת"ן (1690).', 1690),
            ('שס"ד (1604).', 1604),
            ('תכ"א (1661).', 1661),
        ],
    )
    def test_publication_hebrew_year(self, text: str, expected_year: int) -> None:
        dr = resolve(text)
        assert dr.year_start == expected_year
        assert dr.year_end == expected_year
        assert dr.source_format == "publication_hebrew_year"

    # Seleucid era
    def test_seleucid_era(self) -> None:
        dr = resolve("א'תתקס\"א לשטרות (1650)")
        assert dr.year_start == 1650
        assert dr.source_format == "seleucid_era"

    # Standalone Hebrew year (last resort)
    @pytest.mark.parametrize(
        "text, expected_year",
        [
            ('ת"ג', 1643),
            ('ש"ס', 1600),
            ('תכ"א', 1661),
        ],
    )
    def test_standalone_hebrew_year(self, text: str, expected_year: int) -> None:
        dr = resolve(text)
        assert dr.year_start == expected_year
        assert dr.source_format == "standalone_hebrew_year"

    # Edge cases
    def test_empty_string(self) -> None:
        dr = resolve("")
        assert dr.year_start is None
        assert dr.source_format == "unresolved"

    def test_none_like(self) -> None:
        dr = resolve("   ")
        assert dr.source_format == "unresolved"

    def test_garbage_returns_unresolved(self) -> None:
        dr = resolve("no date here at all")
        assert dr.source_format == "unresolved"


# ── resolve_person_dates() ───────────────────────────────────────────────────


class TestResolvePersonDates:
    def test_birth_and_death(self) -> None:
        assert resolve_person_dates("1542-1620") == {
            "birth_year": 1542,
            "death_year": 1620,
            "active_year": None,
        }

    def test_open_ended_birth(self) -> None:
        assert resolve_person_dates("1661-") == {
            "birth_year": 1661,
            "death_year": None,
            "active_year": None,
        }

    def test_death_only(self) -> None:
        result = resolve_person_dates("נפטר 1628 בערך")
        assert result["death_year"] == 1628
        assert result["birth_year"] is None

    def test_birth_only(self) -> None:
        result = resolve_person_dates("נולד 1540 בערך")
        assert result["birth_year"] == 1540
        assert result["death_year"] is None

    def test_century_yields_active(self) -> None:
        result = resolve_person_dates("המאה ה-17")
        assert result["active_year"] == 1650
        assert result["birth_year"] is None
        assert result["death_year"] is None

    def test_active_period(self) -> None:
        result = resolve_person_dates("פעיל 1407")
        assert result["active_year"] == 1407

    def test_empty_string(self) -> None:
        result = resolve_person_dates("")
        assert result == {"birth_year": None, "death_year": None, "active_year": None}

    def test_single_year(self) -> None:
        result = resolve_person_dates("1650")
        assert result["active_year"] == 1650


# ── dates_overlap() ──────────────────────────────────────────────────────────


class TestDatesOverlap:
    def test_overlapping_ranges(self) -> None:
        a = DateRange(1540, 1620)
        b = DateRange(1543, 1630)
        assert dates_overlap(a, b) is True

    def test_non_overlapping_ranges(self) -> None:
        a = DateRange(1540, 1620)
        b = DateRange(1200, 1260)
        assert dates_overlap(a, b) is False

    def test_tolerance_bridges_small_gap(self) -> None:
        a = DateRange(1540, 1620)
        b = DateRange(1623, 1700)
        assert dates_overlap(a, b, tolerance=5) is True

    def test_no_tolerance_gap_fails(self) -> None:
        a = DateRange(1540, 1620)
        b = DateRange(1623, 1700)
        assert dates_overlap(a, b, tolerance=0) is False

    def test_none_dates_always_overlap(self) -> None:
        a = DateRange(None, None)
        b = DateRange(1540, 1620)
        assert dates_overlap(a, b) is True

    def test_partial_dates(self) -> None:
        a = DateRange(1540, None)
        b = DateRange(None, 1620)
        assert dates_overlap(a, b) is True

    def test_identical_ranges(self) -> None:
        a = DateRange(1600, 1700)
        assert dates_overlap(a, a) is True
