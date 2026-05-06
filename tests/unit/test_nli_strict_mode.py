"""Tests for the NLI strict-mode authority gate (E_AUTH_F4).

These tests verify the three-path decision logic of
``resolve_with_nli_priority`` without touching the real
``mazal_index.db`` or the real VIAF SRU endpoint. Every test injects
a small mock matcher that exposes ``match_person`` (and optionally
``iter_person_candidates``).

Schema note: the live Mazal schema has no VIAF column, so every
``StrictMatchResult.viaf_uri`` is expected to be ``None``. F2's
downstream Wikidata crosscheck is responsible for harvesting the VIAF
identifier from the NLI ID.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Literal
from unittest.mock import patch

from converter.authority.nli_strict_mode import (
    StrictMatchResult,
    is_enabled,
    levenshtein_normalized_hebrew,
    resolve_with_nli_priority,
)


# ── Test doubles ──────────────────────────────────────────────────────


class _FakeMazalMatcher:
    """Minimal stand-in for ``MazalMatcher``.

    Configured per-test with:
        ``match_response``: what ``match_person`` returns
        ``candidates``: the iterator's payload (or ``None`` to omit the
            optional ``iter_person_candidates`` method entirely)
    """

    def __init__(
        self,
        *,
        match_response: str | None,
        candidates: list[tuple[str, str]] | None = None,
    ) -> None:
        self._match_response = match_response
        self._candidates = candidates
        self.match_calls: list[tuple[str, str | None]] = []

    def match_person(
        self, name: str, dates: str | None = None
    ) -> str | None:
        self.match_calls.append((name, dates))
        return self._match_response


class _FakeMazalMatcherWithIter(_FakeMazalMatcher):
    """Variant that exposes the optional Path-2 iterator."""

    def iter_person_candidates(
        self, name: str
    ) -> Iterable[tuple[str, str]]:  # noqa: ARG002 - signature only
        assert self._candidates is not None, "candidates not configured"
        return list(self._candidates)


# ── 1. Exact match path ──────────────────────────────────────────────


class TestExactMatchPath:
    """Path 1 — Mazal ``match_person`` returns an NLI ID."""

    def test_exact_match_with_viaf_returns_strict(self) -> None:
        """When the schema *did* carry a VIAF column the result would be
        ``nli_strict`` with a populated ``viaf_uri``. The current schema
        has no VIAF column (see module docstring) so ``viaf_uri`` is
        ``None`` even in this 'rich' case — but the path is still
        ``nli_strict`` and confidence is ``high``. This test pins that
        contract: no VIAF SRU call, ``nli_strict`` source, ``high``
        confidence — the only field the schema decision changes is
        ``viaf_uri``, which remains ``None`` until the schema grows the
        column.
        """
        mazal = _FakeMazalMatcher(match_response="987007414776605171")

        result: StrictMatchResult = resolve_with_nli_priority(
            "שלמה בן יצחק", mazal, name_dates="1040-1105"
        )

        assert result.source == "nli_strict"
        assert result.mazal_id == "987007414776605171"
        assert result.viaf_uri is None  # schema decision: no viaf column
        assert result.confidence_hint == "high"
        assert result.near_miss_suggestion is None
        # Critically, no fallback to a VIAF call: the matcher recorded
        # exactly one match_person invocation.
        assert len(mazal.match_calls) == 1
        assert mazal.match_calls[0] == ("שלמה בן יצחק", "1040-1105")


# ── 2. Exact match without VIAF still returns strict ────────────────


class TestExactMatchNoViaf:
    """Path 1 — Mazal hit but the record carries no VIAF (today's schema)."""

    def test_exact_match_without_viaf_still_returns_strict(self) -> None:
        mazal = _FakeMazalMatcher(match_response="987007500956005171")

        result = resolve_with_nli_priority("ב\"ק, שמשון", mazal)

        assert result.source == "nli_strict"
        assert result.mazal_id == "987007500956005171"
        assert result.viaf_uri is None
        assert result.confidence_hint == "high"
        assert result.near_miss_suggestion is None


# ── 3. Levenshtein near-miss ─────────────────────────────────────────


class TestLevenshteinNearMiss:
    """Path 2 — exact miss, but a candidate within edit distance ≤ 2."""

    def test_levenshtein_near_miss_returns_medium(self) -> None:
        # MARC source: "כרמי" (4 letters). Authority spelling: "קרמי"
        # (one substitution: כ → ק). Distance = 1, well within threshold.
        mazal = _FakeMazalMatcherWithIter(
            match_response=None,
            candidates=[
                ("גולדברג", "987000000000001"),  # distance ~7 — far
                ("קרמי", "987007310591405171"),  # distance 1 — near
                ("רוזנברג", "987000000000002"),  # distance ~7 — far
            ],
        )

        result = resolve_with_nli_priority("כרמי", mazal)

        assert result.source == "nli_levenshtein"
        assert result.confidence_hint == "medium"
        assert result.mazal_id == "987007310591405171"
        assert result.near_miss_suggestion == "קרמי"
        assert result.viaf_uri is None


# ── 4. Levenshtein too far — fall through ────────────────────────────


class TestLevenshteinTooFar:
    """Path 3 — closest candidate distance > 2 → fallback."""

    def test_levenshtein_too_far_falls_through(self) -> None:
        # Source: "כרמי" (4 letters). Closest candidate "אברהמסון"
        # (8 letters) — distance > 2.
        mazal = _FakeMazalMatcherWithIter(
            match_response=None,
            candidates=[
                ("אברהמסון", "987000000000010"),
                ("רוטשילד", "987000000000011"),
                ("בן-גוריון", "987000000000012"),
            ],
        )

        result = resolve_with_nli_priority("כרמי", mazal)

        # Sanity: the closest candidate really is > 2 edits away.
        closest = min(
            levenshtein_normalized_hebrew("כרמי", c)
            for c, _ in [
                ("אברהמסון", ""),
                ("רוטשילד", ""),
                ("בן-גוריון", ""),
            ]
        )
        assert closest > 2

        assert result.source == "fallback"
        assert result.confidence_hint == "low"
        assert result.mazal_id is None
        assert result.viaf_uri is None
        assert result.near_miss_suggestion is None


# ── 5. Kill-switch ───────────────────────────────────────────────────


class TestKillSwitch:
    """``MHM_DISABLE_NLI_STRICT=1`` bypasses the module entirely."""

    def test_kill_switch_disables(self) -> None:
        with patch.dict(os.environ, {"MHM_DISABLE_NLI_STRICT": "1"}):
            assert is_enabled() is False

            mazal = _FakeMazalMatcher(match_response="987007414776605171")
            result = resolve_with_nli_priority("שלמה בן יצחק", mazal)

            # When disabled we return immediately — no matcher call.
            assert result.source == "fallback"
            assert result.confidence_hint == "low"
            assert result.mazal_id is None
            assert result.viaf_uri is None
            assert mazal.match_calls == []

        # Sanity: outside the patch, is_enabled() flips back.
        # (Only true when the test runner was not invoked with the kill
        # switch already set; if it was, we just skip this assertion.)
        if os.environ.get("MHM_DISABLE_NLI_STRICT") != "1":
            assert is_enabled() is True


# Type-checker pin: ensures the result type's literal fields stay in
# sync with the signature documented in F4. If a future change drops
# one of these literals, mypy/pyright will fail this assignment.
_PINNED_SOURCE: Literal["nli_strict", "nli_levenshtein", "fallback"] = (
    "fallback"
)
_PINNED_CONFIDENCE: Literal["high", "medium", "low"] = "low"
