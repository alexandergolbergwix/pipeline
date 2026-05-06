"""Stage 3 authority-matching guards (pure helpers).

These guards address the 22 false-positive matches identified by the
2026-04-30 manual review of Stage 3 (`E_AUTH_REVIEW`). They run
*after* VIAF / Mazal return a candidate but *before* the match is
written into the authority-enriched JSON, so a wrong cluster never
reaches the GUI's auto-approve path.

Five guards:

1. ``_guard_date_conflict`` — drop matches where the candidate's
   biographical dates make the role implausible (e.g. born 200 y
   after the manuscript).
2. ``_guard_short_name_homonym`` — flag tiny MARC source names
   (``יעקב``, ``Esther``) when VIAF returns a richly-disambiguated
   cluster that almost certainly is a different person.
3. ``_guard_cluster_collapse`` — when two distinct MARC names in
   one record resolve to the same VIAF cluster, mark BOTH as
   ``low`` confidence. (Implemented as a post-pass over the match
   list, see :func:`apply_cluster_collapse`.)
4. ``_guard_placeholder_name`` — short cataloguer abbreviations
   (``א"א``, ``מל"י``, ``N.N.``) are not real persons and must
   never be sent to VIAF in the first place.
5. ``_score_confidence`` — assign ``high`` / ``medium`` / ``low``
   based on which signals agree.

The functions are pure (no I/O, no Qt) so unit tests mock the
matchers and feed synthetic data directly.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────

# Buffer years for date-imprecision when comparing manuscript year
# against a person's birth year. A scribe born 5 years after the
# catalogued manuscript date is still implausible but the slack
# absorbs typical century / round-year cataloguing imprecision.
DATE_BIRTH_BUFFER_YEARS = 5

# Maximum gap between a person's death year and the manuscript date
# for roles that imply active authorship/transcription.
DATE_DEATH_AFTERLIFE_YEARS = 80

# Maximum plausible lifespan: if a candidate died this many years AFTER
# the manuscript was made, they were almost certainly born after the MS
# was made too (impossible authorship). Used when the candidate has a
# death year but no birth year on record.
DATE_DEATH_POSTHUMOUS_YEARS = 120

# Roles where the person must have been physically present when the
# manuscript was made. A scribe / transcriber dying 80+ years before
# the MS could not have written it.
PHYSICAL_PRODUCTION_ROLES = frozenset(
    {
        "scribe",
        "transcriber",
        "copyist",
    }
)

# Roles where the person *authored a text* that the manuscript later
# copied. Hebrew manuscripts routinely copy medieval authors centuries
# after their death (Maimonides d.1204 in 17th-c. copies, Rashi d.1105,
# etc.). For these roles only the birth-year check matters: the author
# had to exist before the text could be authored. Death is unrelated
# to the MS copy date.
TEXTUAL_AUTHORSHIP_ROLES = frozenset(
    {
        "author",
        "translator",
        "commentator",
        "editor",
    }
)

# Backwards-compat alias — removed 2026-05-04 in favour of the split
# above. Existing call sites that still reference ``AUTHORSHIP_ROLES``
# get the union for least-surprise behaviour.
AUTHORSHIP_ROLES = PHYSICAL_PRODUCTION_ROLES | TEXTUAL_AUTHORSHIP_ROLES

# Common cataloguer placeholder substrings — case-insensitive contains
# match. ``N.N.`` (non nominatus) and friends are surface markers, not
# real authority entries.
_PLACEHOLDER_SUBSTRINGS: tuple[str, ...] = (
    "anonymous",
    "מחבר אלמוני",
    "פלוני",
    "אלמוני",
    "n.n.",
    " nn",
    "nn ",
)

_HEBREW_LETTER_ABBREV_RE = re.compile(r"^[\u05d0-\u05ea]\"[\u05d0-\u05ea]$")
# Single-letter initials with periods, optionally separated by comma /
# whitespace: ``M.J.``, ``M. J.``, ``א., א.``, ``A. B.``. The trailing
# period is optional because some catalogue entries strip it.
_LATIN_INITIALS_RE = re.compile(
    r"^[A-Za-z\u05d0-\u05ea]\.[\s,]*[A-Za-z\u05d0-\u05ea]\.?$"
)


# ── Helpers ───────────────────────────────────────────────────────────


def _clean_name(name: str) -> str:
    """Strip MARC trailing punctuation + whitespace from a person name."""
    return name.strip().rstrip(",;:.").strip()


def _tokenise(name: str) -> list[str]:
    """Split a person name into whitespace-/comma-delimited tokens."""
    raw = re.split(r"[\s,]+", _clean_name(name))
    return [t for t in raw if t]


def _parse_year(value: object) -> int | None:
    """Extract a four-digit year from a freeform date string.

    Accepts ``"1684"``, ``"d.1684"``, ``"1612-1684"``, ``"ca. 1700"``,
    integers, etc. Returns the *first* plausible year (100–2100) found
    in the string, or ``None``.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value if 100 < value < 2100 else None
    s = str(value)
    for match in re.finditer(r"\d{3,4}", s):
        try:
            yr = int(match.group(0))
        except ValueError:
            continue
        if 100 < yr < 2100:
            return yr
    return None


def extract_manuscript_year(record: dict[str, Any]) -> int | None:
    """Return the catalogued manuscript year, or ``None``.

    Reads ``record["dates"]["year"]`` first (Stage 0 already parses
    MARC 008 / 264$c / 260$c into this field). Falls back to the
    raw ``original_string`` when the structured year is missing.
    """
    dates = record.get("dates")
    if isinstance(dates, dict):
        year = dates.get("year")
        if isinstance(year, int) and 100 < year < 2100:
            return year
        original = dates.get("original_string")
        if original:
            return _parse_year(original)
    if isinstance(dates, str):
        return _parse_year(dates)
    return None


# ── Guard 4: placeholder name filter ─────────────────────────────────


def is_placeholder_name(name: str) -> bool:
    """Return True for cataloguer-style abbreviations / placeholders.

    These are not real persons and must never be sent to VIAF / Mazal:

    - ``א"א``, ``מל"י`` — Hebrew two-letter abbreviations
    - ``M.J.``, ``A. B.``, ``א., א.`` — initials with periods (any script)
    - bare letters length < 4 (``א.``, ``יע``)
    - substring of common placeholders (``Anonymous``, ``N.N.``,
      ``מחבר אלמוני``, ``פלוני``, ``אלמוני``)

    The regex checks run on the *raw* string (whitespace-stripped only) so
    we don't lose the trailing period that distinguishes ``א., א.`` from
    a real name.
    """
    raw = (name or "").strip()
    if not raw:
        return True
    # Letter-only count (drops punctuation, quotes, whitespace) — a real
    # name always has at least four characters of script content.
    letters_only = re.sub(r"[^A-Za-z\u05d0-\u05ea]", "", raw)
    if len(letters_only) < 4:
        return True
    # Strip cataloguer-style trailing comma/period for the abbreviation
    # checks but keep the inner punctuation (so ``א., א.`` still matches
    # the initials regex).
    abbrev_target = raw.rstrip(",;:")
    if _HEBREW_LETTER_ABBREV_RE.match(abbrev_target):
        return True
    if _LATIN_INITIALS_RE.match(abbrev_target):
        return True
    lower = raw.lower()
    for token in _PLACEHOLDER_SUBSTRINGS:
        if token in lower:
            return True
    return False


# ── Guard 1: date-conflict guard ─────────────────────────────────────


def evaluate_date_conflict(
    role: str,
    ms_year: int | None,
    person_birth_year: int | None,
    person_death_year: int | None,
) -> str | None:
    """Return a reason string if the dates are incompatible, else ``None``.

    Two role classes get different death-side treatment (Hebrew-MS-aware):

    * **PHYSICAL_PRODUCTION_ROLES** (scribe / transcriber / copyist) —
      the person physically wrote this manuscript. They cannot have
      died >80 years before its creation. Death-year check fires.
    * **TEXTUAL_AUTHORSHIP_ROLES** (author / translator / commentator /
      editor) — they authored a text which this manuscript copies.
      Hebrew manuscripts routinely copy medieval authors centuries
      after their death (Maimonides d.1204 in 17th-c. copies, Rashi
      d.1105, etc.). Only the *birth*-year check applies: the author
      must have existed before the text could be written. Death is
      unrelated to the copy's date.

    All roles always get the universal birth-year check (``born >
    ms_year + buffer`` is biologically impossible regardless of role)
    and the posthumous-by-too-much check (when only death-year is
    known and exceeds the maximum plausible lifespan).
    """
    if ms_year is None:
        return None
    if person_birth_year is not None and person_birth_year > ms_year + DATE_BIRTH_BUFFER_YEARS:
        return (
            f"date-conflict: person born {person_birth_year}, "
            f"MS dated {ms_year} (cannot be MS author)"
        )
    role_l = (role or "").lower()
    if role_l in PHYSICAL_PRODUCTION_ROLES and person_death_year is not None:
        gap = ms_year - person_death_year
        if gap > DATE_DEATH_AFTERLIFE_YEARS:
            return (
                f"date-conflict: scribe/copyist died {person_death_year}, "
                f"MS dated {ms_year} (>{DATE_DEATH_AFTERLIFE_YEARS}y gap)"
            )
    # Posthumous-by-too-much: implausible lifespan even with birth unknown.
    if (
        person_death_year is not None
        and person_birth_year is None
        and (person_death_year - ms_year) > DATE_DEATH_POSTHUMOUS_YEARS
    ):
        return (
            f"date-conflict: person died {person_death_year} "
            f"({person_death_year - ms_year}y after MS dated {ms_year}); "
            "implausible lifespan implies birth after MS"
        )
    return None


# ── Guard 2: short-name homonym guard ────────────────────────────────


def is_short_name_homonym(
    marc_name: str,
    preferred_name_lat: str | None,
    *,
    mazal_matched: bool,
    biographical_dates_present: bool,
) -> bool:
    """True when a 1-token MARC name landed on a richly-named VIAF cluster.

    Single-token Hebrew names (``יעקב``, ``Isaac``) routinely match
    several historic figures. VIAF SRU returns the highest-ranked
    cluster, which is usually the wrong one when the source has no
    disambiguator. We accept the match only when an independent
    signal (Mazal hit, MARC 100$d biographical dates) corroborates.
    """
    if not preferred_name_lat:
        return False
    marc_tokens = _tokenise(marc_name)
    if len(marc_tokens) != 1:
        return False
    candidate_tokens = _tokenise(preferred_name_lat)
    if len(candidate_tokens) <= 2:
        return False
    if mazal_matched or biographical_dates_present:
        return False
    return True


# ── Guard 5: confidence scoring ──────────────────────────────────────


def score_confidence(
    *,
    has_mazal: bool,
    has_viaf: bool,
    has_preferred_name_lat: bool,
    date_conflict_reason: str | None,
    short_name_homonym: bool,
    cluster_collapsed: bool = False,
    wikidata_disagrees: bool = False,
    wikidata_confirms: bool = False,
    over_merge_detected: bool = False,
    has_wikidata: bool = False,
    cross_source_conflict: bool = False,
) -> str:
    """Return ``"high"``, ``"medium"``, or ``"low"``.

    Base ladder (deterministic 5-guard layer, 4-source aware):

    The number of agreeing identifier sources is ``sources = sum([
    has_mazal, has_viaf, has_wikidata])`` (0..3 for persons; KIMA is
    place-specific and handled by :func:`score_place_confidence`).

    high   — at least 2 sources agree AND a Latin preferred name is
             present (cross-script verification).
    medium — exactly one source matched, or two without Latin form.
    low    — any guard raised a concern, OR cluster collapsed, OR a
             ``cross_source_conflict`` was flagged by an upstream
             reconciler (e.g. Mazal NLI ID clashes with the Wikidata
             P8189 on the same VIAF cluster).

    Backwards compatibility: when ``has_wikidata=False`` and
    ``cross_source_conflict=False`` (the defaults), the ``sources``
    count collapses to ``has_mazal + has_viaf`` and the ladder
    reproduces the previous 2-source behaviour exactly — the existing
    truth-table tests pass without modification.

    Cross-source flags (F2/F3 Wikidata cross-check):

    * ``cross_source_conflict=True`` → forced ``low`` (sticky). Set by
      reconcilers that detect identifier clashes across the four
      authorities; takes precedence over agreement signals.
    * ``over_merge_detected=True`` → forced ``low`` (sticky).
    * ``wikidata_disagrees=True`` → demote one rung: ``high → medium``,
      ``medium → low``. Cannot promote.
    * ``wikidata_confirms=True`` → promote ``medium → high``. Never
      moves ``low`` upward (``low`` is sticky).

    Sticky-low rule: once the deterministic guards have produced
    ``low`` (or once an over-merge / cross-source conflict is
    detected), no positive flag can promote it. This is the
    precision-preserving invariant.

    Note: ``llm_disagrees`` / ``llm_confirms`` (F1) were removed
    2026-05-03 — see DRIFT_LOG type 14. The 13GB DictaLM weight
    bundle was incompatible with the desktop-app distribution model.
    """
    # ── Hard rejections: cross-source conflict is sticky-low ─────────
    if cross_source_conflict:
        return "low"

    # ── Base score from the deterministic guards ─────────────────────
    if date_conflict_reason or short_name_homonym or cluster_collapsed:
        base = "low"
    else:
        sources = sum([has_mazal, has_viaf, has_wikidata])
        if sources >= 2 and has_preferred_name_lat:
            base = "high"
        elif sources >= 1:
            base = "medium"
        else:
            base = "low"

    # ── Over-merge override → force low (sticky on top of everything)
    if over_merge_detected:
        return "low"

    # ── Sticky low: never promote upward from low ────────────────────
    if base == "low":
        return "low"

    # ── Apply demotions first (a single disagreement is enough) ──────
    if wikidata_disagrees:
        if base == "high":
            return "medium"
        # base == "medium" → demote to low
        return "low"

    # ── Apply promotions from medium → high when corroborated ────────
    if base == "medium" and wikidata_confirms:
        return "high"

    return base


# ── Place confidence (KIMA + Wikidata + optional Mazal) ──────────────


def score_place_confidence(
    *,
    has_kima: bool,
    has_wikidata: bool,
    has_mazal: bool = False,
) -> str:
    """Confidence ladder for place matches (KIMA + Wikidata + optional Mazal).

    KIMA returns Wikidata URIs natively, so KIMA agreement with a
    Wikidata match is largely a self-check; the value of running both
    is detecting rare KIMA-stale-pointer cases (KIMA still references
    a Wikidata QID that has been merged or deleted).

    Ladder:

    high    — at least 2 of {KIMA, Wikidata, Mazal} matched.
    medium  — exactly 1 source matched.
    low     — no source matched.
    """
    sources = sum([has_kima, has_wikidata, has_mazal])
    if sources >= 2:
        return "high"
    if sources == 1:
        return "medium"
    return "low"


# ── Main public entrypoint ───────────────────────────────────────────


def evaluate_match(
    *,
    marc_name: str,
    role: str,
    ms_year: int | None,
    mazal_id: str | None,
    viaf_uri: str | None,
    preferred_name_lat: str | None = None,
    person_birth_year: int | None = None,
    person_death_year: int | None = None,
    biographical_dates_in_marc: bool = False,
    wikidata_disagrees: bool = False,
    wikidata_confirms: bool = False,
    over_merge_detected: bool = False,
    has_wikidata: bool = False,
    cross_source_conflict: bool = False,
    wikidata_qid: str | None = None,
) -> dict[str, Any]:
    """Run guards 1, 2, 4, 5 on a single match and return a verdict.

    Guard 3 (cluster collapse) operates over the *list* of matches
    in a record and is applied separately by :func:`apply_cluster_collapse`.

    Returns a dict::

        {
            "confidence": "high" | "medium" | "low",
            "matched": 0 | 1,                # backwards-compat
            "mazal_id": str | None,          # cleared if rejected
            "viaf_uri": str | None,          # cleared if rejected
            "wikidata_qid": str | None,      # surfaced 4-source authority
            "rejection_reason": str | None,  # populated when a hard guard fires
            "guard_flags": list[str],        # diagnostic, e.g. ["short_name_homonym"]
        }
    """
    flags: list[str] = []
    rejection: str | None = None
    out_mazal = mazal_id
    out_viaf = viaf_uri

    # Guard 4 — placeholder name (hard reject)
    if is_placeholder_name(marc_name):
        flags.append("placeholder_name")
        rejection = "placeholder_name"
        out_mazal = None
        out_viaf = None
        return {
            "confidence": "low",
            "matched": 0,
            "mazal_id": None,
            "viaf_uri": None,
            "wikidata_qid": wikidata_qid,
            "rejection_reason": rejection,
            "guard_flags": flags,
        }

    # Guard 1 — date conflict (hard reject when dates are present)
    date_reason = evaluate_date_conflict(
        role=role,
        ms_year=ms_year,
        person_birth_year=person_birth_year,
        person_death_year=person_death_year,
    )
    if date_reason:
        flags.append("date_conflict")
        rejection = date_reason
        out_viaf = None  # date came from VIAF cluster; clear it
        # Mazal hit may still be valid (different person) — but to be
        # conservative we degrade confidence rather than re-attaching it.

    # Guard 2 — short-name homonym (soft: degrade to low, keep IDs)
    short_homonym = is_short_name_homonym(
        marc_name=marc_name,
        preferred_name_lat=preferred_name_lat,
        mazal_matched=bool(out_mazal),
        biographical_dates_present=biographical_dates_in_marc,
    )
    if short_homonym:
        flags.append("short_name_homonym")

    if over_merge_detected:
        flags.append("over_merge_detected")
    if wikidata_disagrees:
        flags.append("wikidata_disagrees")
    if wikidata_confirms:
        flags.append("wikidata_confirms")
    if has_wikidata:
        flags.append("has_wikidata")
    if cross_source_conflict:
        flags.append("cross_source_conflict")

    confidence = score_confidence(
        has_mazal=bool(out_mazal),
        has_viaf=bool(out_viaf),
        has_preferred_name_lat=bool(preferred_name_lat),
        date_conflict_reason=date_reason,
        short_name_homonym=short_homonym,
        wikidata_disagrees=wikidata_disagrees,
        wikidata_confirms=wikidata_confirms,
        over_merge_detected=over_merge_detected,
        has_wikidata=has_wikidata,
        cross_source_conflict=cross_source_conflict,
    )

    if confidence == "low" and over_merge_detected:
        # Cluster collapse / over-merge → drop the cluster-derived IDs
        # for safety. Keep Mazal (it's per-person authoritative) so the
        # GUI's manual review still has something to anchor on.
        out_viaf = None

    return {
        "confidence": confidence,
        "matched": 1 if confidence == "high" else 0,
        "mazal_id": out_mazal,
        "viaf_uri": out_viaf,
        "wikidata_qid": wikidata_qid,
        "rejection_reason": rejection,
        "guard_flags": flags,
    }


# ── Guard 3: cluster-collapse detector ───────────────────────────────


def apply_cluster_collapse(matches: list[dict[str, Any]]) -> int:
    """Detect and downgrade VIAF cluster collapses in a single record.

    When two *distinct* MARC name strings in one record both resolve
    to the same ``viaf_uri``, demote BOTH matches to confidence
    ``low`` (don't drop them — manual review may approve one). Sets
    ``guard_flags`` += ``["cluster_collapse"]`` and ``matched=0`` on
    affected rows.

    Returns the number of matches downgraded.
    """
    by_viaf: dict[str, list[int]] = {}
    for idx, m in enumerate(matches):
        uri = m.get("viaf_uri")
        if not uri:
            continue
        name = _clean_name(str(m.get("name") or ""))
        # Bucket entries by VIAF URI but ignore identical name strings
        # (they're the same person mentioned twice — common, and not a
        # collapse).
        bucket = by_viaf.setdefault(uri, [])
        existing_names = {_clean_name(str(matches[j].get("name") or "")) for j in bucket}
        if name not in existing_names:
            bucket.append(idx)

    downgraded = 0
    for uri, indices in by_viaf.items():
        if len(indices) < 2:
            continue
        for idx in indices:
            m = matches[idx]
            if m.get("confidence") == "low":
                continue  # already low — count once
            m["confidence"] = "low"
            m["matched"] = 0
            flags = list(m.get("guard_flags") or [])
            if "cluster_collapse" not in flags:
                flags.append("cluster_collapse")
            m["guard_flags"] = flags
            downgraded += 1
    return downgraded
