"""Wikidata SPARQL cross-check + over-merge detection (F2 / F3 of Plan 2026-04-30).

Background
==========

Stage 3's existing guards (``stage3_guards.py``) reject 22 of the 22 false
positives from the 2026-04-30 review by reasoning over MARC-side signals
only — manuscript date, Mazal+VIAF agreement, name shape. Three rejects
cannot be defended that way: VIAF SRU returned a *single* cluster URI, but
that cluster turned out to be an over-merge in VIAF's own database (two
distinct historical persons collapsed into one VIAF authority record).
The reviewer detected those by hand via a Wikidata SPARQL lookup — the
Wikidata community routinely splits VIAF over-merges, and the resulting
multiple ``wdt:P214`` matches on the same VIAF ID are a high-precision
signal that the cluster is suspect.

This module implements two complementary signals.

* **F2 — Wikidata cross-check (``lookup_viaf`` + ``is_overmerged``).**
  Query the Wikidata Query Service for every Wikidata item carrying
  ``wdt:P214 = <viaf_id>``. If two or more items come back AND they
  disagree on birth/death year or occupation, treat the VIAF cluster
  as over-merged. Pure cardinality (≥2 items) is *not* enough — Wikidata
  also has alternate "version" items (e.g., a person plus their
  pseudonym) that share a VIAF ID legitimately.

* **F3 — Mazal-pair collision (``OverMergeTable.detect_pair_collision``).**
  Within a single Stage-3 run, if two distinct MARC names with two
  distinct Mazal IDs both resolved to the same VIAF cluster, that
  cluster is over-merged on the VIAF side regardless of what Wikidata
  thinks. This is the strongest signal — Mazal/NLI is a curated,
  per-person authority — but only fires when both candidates were also
  matched in Mazal.

The module is **pure** (no Qt, no workers wiring) so unit tests can
exercise it directly. ``AuthorityWorker`` will own a singleton
``OverMergeTable`` for the run and consult ``lookup_viaf`` /
``is_overmerged`` from inside ``_match_marc_person_entry`` — that wiring
is Agent D's responsibility (this module exposes the surface only).

Disable
=======

Set ``MHM_DISABLE_WIKIDATA_CROSSCHECK=1`` in the environment to skip
all SPARQL calls (useful for offline runs or when the WDQS endpoint is
flaky). ``is_enabled()`` returns ``False`` and callers must short-circuit.

Cache
=====

Results are cached at ``~/.cache/mhm-pipeline/wikidata_viaf.json`` keyed
by VIAF ID. TTL is 30 days. Override the cache root with
``MHM_AUTHORITY_CACHE_DIR``. A corrupt cache file is logged, treated as
a miss, and rewritten — never propagated as an exception.

References
==========

* SWJ 2026-04-30 review: ``/Users/alexandergo/Desktop/test_subset/authority_review_report.md``
* CLAUDE.md Rule 22 (VIAF cluster harvesting), Rule 23 (cross-identifier
  verification), Rule 29 (VIAF nameType cross-validation).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import requests

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────

WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "MHM-Pipeline/1.0 (alexandergo@wix.com)"
HTTP_TIMEOUT = 12  # seconds per request
CACHE_TTL_SECONDS = 30 * 86400  # 30 days
MIN_REQUEST_INTERVAL_SECONDS = 1.0  # session-wide throttle: 1 req/s
MAX_RETRIES = 3
BACKOFF_SCHEDULE_SECONDS = (1.0, 2.0, 4.0)  # 1st, 2nd, 3rd attempt

DISABLE_ENV_VAR = "MHM_DISABLE_WIKIDATA_CROSSCHECK"
CACHE_DIR_ENV_VAR = "MHM_AUTHORITY_CACHE_DIR"

# Cantillation marks (te'amim) range used in the Tanakh.
_CANTILLATION_RANGE = (0x0591, 0x05AF)

_SPARQL_QUERY_TEMPLATE = (
    'SELECT ?item '
    '(GROUP_CONCAT(DISTINCT ?label; separator="\\t") AS ?labels) '
    '(SAMPLE(?birth) AS ?b) '
    '(SAMPLE(?death) AS ?d) '
    '(GROUP_CONCAT(DISTINCT ?occ; separator="\\t") AS ?occs) '
    "WHERE { "
    '?item wdt:P214 "{viaf_id}" . '
    'OPTIONAL { ?item rdfs:label ?label FILTER(LANG(?label)="he") } '
    "OPTIONAL { ?item wdt:P569 ?birth } "
    "OPTIONAL { ?item wdt:P570 ?death } "
    "OPTIONAL { ?item wdt:P106 ?occ } "
    "} GROUP BY ?item"
)


# ── Public dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class WikidataResult:
    """One SPARQL probe of a VIAF ID against Wikidata.

    All collection fields are tuples (frozen) so the dataclass is hashable
    and safe to ferry across threads / cache layers without defensive copies.
    """

    viaf_id: str
    qids: tuple[str, ...]
    hebrew_labels: tuple[str, ...]
    birth_years: tuple[int, ...]
    death_years: tuple[int, ...]
    occupations: tuple[str, ...]
    fetched_at: float
    error: str | None


# ── Module-level rate limiter (token-bucket) ──────────────────────────


_rate_lock = threading.Lock()
_last_request_at: float = 0.0


def _throttle() -> None:
    """Block until the global 1 req/s budget allows another call."""
    global _last_request_at
    with _rate_lock:
        now = time.monotonic()
        wait = MIN_REQUEST_INTERVAL_SECONDS - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _reset_throttle_for_tests() -> None:
    """Reset the rate-limit clock between tests so they don't pay sleeps."""
    global _last_request_at
    with _rate_lock:
        _last_request_at = 0.0


# ── Enablement ────────────────────────────────────────────────────────


def is_enabled() -> bool:
    """False when the user has opted out OR ``requests`` is unavailable.

    The ``requests`` import at module top is required, but we still gate
    on ``ImportError`` defensively so a broken environment yields a
    silent skip rather than a Stage-3 crash.
    """
    if os.environ.get(DISABLE_ENV_VAR) == "1":
        return False
    try:
        import requests as _r  # noqa: F401, PLC0415
    except ImportError:  # pragma: no cover - defensive
        return False
    return True


# ── Cache ─────────────────────────────────────────────────────────────


def _cache_path() -> Path:
    """Resolve the on-disk cache file path, honouring the override env var."""
    override = os.environ.get(CACHE_DIR_ENV_VAR)
    base = Path(override) if override else Path.home() / ".cache" / "mhm-pipeline"
    base.mkdir(parents=True, exist_ok=True)
    return base / "wikidata_viaf.json"


def _load_cache() -> dict[str, dict[str, object]]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Wikidata cross-check cache corrupt at %s (%s); ignoring.", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Wikidata cross-check cache shape unexpected at %s; ignoring.", path)
        return {}
    return data


def _save_cache(cache: dict[str, dict[str, object]]) -> None:
    path = _cache_path()
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem-level failure
        logger.warning("Wikidata cross-check cache write failed at %s (%s).", path, exc)


def _result_to_cache(result: WikidataResult) -> dict[str, object]:
    return {
        "viaf_id": result.viaf_id,
        "qids": list(result.qids),
        "hebrew_labels": list(result.hebrew_labels),
        "birth_years": list(result.birth_years),
        "death_years": list(result.death_years),
        "occupations": list(result.occupations),
        "fetched_at": result.fetched_at,
        "error": result.error,
    }


def _cache_to_result(payload: dict[str, object]) -> WikidataResult | None:
    try:
        return WikidataResult(
            viaf_id=str(payload["viaf_id"]),
            qids=tuple(str(q) for q in (payload.get("qids") or [])),
            hebrew_labels=tuple(str(s) for s in (payload.get("hebrew_labels") or [])),
            birth_years=tuple(int(y) for y in (payload.get("birth_years") or [])),
            death_years=tuple(int(y) for y in (payload.get("death_years") or [])),
            occupations=tuple(str(s) for s in (payload.get("occupations") or [])),
            fetched_at=float(payload.get("fetched_at") or 0.0),
            error=str(payload["error"]) if payload.get("error") else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


# ── SPARQL fetcher ────────────────────────────────────────────────────


def _build_query(viaf_id: str) -> str:
    safe = viaf_id.replace('"', "")
    return _SPARQL_QUERY_TEMPLATE.replace("{viaf_id}", safe)


def _now() -> float:
    """Wall-clock seconds. Wrapped so tests can monkeypatch."""
    return time.time()


def _parse_sparql_year(raw: str | None) -> int | None:
    if not raw:
        return None
    match = re.search(r"-?\d{1,4}", raw)
    if not match:
        return None
    try:
        year = int(match.group(0))
    except ValueError:
        return None
    return year if -3000 < year < 3000 else None


def _parse_sparql_response(viaf_id: str, payload: dict[str, object]) -> WikidataResult:
    bindings_field = payload.get("results", {})
    bindings: list[dict[str, dict[str, str]]] = []
    if isinstance(bindings_field, dict):
        candidate = bindings_field.get("bindings")
        if isinstance(candidate, list):
            bindings = candidate  # type: ignore[assignment]

    qids: list[str] = []
    labels: set[str] = set()
    births: set[int] = set()
    deaths: set[int] = set()
    occupations: set[str] = set()
    for row in bindings:
        if not isinstance(row, dict):
            continue
        item_field = row.get("item", {})
        item_uri = item_field.get("value", "") if isinstance(item_field, dict) else ""
        if not item_uri:
            continue
        qid_match = re.search(r"(Q\d+)$", item_uri)
        if not qid_match:
            continue
        qid = qid_match.group(1)
        if qid not in qids:
            qids.append(qid)

        label_field = row.get("labels", {})
        label_value = label_field.get("value", "") if isinstance(label_field, dict) else ""
        if isinstance(label_value, str) and label_value:
            for piece in label_value.split("\t"):
                piece = piece.strip()
                if piece:
                    labels.add(piece)

        b_field = row.get("b", {})
        b_value = b_field.get("value", "") if isinstance(b_field, dict) else ""
        b_year = _parse_sparql_year(b_value)
        if b_year is not None:
            births.add(b_year)

        d_field = row.get("d", {})
        d_value = d_field.get("value", "") if isinstance(d_field, dict) else ""
        d_year = _parse_sparql_year(d_value)
        if d_year is not None:
            deaths.add(d_year)

        occ_field = row.get("occs", {})
        occ_value = occ_field.get("value", "") if isinstance(occ_field, dict) else ""
        if isinstance(occ_value, str) and occ_value:
            for piece in occ_value.split("\t"):
                piece = piece.strip()
                if piece:
                    occupations.add(piece)

    return WikidataResult(
        viaf_id=viaf_id,
        qids=tuple(qids),
        hebrew_labels=tuple(sorted(labels)),
        birth_years=tuple(sorted(births)),
        death_years=tuple(sorted(deaths)),
        occupations=tuple(sorted(occupations)),
        fetched_at=_now(),
        error=None,
    )


_session: requests.Session | None = None
_session_lock = threading.Lock()


def _get_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            sess = requests.Session()
            sess.headers["Accept"] = "application/sparql-results+json"
            sess.headers["User-Agent"] = USER_AGENT
            _session = sess
        return _session


def _http_fetch(viaf_id: str) -> WikidataResult:
    """Run the SPARQL query for *viaf_id* with bounded retry. Always returns
    a ``WikidataResult`` — populated ``.error`` indicates final failure."""
    query = _build_query(viaf_id)
    session = _get_session()
    last_exc: str | None = None

    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            resp = session.get(
                WDQS_ENDPOINT,
                params={"query": query, "format": "json"},
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_exc = f"{type(exc).__name__}: {exc}"
            logger.debug("Wikidata cross-check transport error %s for %s", last_exc, viaf_id)
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_SCHEDULE_SECONDS[attempt])
            continue

        status = resp.status_code
        if status == 200:
            try:
                payload = resp.json()
            except ValueError as exc:
                last_exc = f"InvalidJSON: {exc}"
                logger.debug("Wikidata cross-check non-JSON 200 for %s", viaf_id)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_SCHEDULE_SECONDS[attempt])
                continue
            if not isinstance(payload, dict):
                last_exc = "InvalidJSON: top-level not a dict"
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_SCHEDULE_SECONDS[attempt])
                continue
            return _parse_sparql_response(viaf_id, payload)

        # 429 / 5xx → retry with backoff. Other 4xx → bail.
        last_exc = f"HTTP {status}"
        if status == 429 or 500 <= status < 600:
            logger.debug(
                "Wikidata cross-check %s for %s (attempt %d/%d)",
                last_exc,
                viaf_id,
                attempt + 1,
                MAX_RETRIES,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_SCHEDULE_SECONDS[attempt])
            continue
        # Permanent client error
        break

    return WikidataResult(
        viaf_id=viaf_id,
        qids=(),
        hebrew_labels=(),
        birth_years=(),
        death_years=(),
        occupations=(),
        fetched_at=_now(),
        error=last_exc or "unknown",
    )


# ── Public lookup ─────────────────────────────────────────────────────


_lookup_lock = threading.Lock()


def lookup_viaf(viaf_id: str) -> WikidataResult:
    """Return the Wikidata snapshot for *viaf_id*, going through the
    on-disk 30-day cache. Never raises — failures show up as
    ``WikidataResult(... error=<str>)`` so callers can degrade silently."""
    if not viaf_id:
        return WikidataResult(
            viaf_id=viaf_id,
            qids=(),
            hebrew_labels=(),
            birth_years=(),
            death_years=(),
            occupations=(),
            fetched_at=_now(),
            error="empty viaf_id",
        )

    with _lookup_lock:
        cache = _load_cache()
        cached = cache.get(viaf_id)
        if isinstance(cached, dict):
            cached_result = _cache_to_result(cached)
            if cached_result is not None:
                age = _now() - cached_result.fetched_at
                if age < CACHE_TTL_SECONDS:
                    return cached_result

        result = _http_fetch(viaf_id)
        # Successful results AND error results both get cached: this stops
        # us from hammering a flaky endpoint on every Stage-3 record.
        cache[viaf_id] = _result_to_cache(result)
        _save_cache(cache)
        return result


# ── Hebrew label matcher ──────────────────────────────────────────────


def strip_hebrew_diacritics(text: str) -> str:
    """Drop nikud + cantillation; normalise unicode; collapse whitespace.

    Public since 2026-04-30 (Agent A promotion). The leading-underscore
    alias :data:`_strip_hebrew_diacritics` is kept for any caller that
    imported the private name during the F2/F3 rollout.
    """
    normalised = unicodedata.normalize("NFKD", text or "")
    out_chars: list[str] = []
    for ch in normalised:
        cp = ord(ch)
        # Combining nikud (0x05B0–0x05C7 covers all vowel points + dagesh
        # forms) and cantillation marks (0x0591–0x05AF).
        if 0x0591 <= cp <= 0x05C7:
            continue
        # Other combining diacritics (Latin nikud-equivalents).
        if unicodedata.combining(ch):
            continue
        out_chars.append(ch)
    cleaned = "".join(out_chars)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# Backward-compat alias — keep the private name green for any caller
# that imported it before the 2026-04-30 promotion.
_strip_hebrew_diacritics = strip_hebrew_diacritics


def _levenshtein(a: str, b: str) -> int:
    """Pure-Python edit distance. O(len(a) * len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            current[j] = min(
                previous[j] + 1,        # deletion
                current[j - 1] + 1,     # insertion
                previous[j - 1] + cost  # substitution
            )
        previous = current
    return previous[-1]


def hebrew_label_matches(
    marc_name: str,
    labels: Sequence[str],
    *,
    max_distance: int = 2,
) -> bool:
    """Levenshtein-tolerant Hebrew name comparison.

    Both sides are normalised (nikud + cantillation stripped, whitespace
    collapsed) before the distance check. Returns True iff at least one
    label is within *max_distance* edits of the MARC name.
    """
    if not marc_name or not labels:
        return False
    needle = strip_hebrew_diacritics(marc_name)
    if not needle:
        return False
    for label in labels:
        if not label:
            continue
        haystack = strip_hebrew_diacritics(str(label))
        if not haystack:
            continue
        if _levenshtein(needle, haystack) <= max_distance:
            return True
    return False


# ── Over-merge detector ───────────────────────────────────────────────


def is_overmerged(result: WikidataResult) -> bool:
    """Apply the bounded over-merge rule from the Plan.

    True iff Wikidata returns ≥2 distinct items for this VIAF ID AND at
    least one disagreement signal fires:

    * ≥2 distinct ``birth_years``, OR
    * ≥2 distinct ``death_years``, OR
    * ≥2 distinct ``occupations`` URIs.

    Pure cardinality is intentionally NOT enough — Wikidata legitimately
    keeps multiple items per VIAF ID for redirected/sub-aspect records.
    Disagreement on a biographical fact is the precision-preserving
    upgrade.
    """
    if len(result.qids) < 2:
        return False
    if len(set(result.birth_years)) >= 2:
        return True
    if len(set(result.death_years)) >= 2:
        return True
    if len(set(result.occupations)) >= 2:
        return True
    return False


# ── F3 — Mazal-pair collision table ───────────────────────────────────


@dataclass
class _MazalPairEntry:
    marc_names: set[str] = field(default_factory=set)
    mazal_ids: set[str] = field(default_factory=set)


class OverMergeTable:
    """Per-Stage-3-run aggregator.

    AuthorityWorker creates one instance for the run, calls
    :meth:`record_mazal_pair` after every successful joint Mazal+VIAF
    match, then asks :meth:`detect_pair_collision` for the set of
    VIAF IDs that gathered two distinct (marc_name, mazal_id) pairs.
    Those VIAFs are forced to ``low`` confidence in the GUI.

    The table also caches Wikidata lookups via :meth:`get` so each
    VIAF ID is fetched at most once per run, even if the cluster
    appears on multiple records.
    """

    def __init__(self) -> None:
        self._wikidata: dict[str, WikidataResult] = {}
        self._pairs: dict[str, _MazalPairEntry] = {}

    def get(self, viaf_id: str) -> WikidataResult:
        """Cache-aware lookup of *viaf_id*. Calls :func:`lookup_viaf` on
        first sight; later calls return the in-memory copy."""
        cached = self._wikidata.get(viaf_id)
        if cached is not None:
            return cached
        result = lookup_viaf(viaf_id)
        self._wikidata[viaf_id] = result
        return result

    def record_mazal_pair(
        self, marc_name: str, mazal_id: str, viaf_id: str
    ) -> None:
        """Record that (*marc_name*, *mazal_id*) was matched against *viaf_id*.

        Empty inputs are ignored — the F3 signal only fires when both
        Mazal and VIAF resolved on both sides."""
        if not marc_name or not mazal_id or not viaf_id:
            return
        entry = self._pairs.setdefault(viaf_id, _MazalPairEntry())
        entry.marc_names.add(marc_name.strip())
        entry.mazal_ids.add(mazal_id.strip())

    def detect_pair_collision(self) -> set[str]:
        """Return the set of VIAF IDs where two distinct MARC names AND
        two distinct Mazal IDs were recorded. That combination is the
        Plan's F3 signal: only Mazal can prove that two MARC strings
        denote different real people, so two Mazal IDs sharing one
        VIAF cluster is *the* over-merge signature."""
        out: set[str] = set()
        for viaf_id, entry in self._pairs.items():
            if len(entry.marc_names) >= 2 and len(entry.mazal_ids) >= 2:
                out.add(viaf_id)
        return out

    def clear(self) -> None:
        """Forget all per-run state. Called by AuthorityWorker between runs."""
        self._wikidata.clear()
        self._pairs.clear()


__all__ = [
    "OverMergeTable",
    "WikidataResult",
    "hebrew_label_matches",
    "is_enabled",
    "is_overmerged",
    "lookup_viaf",
    "strip_hebrew_diacritics",
]
