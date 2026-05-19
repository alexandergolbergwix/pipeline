"""Wikidata reverse lookup — Hebrew label → canonical English label.

This module implements Tier 2.5 of the Rule 46 smart-transliteration waterfall.
When the MHM pipeline must emit an English label for a Hebrew person or work
name, it asks Wikidata whether an item already carries that exact Hebrew
string as its ``rdfs:label@he`` or ``skos:altLabel@he``. If so, the item's
``rdfs:label@en`` is the canonical Latin form a Wikidata curator would expect
(``"משה בן מימון"`` → ``"Maimonides"`` rather than the algorithmic
``"Moshe ben Maimon"``).

Design constraints — derived from CLAUDE.md Rule 46 + Rule 25 (moratorium):

* **Never raises.** All failure modes — bad input, no Hebrew, network error,
  HTTP non-200, malformed JSON, cache I/O error — return ``None`` so the
  caller can simply fall through to the next tier.
* **Offline-safe.** Honours ``MHM_NO_NETWORK=true`` and the explicit
  ``allow_network=False`` override. The repository's ``tests/conftest.py``
  already blocks all real HTTP at the urllib3 layer, so unit tests cannot
  accidentally exfiltrate Hebrew labels even without explicit mocks.
* **Tight timeout.** A user-facing pipeline step cannot afford a 15-second
  WDQS wait. The default ``timeout_seconds=1.5`` matches the latency budget
  the orchestrator allocates for Tier 2.5.
* **Cached negative results.** A 24-hour TTL on misses lets the long tail of
  uncatalogued Hebrew names retry nightly without re-hammering WDQS.
* **No PII or claim data cached.** Only the Hebrew → English mapping plus the
  fetch timestamp and TTL. No QIDs, no descriptions — both because Rule 46 is
  strictly about label generation and because keeping the cache schema minimal
  makes it auditable.

See ``hebrew_translit.py`` for the rest of the waterfall (override dict,
NLI ALA-LC romanization read, algorithmic ALA-LC fallback). DO NOT import
this module from ``hebrew_translit`` — the integration happens one layer up
in the orchestrator so the offline tiers stay genuinely offline.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import pathlib
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# WDQS courtesy policy requires a descriptive User-Agent.
_USER_AGENT = (
    "MHMPipeline/1.0 (https://github.com/alexandergolbergwix/pipeline; alexandergo@wix.com)"
)

# TTLs in seconds. Positive results are stable on Wikidata for months, so
# 30 days is comfortable. Negative results may simply mean the entity has
# not yet been added to Wikidata, so we retry daily.
_POSITIVE_TTL_SECONDS = 30 * 24 * 3600
_NEGATIVE_TTL_SECONDS = 24 * 3600

# Hard caps on the Hebrew literal length sent to SPARQL. The Wikidata
# courtesy policy prefers small, focused queries; a 200-char limit also
# acts as a defence-in-depth against pathological inputs.
_MAX_HEBREW_LEN = 200

# Cache file schema version. Bump if the entry shape changes.
_CACHE_VERSION = 1
_CACHE_FILENAME = "wikidata_reverse_label_cache.json"


# ── Public surface ────────────────────────────────────────────────────


def lookup_english_label(
    hebrew_text: str,
    *,
    timeout_seconds: float = 1.5,
    cache_path: pathlib.Path | None = None,
    allow_network: bool | None = None,
) -> str | None:
    """Return Wikidata's English label for a Hebrew label, or ``None``.

    Strategy:
        1. Check the on-disk JSON cache (default at the platformdirs
           ``user_cache_dir``). A live cache entry (positive or negative)
           is returned immediately with no network call.
        2. If absent and network is allowed, issue a single SPARQL query
           against ``https://query.wikidata.org/sparql`` with a tight timeout.
        3. On success, store the result in cache and return it.
        4. On failure (network error, timeout, no result, malformed JSON),
           cache the negative result for 24 hours and return ``None``.

    The function NEVER raises — all failures return ``None``. Callers can
    treat ``None`` as "fall through to the next tier".

    Args:
        hebrew_text: A Hebrew string to look up; must contain at least one
            Hebrew character. Leading/trailing whitespace is stripped.
        timeout_seconds: HTTP timeout for the SPARQL request.
        cache_path: Override the default cache location (used by tests).
        allow_network: Explicit override. If ``None``, defaults to ``True``
            unless ``MHM_NO_NETWORK=true`` is set in the environment.
    """
    if not isinstance(hebrew_text, str):
        return None
    cleaned = hebrew_text.strip()
    if not cleaned:
        return None
    if not _has_hebrew(cleaned):
        return None
    if len(cleaned) > _MAX_HEBREW_LEN:
        return None

    resolved_cache_path = _resolve_cache_path(cache_path)
    cache = _load_cache(resolved_cache_path)
    entry = cache.get("entries", {}).get(cleaned)
    if entry and _entry_is_fresh(entry):
        value = entry.get("english_label")
        if isinstance(value, str) or value is None:
            return value

    network_ok = _network_allowed(allow_network)
    if not network_ok:
        return None

    label = _query_wikidata(cleaned, timeout_seconds=timeout_seconds)
    ttl = _POSITIVE_TTL_SECONDS if label else _NEGATIVE_TTL_SECONDS
    _store_entry(resolved_cache_path, cache, cleaned, label, ttl)
    return label


# ── Internals ─────────────────────────────────────────────────────────


def _has_hebrew(text: str) -> bool:
    """True if any character is in the Hebrew Unicode block (U+0590..U+05FF)."""
    return any("֐" <= c <= "׿" for c in text)


def _network_allowed(explicit: bool | None) -> bool:
    """Resolve the network-allowed flag.

    Precedence: explicit argument wins over the env var. When the explicit
    argument is ``None``, the env var ``MHM_NO_NETWORK`` (truthy → block)
    decides. Default is to allow the network.
    """
    if explicit is not None:
        return explicit
    no_net = os.environ.get("MHM_NO_NETWORK", "").strip().lower()
    return no_net not in ("1", "true", "yes", "on")


def _escape_sparql_literal(text: str) -> str:
    """Escape a string for safe embedding inside a SPARQL ``"..."`` literal.

    The reconciler module uses the same minimal escape (backslash and
    double-quote). Newlines are stripped outright — they have no place in a
    Wikidata label and including them risks query injection.
    """
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _build_sparql(hebrew_text: str) -> str:
    """Return the SPARQL query that searches both rdfs:label and skos:altLabel."""
    safe = _escape_sparql_literal(hebrew_text)
    return (
        "SELECT ?label_en WHERE {\n"
        "  {\n"
        f'    ?item rdfs:label "{safe}"@he .\n'
        "  } UNION {\n"
        f'    ?item skos:altLabel "{safe}"@he .\n'
        "  }\n"
        "  ?item rdfs:label ?label_en .\n"
        '  FILTER(LANG(?label_en) = "en")\n'
        "  FILTER(!ISBLANK(?item))\n"
        "}\n"
        "LIMIT 1\n"
    )


def _query_wikidata(hebrew_text: str, *, timeout_seconds: float) -> str | None:
    """Issue the SPARQL query and extract the first English label, or None.

    Catches every exception class. The function's contract is "never raise"
    so the caller can blindly use the return value.
    """
    sparql = _build_sparql(hebrew_text)
    try:
        resp = requests.get(
            _SPARQL_ENDPOINT,
            params={"query": sparql, "format": "json"},
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/sparql-results+json",
            },
            timeout=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — explicitly swallow per contract
        logger.debug("Wikidata reverse lookup network error: %s", exc)
        return None

    try:
        if getattr(resp, "status_code", 0) != 200:
            return None
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Wikidata reverse lookup JSON error: %s", exc)
        return None

    if not isinstance(data, dict):
        return None
    bindings = data.get("results", {}).get("bindings", [])
    if not isinstance(bindings, list) or not bindings:
        return None
    first = bindings[0]
    if not isinstance(first, dict):
        return None
    label_node = first.get("label_en", {})
    if not isinstance(label_node, dict):
        return None
    value = label_node.get("value")
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


# ── Cache I/O ─────────────────────────────────────────────────────────


def _resolve_cache_path(explicit: pathlib.Path | None) -> pathlib.Path:
    """Return the cache file path, defaulting to platformdirs user cache dir."""
    if explicit is not None:
        return explicit
    try:
        import platformdirs  # noqa: PLC0415

        base = pathlib.Path(platformdirs.user_cache_dir("MHMPipeline"))
    except Exception:  # noqa: BLE001 — never fail; fall back to repo-local tmp
        base = pathlib.Path.home() / ".cache" / "MHMPipeline"
    return base / _CACHE_FILENAME


def _load_cache(path: pathlib.Path) -> dict[str, Any]:
    """Load cache JSON; return a fresh empty shell on any error."""
    empty: dict[str, Any] = {"version": _CACHE_VERSION, "entries": {}}
    try:
        if not path.exists():
            return empty
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Wikidata reverse lookup cache read failed: %s", exc)
        return empty
    if not isinstance(data, dict):
        return empty
    if data.get("version") != _CACHE_VERSION:
        return empty
    entries = data.get("entries")
    if not isinstance(entries, dict):
        data["entries"] = {}
    return data


def _entry_is_fresh(entry: dict[str, Any]) -> bool:
    """True if the entry's TTL has not expired yet."""
    fetched_at_raw = entry.get("fetched_at")
    ttl_raw = entry.get("ttl_seconds")
    if not isinstance(fetched_at_raw, str) or not isinstance(ttl_raw, int):
        return False
    try:
        # Accept both naive "...Z" and aware ISO 8601.
        if fetched_at_raw.endswith("Z"):
            fetched_at = _dt.datetime.fromisoformat(fetched_at_raw[:-1]).replace(
                tzinfo=_dt.UTC
            )
        else:
            fetched_at = _dt.datetime.fromisoformat(fetched_at_raw)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=_dt.UTC)
    except Exception:  # noqa: BLE001
        return False
    now = _dt.datetime.now(tz=_dt.UTC)
    age = (now - fetched_at).total_seconds()
    return age < ttl_raw


def _store_entry(
    path: pathlib.Path,
    cache: dict[str, Any],
    hebrew_text: str,
    english_label: str | None,
    ttl_seconds: int,
) -> None:
    """Write a cache entry to disk. Silently swallow I/O errors."""
    entries = cache.setdefault("entries", {})
    entries[hebrew_text] = {
        "english_label": english_label,
        "fetched_at": _dt.datetime.now(tz=_dt.UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ttl_seconds": ttl_seconds,
    }
    cache.setdefault("version", _CACHE_VERSION)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(cache, fp, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Wikidata reverse lookup cache write failed: %s", exc)
