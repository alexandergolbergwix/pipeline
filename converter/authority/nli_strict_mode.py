"""NLI Strict Mode (E_AUTH_F4).

Authority resolution gate that prefers Mazal/NLI over VIAF SRU search.

Background — why this module exists
-----------------------------------

VIAF's SRU `/search/viaf` endpoint is unreliable for Hebrew queries
(see CLAUDE.md Rule 11 on the JSON-Accept-header regression and
Rule 29 on the nameType cross-validation incident, where corporate
clusters were silently returned as person matches). The library
therefore prefers an authoritative NLI/Mazal hit over a VIAF SRU
candidate whenever both are available.

This module wraps that preference into a single function,
``resolve_with_nli_priority``, which:

1. **Path 1 — exact Mazal match.** Calls ``mazal.match_person(name, dates)``.
   If Mazal returns an NLI ID, that is the authoritative answer; we do
   *not* call VIAF SRU. The caller (Stage 3 / F2 Wikidata crosscheck)
   is expected to follow up the Mazal ID with a Wikidata SPARQL query
   to harvest the VIAF identifier through P8189 cross-references —
   that path is more reliable than VIAF SRU for Hebrew names.

2. **Path 2 — Levenshtein near-miss.** When exact match fails *and*
   the matcher exposes an optional ``iter_person_candidates(name)``
   method (a Protocol, not a hard dependency), we scan candidate
   strings within edit distance ≤ 2 of the normalised query. If the
   real ``MazalMatcher`` does not expose such an iterator we return
   a ``fallback`` result and let the existing VIAF SRU path run
   downstream, exactly as before. The optional iterator keeps this
   module additive — no rewrite of ``mazal_matcher.py`` is required.

3. **Path 3 — fallback.** No Mazal hit, no near-miss. Caller falls
   back to its existing VIAF SRU pipeline.

Schema decision
---------------

The Mazal authority schema (``converter/authority/mazal_index.py``,
``CREATE TABLE authorities`` at lines 102–110) has columns::

    nli_id, entity_type, preferred_name_heb, preferred_name_lat,
    dates, aleph_id

There is **no** ``viaf_id`` column. The Plan's "Mazal-prefers-VIAF"
optimisation therefore collapses to "Mazal-only when matched": Path 1
returns ``viaf_uri=None`` with ``confidence_hint="high"``. F2's
downstream Wikidata crosscheck will resolve a VIAF identifier from the
NLI ID (via Wikidata's P8189→P214 cross-link) more reliably than
VIAF SRU search would.

We deliberately do **not** modify the schema — that is out of scope
for F4 and would require re-ingesting the NLI authority XML dumps.

Disable
-------

Set ``MHM_DISABLE_NLI_STRICT=1`` in the environment to bypass this
module entirely. ``is_enabled()`` returns ``False`` and
``resolve_with_nli_priority`` returns an immediate fallback result
without touching the matcher.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol


# ── Public types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrictMatchResult:
    """Outcome of an NLI-priority lookup.

    Attributes:
        mazal_id: NLI authority ID (e.g. ``"987007414776605171"``) when a
            Mazal record was matched (Path 1 or Path 2). ``None`` for
            Path 3.
        viaf_uri: Always ``None`` — the current Mazal schema carries no
            VIAF column. Kept on the dataclass so callers can adopt the
            interface today; the value will become populated when (and
            if) the schema grows a ``viaf_id`` column.
        source: Which path produced the match. ``"nli_strict"`` for
            an exact Mazal hit; ``"nli_levenshtein"`` for a normalised
            edit-distance ≤ 2 near-miss; ``"fallback"`` otherwise.
        near_miss_suggestion: For Path 2, the best Mazal candidate
            string (preferred Hebrew form). ``None`` for Paths 1 and 3.
        confidence_hint: ``"high"`` for exact Mazal matches,
            ``"medium"`` for Levenshtein near-misses, ``"low"`` for
            fallback. Stage-3 guards may further degrade this.
    """

    mazal_id: str | None
    viaf_uri: str | None
    source: Literal["nli_strict", "nli_levenshtein", "fallback"]
    near_miss_suggestion: str | None
    confidence_hint: Literal["high", "medium", "low"]


class _MazalProtocol(Protocol):
    """Minimal surface F4 needs from a MazalMatcher.

    The real ``MazalMatcher`` (``converter/authority/mazal_matcher.py``)
    satisfies the ``match_person`` part of this protocol. Path 2's
    Levenshtein scan additionally requires an optional
    ``iter_person_candidates`` method; if it is not exposed the F4
    falls through to the Path-3 fallback rather than rewriting the
    matcher.
    """

    def match_person(
        self, name: str, dates: str | None = None
    ) -> str | None:  # pragma: no cover - structural
        ...


# ── Module-level constants ────────────────────────────────────────────

_KILL_SWITCH_ENV = "MHM_DISABLE_NLI_STRICT"

# Levenshtein near-miss is accepted up to and including this many edits
# of the normalised string. Beyond it, the cataloguer's spelling and the
# authority record's spelling are too different for an automatic match.
_LEVENSHTEIN_THRESHOLD = 2

# Hebrew cantillation marks (te'amim): U+0591..U+05AF.
_CANTILLATION_RE = re.compile(r"[\u0591-\u05AF]")

# Hebrew vowel points (nikud) — split out from the wider 0x0591..0x05C7
# block because the consonantal markers in that block (sin/shin dot,
# rafe-less placeholder, etc.) overlap with cantillation; we strip the
# vowel-only ranges below.
_NIKUD_RE = re.compile(r"[\u05B0-\u05BD\u05C1\u05C2\u05C4\u05C5\u05C7]")

# Punctuation / quotes that catalogue strings tend to drag in around a
# name. We strip them (rather than including them in the distance
# computation) so that ``כרמי,`` and ``כרמי`` are treated as the same
# normalised form.
_PUNCT_RE = re.compile(r'[\s\.,;:"\'\u05f3\u05f4\(\)\[\]]+')


# ── Kill-switch ───────────────────────────────────────────────────────


def is_enabled() -> bool:
    """Return ``False`` when the strict-mode kill-switch is active.

    The kill-switch is the env var ``MHM_DISABLE_NLI_STRICT``. Set it
    to ``"1"`` to bypass this module entirely; any other value (including
    unset / empty) leaves the strict mode enabled.
    """
    return os.environ.get(_KILL_SWITCH_ENV, "") != "1"


# ── Hebrew normalisation ──────────────────────────────────────────────


def _normalise_hebrew(text: str) -> str:
    """Strip nikud, cantillation, punctuation; collapse whitespace.

    The output is the canonical form used by both the Levenshtein
    distance and the candidate-string comparison. We keep Latin
    characters as-is so the helper also works for transliterated NLI
    forms (``Carpi, Yahuda Hayyim``).
    """
    if not text:
        return ""
    # NFD splits combining diacritics from base letters so the regex
    # ranges below catch them regardless of the input form (NFC vs NFD).
    decomposed = unicodedata.normalize("NFD", text)
    stripped = _CANTILLATION_RE.sub("", decomposed)
    stripped = _NIKUD_RE.sub("", stripped)
    # Drop any other combining marks (Mn) that survived the explicit
    # range strip — covers Latin diacritics on transliterated names.
    stripped = "".join(
        ch for ch in stripped if unicodedata.category(ch) != "Mn"
    )
    # Recompose what's left so multi-codepoint Hebrew letters compare
    # consistently against single-codepoint forms in the index.
    recomposed = unicodedata.normalize("NFC", stripped)
    collapsed = _PUNCT_RE.sub(" ", recomposed).strip()
    # Final whitespace collapse — internal multiple spaces to one.
    return re.sub(r"\s+", " ", collapsed).lower()


def levenshtein_normalized_hebrew(a: str, b: str) -> int:
    """Return the edit distance between two Hebrew/Latin name strings.

    Both inputs are first normalised by :func:`_normalise_hebrew`
    (cantillation + nikud stripped, punctuation collapsed, lowercased).
    The body is a standard two-row Wagner–Fischer Levenshtein — pure
    Python, no third-party dependency.
    """
    s = _normalise_hebrew(a)
    t = _normalise_hebrew(b)
    if s == t:
        return 0
    if not s:
        return len(t)
    if not t:
        return len(s)
    # Two-row dynamic programming. Costs: insert/delete/substitute = 1.
    previous_row = list(range(len(t) + 1))
    for i, sc in enumerate(s, start=1):
        current_row: list[int] = [i]
        for j, tc in enumerate(t, start=1):
            insert_cost = current_row[j - 1] + 1
            delete_cost = previous_row[j] + 1
            substitute_cost = previous_row[j - 1] + (0 if sc == tc else 1)
            current_row.append(min(insert_cost, delete_cost, substitute_cost))
        previous_row = current_row
    return previous_row[-1]


# ── Path 2 helper: optional candidate iterator ────────────────────────


def _iter_candidates(
    matcher: _MazalProtocol, name: str
) -> Iterable[tuple[str, str]] | None:
    """Return ``(candidate_string, nli_id)`` pairs from the matcher, or None.

    Uses ``getattr`` so the optional ``iter_person_candidates`` method
    can be supplied by a wrapper or a test double without modifying
    the real ``MazalMatcher`` class. When the matcher does not expose
    this iterator, we return ``None`` and the caller falls through to
    the Path-3 fallback (per spec — we do not invent a full-table scan
    on the SQLite index).
    """
    iter_method = getattr(matcher, "iter_person_candidates", None)
    if iter_method is None or not callable(iter_method):
        return None
    try:
        return iter_method(name)
    except Exception:
        # A misbehaving optional iterator should not break the strict
        # mode — degrade silently to Path 3.
        return None


# ── Public entrypoint ────────────────────────────────────────────────


def resolve_with_nli_priority(
    name: str,
    mazal: _MazalProtocol,
    *,
    name_dates: str | None = None,
) -> StrictMatchResult:
    """Resolve ``name`` to a Mazal record before consulting VIAF SRU.

    See module docstring for the three-path decision table.

    Args:
        name: The MARC source name to resolve. Empty / whitespace-only
            inputs return an immediate fallback.
        mazal: A matcher object satisfying ``_MazalProtocol``. The real
            ``MazalMatcher`` qualifies; tests pass a small mock.
        name_dates: Optional disambiguating date string from MARC 100$d
            (e.g. ``"1138-1204"``). Forwarded to ``match_person``.

    Returns:
        A frozen :class:`StrictMatchResult` capturing which path fired
        and what was found. Never raises on lookup failure.
    """
    if not is_enabled():
        return StrictMatchResult(
            mazal_id=None,
            viaf_uri=None,
            source="fallback",
            near_miss_suggestion=None,
            confidence_hint="low",
        )

    if not name or not name.strip():
        return StrictMatchResult(
            mazal_id=None,
            viaf_uri=None,
            source="fallback",
            near_miss_suggestion=None,
            confidence_hint="low",
        )

    # ── Path 1 — exact Mazal match ────────────────────────────────────
    try:
        mazal_id = mazal.match_person(name, dates=name_dates)
    except TypeError:
        # Backwards-compat: older matchers used positional ``dates``.
        mazal_id = mazal.match_person(name, name_dates)

    if mazal_id:
        # Schema decision: no viaf_id column on the authorities table,
        # so viaf_uri is always None on Path 1. F2's Wikidata crosscheck
        # resolves the VIAF later via P8189.
        return StrictMatchResult(
            mazal_id=str(mazal_id),
            viaf_uri=None,
            source="nli_strict",
            near_miss_suggestion=None,
            confidence_hint="high",
        )

    # ── Path 2 — Levenshtein near-miss ────────────────────────────────
    candidates = _iter_candidates(mazal, name)
    if candidates is None:
        return StrictMatchResult(
            mazal_id=None,
            viaf_uri=None,
            source="fallback",
            near_miss_suggestion=None,
            confidence_hint="low",
        )

    best_distance = _LEVENSHTEIN_THRESHOLD + 1
    best_candidate: tuple[str, str] | None = None
    for candidate_name, candidate_id in candidates:
        if not candidate_name or not candidate_id:
            continue
        distance = levenshtein_normalized_hebrew(name, candidate_name)
        if distance < best_distance:
            best_distance = distance
            best_candidate = (candidate_name, str(candidate_id))
            if distance == 0:
                # Exact normalised match on a candidate that the SQLite
                # index missed (different surface form). Treat as Path 1.
                return StrictMatchResult(
                    mazal_id=str(candidate_id),
                    viaf_uri=None,
                    source="nli_strict",
                    near_miss_suggestion=None,
                    confidence_hint="high",
                )

    if best_candidate is not None and best_distance <= _LEVENSHTEIN_THRESHOLD:
        suggestion, suggestion_id = best_candidate
        return StrictMatchResult(
            mazal_id=suggestion_id,
            viaf_uri=None,
            source="nli_levenshtein",
            near_miss_suggestion=suggestion,
            confidence_hint="medium",
        )

    # ── Path 3 — fallback ─────────────────────────────────────────────
    return StrictMatchResult(
        mazal_id=None,
        viaf_uri=None,
        source="fallback",
        near_miss_suggestion=None,
        confidence_hint="low",
    )
