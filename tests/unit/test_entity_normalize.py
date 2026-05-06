"""Unit tests for :mod:`ner.entity_normalize`.

Covers the trailing-punctuation cases observed in the 2026-05-03 audit
of ``test_subset/ner_results.json`` (38 of 137 entities had garbage
edges) plus Hebrew-abbreviation preservation invariants.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from ner.entity_normalize import normalize_entity_text


# ── Real-world bad strings from the audit ─────────────────────────────


def test_strip_trailing_period() -> None:
    assert normalize_entity_text("מ' גסטר.") == "מ' גסטר"


def test_strip_trailing_period_paren() -> None:
    assert normalize_entity_text("Some Author).") == "Some Author"


def test_strip_trailing_paren_only() -> None:
    assert normalize_entity_text("Author Name)") == "Author Name"


def test_strip_trailing_paren_colon_space() -> None:
    assert normalize_entity_text("Author Name) :") == "Author Name"


def test_strip_marc_gregorian_equivalent_brackets() -> None:
    """``[=1826],`` is MARC's Gregorian-equivalent year marker; the ``=`` is
    part of the wrapper, not the value, so it is stripped."""
    assert normalize_entity_text("[=1826],") == "1826"


def test_strip_marc_approximate_year_marker() -> None:
    """``[~1500]`` is the approximate-year marker — also a wrapper."""
    assert normalize_entity_text("[~1500]") == "1500"


def test_strip_trailing_bracket_period() -> None:
    assert normalize_entity_text("Item Reference].") == "Item Reference"


def test_strip_trailing_ellipsis() -> None:
    assert normalize_entity_text("Truncated name…") == "Truncated name"


def test_strip_wrapping_double_quotes_and_period() -> None:
    """``'"name".'`` → ``'name'``. Both edges + the period are removed."""
    assert (
        normalize_entity_text('"משה יהודה הכמה"ר מהללאל".')
        == 'משה יהודה הכמה"ר מהללאל'
    )


def test_halberstam_89() -> None:
    assert normalize_entity_text("הלברשטם 89.") == "הלברשטם 89"


# ── Hebrew abbreviations preserved ────────────────────────────────────


def test_internal_gershayim_preserved() -> None:
    """Hebrew abbreviations like רמב"ם carry an internal ASCII double quote
    used as gershayim. The normaliser must not strip it."""
    assert normalize_entity_text('רמב"ם') == 'רמב"ם'


def test_internal_geresh_preserved() -> None:
    """``מ' גסטר`` (ASCII apostrophe used as geresh) — the apostrophe is
    integral to the abbreviation."""
    assert normalize_entity_text("מ' גסטר") == "מ' גסטר"


def test_geresh_at_end_preserved() -> None:
    """A name legitimately ending in geresh (rare but possible) keeps it,
    because the trailing class deliberately excludes ASCII apostrophe."""
    assert normalize_entity_text("י'") == "י'"


def test_compound_abbreviation_with_geresh_and_punct() -> None:
    """Real example: ``"רב' שמעון בן יוחאי."`` — strip wrapping quotes +
    trailing period, keep internal geresh."""
    assert normalize_entity_text('"רב\' שמעון בן יוחאי."') == "רב' שמעון בן יוחאי"


# ── Edge cases ────────────────────────────────────────────────────────


def test_empty_input() -> None:
    assert normalize_entity_text("") == ""


def test_pure_whitespace() -> None:
    assert normalize_entity_text("   \t\n  ") == ""


def test_pure_punctuation() -> None:
    assert normalize_entity_text(".,;:!?") == ""


def test_collapses_internal_whitespace() -> None:
    assert normalize_entity_text("First    Last") == "First Last"


def test_strips_nbsp_and_zero_width() -> None:
    """Non-breaking space (U+00A0) and zero-width space (U+200B) at edges."""
    assert normalize_entity_text("\u00a0Author\u200b") == "Author"


def test_no_change_when_clean() -> None:
    assert normalize_entity_text("Hayyim Vital") == "Hayyim Vital"


def test_no_change_when_hebrew_clean() -> None:
    assert normalize_entity_text("ויטל, חיים בן יוסף") == "ויטל, חיים בן יוסף"
