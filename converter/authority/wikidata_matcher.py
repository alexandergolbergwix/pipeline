"""Wikidata SPARQL primary-source matcher (Agent A — 4-source authority team).

Promoted from the F2/F3 cross-check module on 2026-04-30. While
``wikidata_crosscheck`` looks up VIAF IDs to detect over-merges, this
module treats Wikidata as a *primary* authority source and tries to
resolve a MARC name (or a known foreign identifier) directly to a
QID.

Strategy — three modes per call, in priority order:

1. **Identifier triangulation.** When the caller already knows a VIAF
   or Mazal/J9U id (helper methods :meth:`find_qid_by_viaf` /
   :meth:`find_qid_by_mazal`) we run a one-line SPARQL probe against
   ``wdt:P214`` / ``wdt:P8189``. One row → trusted QID. Two-or-more
   rows → abstain (return ``None``); the F3 over-merge path in
   :mod:`converter.authority.wikidata_crosscheck` will surface that as
   a low-confidence signal.

2. **Hebrew label search.** ``rdfs:label`` / ``skos:altLabel`` /
   ``wdt:P1559`` (native name) are queried with the diacritic-stripped
   MARC name in Hebrew. Returned candidates pass through a
   ``P31/P279*`` type filter against the expected class set
   (person / place / corporate / work) and a Levenshtein-≤-1 check on
   the diacritic-stripped form.

3. **Latin transliteration fallback.** Same pattern but ``@en`` —
   only consulted when Mode 2 yielded no candidate. Internally tagged
   so the worker can choose to skip auto-promotion of Mode-3 hits.

Cache + throttle + errors:

* On-disk JSON cache at ``~/.cache/mhm-pipeline/wikidata_authority.json``
  (a SEPARATE file from ``wikidata_viaf.json`` so a TTL invalidation on
  one does not cascade to the other), 30-day TTL.
* Throttle — re-uses ``wikidata_crosscheck._throttle`` so this module
  and the over-merge detector cooperate on a single 1 req/s WDQS
  budget.
* Errors — 5xx / 429 retried with backoff (1 s, 2 s, 4 s); permanent
  failure returns ``None``. **Never raises.**
* Disable — same env var as the crosscheck module
  (``MHM_DISABLE_WIKIDATA_CROSSCHECK=1``); one switch governs both.

References
==========

* Plan 2026-04-30 (4-source authority team).
* CLAUDE.md Rule 11 (cluster-fetch JSON contract — for parity).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable

from .wikidata_crosscheck import (
    BACKOFF_SCHEDULE_SECONDS,
    CACHE_DIR_ENV_VAR,
    CACHE_TTL_SECONDS,
    HTTP_TIMEOUT,
    MAX_RETRIES,
    USER_AGENT,
    WDQS_ENDPOINT,
    _throttle,
    is_enabled,
    strip_hebrew_diacritics,
)

logger = logging.getLogger(__name__)


# ── Type-class catalogues (for P31/P279* filter) ──────────────────────

# Person-shaped classes: human, fictional human, group of humans,
# religious figure (rabbi).
_PERSON_TYPES: tuple[str, ...] = ("Q5", "Q15632617", "Q3863", "Q4271324")

# Place-shaped classes: human settlement, city, town, geographic location.
_PLACE_TYPES: tuple[str, ...] = ("Q486972", "Q515", "Q11369", "Q1549591")

# Corporate-shaped classes: organization, business, public institution.
_CORPORATE_TYPES: tuple[str, ...] = ("Q43229", "Q4830453", "Q22687")

# Work-shaped classes: literary work, work, written work.
_WORK_TYPES: tuple[str, ...] = ("Q47461344", "Q571", "Q49848")


# ── Module-level cache / session ──────────────────────────────────────

_CACHE_FILENAME = "wikidata_authority.json"
_cache_lock = threading.Lock()
_session_lock = threading.Lock()
_session: Any | None = None  # requests.Session — lazy-imported


def _now() -> float:
    """Wall-clock seconds. Wrapped so tests can monkey-patch."""
    import time  # noqa: PLC0415

    return time.time()


def _cache_path(override: Path | None = None) -> Path:
    """Resolve the on-disk cache file path.

    Honours, in order: an explicit *override* parameter, the
    ``MHM_AUTHORITY_CACHE_DIR`` env var, then ``~/.cache/mhm-pipeline``.
    """
    if override is not None:
        override.parent.mkdir(parents=True, exist_ok=True)
        return override
    env_dir = os.environ.get(CACHE_DIR_ENV_VAR)
    base = Path(env_dir) if env_dir else Path.home() / ".cache" / "mhm-pipeline"
    base.mkdir(parents=True, exist_ok=True)
    return base / _CACHE_FILENAME


def _load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Wikidata authority cache corrupt at %s (%s); ignoring.", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem-level failure
        logger.warning("Wikidata authority cache write failed at %s (%s).", path, exc)


def _get_session() -> Any:
    """Lazy-import ``requests`` and reuse a single ``Session``."""
    global _session
    import requests  # noqa: PLC0415

    with _session_lock:
        if _session is None:
            sess = requests.Session()
            sess.headers["Accept"] = "application/sparql-results+json"
            sess.headers["User-Agent"] = USER_AGENT
            _session = sess
        return _session


def _reset_session_for_tests() -> None:
    """Drop the cached session so ``patch.object(Session, 'get')`` rebinds cleanly."""
    global _session
    with _session_lock:
        _session = None


# ── SPARQL helpers ────────────────────────────────────────────────────


def _escape_literal(value: str) -> str:
    """Quote a SPARQL string literal safely. Strips any embedded double-quote."""
    return value.replace("\\", "\\\\").replace('"', "")


def _qid_from_uri(uri: str) -> str | None:
    match = re.search(r"(Q\d+)$", uri)
    return match.group(1) if match else None


def _qid_sort_key(qid: str) -> int:
    """Numeric portion of a QID, for sorting canonical-first.

    Lower QIDs (Q189564) are almost always older / more canonical than
    higher ones (Q139094451 — created recently by this pipeline). When
    SPARQL returns several candidates that all match a Hebrew label we
    pick the smallest-numbered one.
    """
    try:
        return int(qid[1:])
    except (ValueError, IndexError):
        return 10**12


def _values_clause(types: Iterable[str]) -> str:
    return " ".join(f"wd:{q}" for q in types)


def _build_identifier_query(property_id: str, value: str) -> str:
    return f'SELECT ?p WHERE {{ ?p wdt:{property_id} "{_escape_literal(value)}" }} LIMIT 2'


def _build_label_query(name: str, types: Iterable[str], lang: str) -> str:
    safe = _escape_literal(name)
    values = _values_clause(types)
    # LIMIT 10 + return all candidates; the caller scores by QID number
    # (lower = older = more canonical) so we prefer Q189564 (Rashi) over
    # Q139094451 (a pipeline-created duplicate). LIMIT 2 was too tight —
    # SPARQL's arbitrary order let our duplicates win.
    return (
        "SELECT DISTINCT ?p WHERE { "
        "{ ?p wdt:P1559 \"" + safe + "\"@" + lang + " } "
        "UNION { ?p rdfs:label \"" + safe + "\"@" + lang + " } "
        "UNION { ?p skos:altLabel \"" + safe + "\"@" + lang + " } "
        "?p wdt:P31/wdt:P279* ?type . "
        "VALUES ?type { " + values + " } "
        "} LIMIT 10"
    )


def _build_label_only_query(qid: str, lang: str) -> str:
    return (
        f"SELECT ?label WHERE {{ wd:{qid} rdfs:label ?label . "
        f"FILTER(LANG(?label)=\"{lang}\") }} LIMIT 5"
    )


def _build_type_check_query(qid: str, types: Iterable[str]) -> str:
    values = _values_clause(types)
    return (
        f"ASK {{ wd:{qid} wdt:P31/wdt:P279* ?type . "
        f"VALUES ?type {{ {values} }} }}"
    )


# ── HTTP fetch with retry ─────────────────────────────────────────────


def _http_sparql(query: str) -> dict[str, Any] | None:
    """Run *query* against WDQS with bounded retry. ``None`` on permanent failure."""
    import requests  # noqa: PLC0415

    session = _get_session()
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            resp = session.get(
                WDQS_ENDPOINT,
                params={"query": query, "format": "json"},
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.debug("Wikidata matcher transport error %s", exc)
            if attempt < MAX_RETRIES - 1:
                import time  # noqa: PLC0415

                time.sleep(BACKOFF_SCHEDULE_SECONDS[attempt])
            continue

        status = resp.status_code
        if status == 200:
            try:
                payload = resp.json()
            except ValueError:
                if attempt < MAX_RETRIES - 1:
                    import time  # noqa: PLC0415

                    time.sleep(BACKOFF_SCHEDULE_SECONDS[attempt])
                continue
            if isinstance(payload, dict):
                return payload
            return None

        if status == 429 or 500 <= status < 600:
            logger.debug(
                "Wikidata matcher HTTP %d (attempt %d/%d)",
                status,
                attempt + 1,
                MAX_RETRIES,
            )
            if attempt < MAX_RETRIES - 1:
                import time  # noqa: PLC0415

                time.sleep(BACKOFF_SCHEDULE_SECONDS[attempt])
            continue

        # Permanent 4xx other than 429.
        return None

    return None


def _select_qids(payload: dict[str, Any] | None) -> list[str]:
    if not payload:
        return []
    bindings_field = payload.get("results", {})
    if not isinstance(bindings_field, dict):
        return []
    bindings = bindings_field.get("bindings", [])
    if not isinstance(bindings, list):
        return []
    out: list[str] = []
    for row in bindings:
        if not isinstance(row, dict):
            continue
        # Either ?p or ?item — accept whichever is present.
        for var in ("p", "item"):
            field = row.get(var)
            if isinstance(field, dict):
                value = field.get("value", "")
                if isinstance(value, str):
                    qid = _qid_from_uri(value)
                    if qid and qid not in out:
                        out.append(qid)
    return out


def _extract_literal_values(payload: dict[str, Any] | None, var: str) -> list[str]:
    """Return literal ``?var`` values from a SPARQL bindings payload."""
    if not payload:
        return []
    bindings_field = payload.get("results", {})
    if not isinstance(bindings_field, dict):
        return []
    bindings = bindings_field.get("bindings", [])
    if not isinstance(bindings, list):
        return []
    out: list[str] = []
    for row in bindings:
        if not isinstance(row, dict):
            continue
        field = row.get(var)
        if isinstance(field, dict):
            value = field.get("value", "")
            if isinstance(value, str) and value:
                out.append(value)
    return out


def _select_labels(payload: dict[str, Any] | None) -> list[str]:
    if not payload:
        return []
    bindings_field = payload.get("results", {})
    if not isinstance(bindings_field, dict):
        return []
    bindings = bindings_field.get("bindings", [])
    if not isinstance(bindings, list):
        return []
    out: list[str] = []
    for row in bindings:
        if not isinstance(row, dict):
            continue
        field = row.get("label")
        if isinstance(field, dict):
            value = field.get("value", "")
            if isinstance(value, str) and value:
                out.append(value)
    return out


def _ask_result(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    return bool(payload.get("boolean", False))


# ── Levenshtein (small, stdlib-only) ──────────────────────────────────


def _levenshtein(a: str, b: str) -> int:
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
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + cost,
            )
        previous = current
    return previous[-1]


# ── WikidataMatcher ───────────────────────────────────────────────────


class WikidataMatcher:
    """Primary-source SPARQL resolver for Hebrew + Latin authority names.

    Returns bare QIDs (e.g. ``"Q12345"``) — never URIs. All HTTP failures
    degrade to ``None``; the matcher never raises.
    """

    def __init__(
        self,
        *,
        cache_path: Path | None = None,
        throttle: bool = True,
    ) -> None:
        self._cache_path = _cache_path(cache_path)
        self._throttle_enabled = throttle
        # In-memory mirror of the on-disk cache, lazily loaded.
        self._cache: dict[str, dict[str, Any]] | None = None
        # Per-instance flag set whenever the most recent successful match
        # came from Mode 3 (Latin transliteration). The integrator
        # (Agent F) consults :meth:`last_match_was_latin_only` so it can
        # skip auto-promotion of those hits.
        self._last_was_latin_only: bool = False

    # ── public API ────────────────────────────────────────────────────

    def match_person(self, name: str, dates: str | None = None) -> str | None:
        del dates  # accepted for API symmetry; WDQS has no date filter here.
        return self._match_label(name, _PERSON_TYPES)

    def match_place(self, name: str) -> str | None:
        return self._match_label(name, _PLACE_TYPES)

    def match_corporate(self, name: str) -> str | None:
        return self._match_label(name, _CORPORATE_TYPES)

    def match_work(self, title: str, author: str | None = None) -> str | None:
        del author  # author-based disambiguation is Agent F's concern.
        return self._match_label(title, _WORK_TYPES)

    def find_qid_by_viaf(self, viaf_id: str) -> str | None:
        return self._match_identifier("P214", viaf_id)

    def find_qid_by_mazal(self, mazal_id: str) -> str | None:
        return self._match_identifier("P8189", mazal_id)

    def find_viaf_by_qid(self, qid: str) -> str | None:
        """Backfill VIAF ID from a known Wikidata QID via ``wdt:P214``.

        Used after NLI-strict mode resolves a Mazal hit and triangulates
        to a Wikidata QID — the Wikidata page nearly always carries the
        canonical VIAF cluster ID, which the VIAF SRU search frequently
        misses. ≤ 1 row returned → trusted; multiple values → abstain
        (conflicting VIAF IDs on one item is itself a data-quality flag).
        Honours the same on-disk cache as the other identifier lookups.
        """
        if not is_enabled() or not qid:
            return None
        cache_key = f"qid_to_viaf:{qid}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            value = cached.get("viaf_id")
            return value if isinstance(value, str) else None

        query = (
            f"SELECT ?viaf WHERE {{ wd:{qid} wdt:P214 ?viaf . }} LIMIT 2"
        )
        payload = _http_sparql(query)
        viafs = _extract_literal_values(payload, "viaf")
        chosen = viafs[0] if len(viafs) == 1 else None
        self._cache_put(cache_key, {"viaf_id": chosen})
        return chosen

    def last_match_was_latin_only(self) -> bool:
        """True iff the most recent successful match came from Mode 3
        (Latin transliteration fallback). The worker can use this to
        gate auto-promotion of weaker matches."""
        return self._last_was_latin_only

    # ── internals ─────────────────────────────────────────────────────

    def _match_identifier(self, property_id: str, value: str) -> str | None:
        if not is_enabled() or not value:
            return None
        cache_key = f"id:{property_id}:{value}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            self._last_was_latin_only = bool(cached.get("latin_only", False))
            return cached.get("qid") if isinstance(cached.get("qid"), str) else None

        query = _build_identifier_query(property_id, value)
        payload = _http_sparql(query)
        qids = _select_qids(payload)
        # 1 row = trusted; 0 or 2+ rows = abstain.
        chosen = qids[0] if len(qids) == 1 else None
        self._last_was_latin_only = False
        self._cache_put(cache_key, {"qid": chosen, "latin_only": False})
        return chosen

    def _match_label(self, raw_name: str, type_set: tuple[str, ...]) -> str | None:
        if not is_enabled() or not raw_name:
            return None
        normalised = strip_hebrew_diacritics(raw_name)
        if not normalised:
            return None

        cache_key = f"label:{','.join(type_set)}:{normalised}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            self._last_was_latin_only = bool(cached.get("latin_only", False))
            return cached.get("qid") if isinstance(cached.get("qid"), str) else None

        # Mode 2 — Hebrew label.
        qid = self._mode_label_search(normalised, type_set, lang="he")
        latin_only = False
        if qid is None:
            # Mode 3 — Latin/English fallback.
            qid = self._mode_label_search(normalised, type_set, lang="en")
            latin_only = qid is not None

        self._last_was_latin_only = latin_only
        self._cache_put(cache_key, {"qid": qid, "latin_only": latin_only})
        return qid

    def _mode_label_search(
        self,
        normalised_name: str,
        type_set: tuple[str, ...],
        *,
        lang: str,
    ) -> str | None:
        """Run the label query for *normalised_name*, then verify each
        candidate's type membership and label distance.

        Candidates are sorted by QID number ascending before verification
        — lower QIDs are almost always more canonical (older). This stops
        the matcher from picking a recent pipeline-created duplicate
        (e.g. Q139094451 for Rashi) over the canonical entity (Q189564)
        when both happen to share the same Hebrew label.
        """
        query = _build_label_query(normalised_name, type_set, lang)
        payload = _http_sparql(query)
        candidates = _select_qids(payload)
        candidates_sorted = sorted(candidates, key=_qid_sort_key)
        for candidate in candidates_sorted:
            if not self._verify_type(candidate, type_set):
                continue
            if not self._verify_label(candidate, normalised_name, lang):
                continue
            return candidate
        return None

    def _verify_type(self, qid: str, type_set: tuple[str, ...]) -> bool:
        """Re-check ``P31/P279*`` membership on *qid*. Returns False if
        Wikidata classifies the candidate outside *type_set*."""
        query = _build_type_check_query(qid, type_set)
        payload = _http_sparql(query)
        return _ask_result(payload)

    def _verify_label(self, qid: str, needle_normalised: str, lang: str) -> bool:
        """Fetch the candidate's labels in *lang* and accept iff at
        least one is within Levenshtein ≤ 1 of *needle_normalised* after
        diacritic stripping."""
        query = _build_label_only_query(qid, lang)
        payload = _http_sparql(query)
        labels = _select_labels(payload)
        for label in labels:
            haystack = strip_hebrew_diacritics(label)
            if not haystack:
                continue
            if _levenshtein(needle_normalised, haystack) <= 1:
                return True
        return False

    # ── cache plumbing ────────────────────────────────────────────────

    def _ensure_cache(self) -> dict[str, dict[str, Any]]:
        if self._cache is None:
            with _cache_lock:
                self._cache = _load_cache(self._cache_path)
        return self._cache

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        cache = self._ensure_cache()
        entry = cache.get(key)
        if not isinstance(entry, dict):
            return None
        ts = entry.get("fetched_at")
        if not isinstance(ts, (int, float)):
            return None
        if (_now() - float(ts)) >= CACHE_TTL_SECONDS:
            return None
        return entry

    def _cache_put(self, key: str, payload: dict[str, Any]) -> None:
        cache = self._ensure_cache()
        cache[key] = {**payload, "fetched_at": _now()}
        with _cache_lock:
            _save_cache(self._cache_path, cache)


__all__ = [
    "WikidataMatcher",
]
