"""VIAF (Virtual International Authority File) matcher.

Queries the VIAF SRU/JSON API to resolve person names to VIAF cluster URIs.
Results are cached in memory to avoid repeated HTTP calls within a run.

VIAF SRU endpoint: https://viaf.org/viaf/search
Documentation: https://www.oclc.org/developer/api/oclc-apis/viaf/authority-cluster.en.html
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_VIAF_SEARCH = "https://viaf.org/viaf/search"
_TIMEOUT = 8          # seconds per request
_RATE_LIMIT = 0.5     # seconds between requests (2 req/s — VIAF rate limit)


class VIAFMatcher:
    """Match entity names against the VIAF authority file.

    All results are cached per-instance so repeated calls for the same name
    hit the cache rather than the network.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}
        self._cluster_cache: dict[str, dict[str, str]] = {}
        self._last_request: float = 0.0
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"

    # ── public API ────────────────────────────────────────────────────

    def match_person(self, name: str) -> Optional[str]:
        """Return the VIAF cluster URI for *name*, or None if not found.

        Searches VIAF personal-name headings.  Returns the URI of the
        top-ranked cluster, e.g. ``https://viaf.org/viaf/97804603``.
        """
        return self._search(name, cql_field="local.personalNames")

    def match_place(self, name: str) -> Optional[str]:
        """Return the VIAF cluster URI for a geographic name, or None."""
        return self._search(name, cql_field="local.geographicNames")

    def match_work(self, title: str) -> Optional[str]:
        """Return the VIAF cluster URI for a uniform title, or None."""
        return self._search(title, cql_field="local.uniformTitleWorks")

    def get_cluster_identifiers(self, viaf_id: str) -> dict[str, str]:
        """Fetch VIAF cluster JSON and extract LOD authority identifiers.

        Given a VIAF ID (numeric), fetches the full cluster record and
        extracts GND, LC, BnF, and ISNI identifiers plus birth/death dates.

        Uses ``https://viaf.org/viaf/{id}`` with ``Accept: application/json``.
        Response is namespaced under ``ns1:VIAFCluster`` with sources in
        ``ns1:sources.ns1:source[].content`` as ``PREFIX|ID``.

        Returns a dict with keys: gnd, lc, bnf, isni, birth_date, death_date.
        """
        if viaf_id in self._cluster_cache:
            return self._cluster_cache[viaf_id]

        ids: dict[str, str] = {}
        # /viaf.json endpoint was removed; use content negotiation instead
        url = f"https://viaf.org/viaf/{viaf_id}"

        # Respect rate limit
        elapsed = time.monotonic() - self._last_request
        if elapsed < _RATE_LIMIT:
            time.sleep(_RATE_LIMIT - elapsed)

        try:
            resp = self._session.get(url, timeout=_TIMEOUT)
            self._last_request = time.monotonic()
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("VIAF cluster fetch failed for %s: %s", viaf_id, exc)
            self._last_request = time.monotonic()
            self._cluster_cache[viaf_id] = ids
            return ids

        # Navigate ns1:VIAFCluster wrapper
        cluster = data.get("ns1:VIAFCluster", data)

        # Extract source identifiers from ns1:sources.ns1:source array
        sources = cluster.get("ns1:sources", cluster.get("sources", {}))
        source_list = sources.get("ns1:source", sources.get("source", []))
        if isinstance(source_list, dict):
            source_list = [source_list]

        for source in source_list:
            # Current API uses "content" key; older used "#text"
            text = source.get("content", source.get("#text", ""))
            if isinstance(text, (int, float)):
                text = str(text)
            if not text or "|" not in text:
                continue
            prefix, sid = text.split("|", 1)
            sid = str(sid).strip()
            if prefix == "DNB" and "gnd" not in ids:
                ids["gnd"] = sid
            elif prefix == "LC" and "lc" not in ids:
                ids["lc"] = sid.replace(" ", "")
            elif prefix == "BNF" and "bnf" not in ids:
                ids["bnf"] = sid
            elif prefix == "ISNI" and "isni" not in ids:
                ids["isni"] = sid
            elif prefix == "J9U" and "j9u" not in ids:
                ids["j9u"] = sid

        # Extract dates
        birth = cluster.get("ns1:birthDate", cluster.get("birthDate", ""))
        death = cluster.get("ns1:deathDate", cluster.get("deathDate", ""))
        if birth and str(birth) not in ("0",):
            ids["birth_date"] = str(birth)
        if death and str(death) not in ("0",):
            ids["death_date"] = str(death)

        self._cluster_cache[viaf_id] = ids
        logger.debug("VIAF cluster %s: extracted %d identifiers", viaf_id, len(ids))
        return ids

    # ── internals ─────────────────────────────────────────────────────

    def _search(self, name: str, cql_field: str) -> Optional[str]:
        cache_key = f"{cql_field}:{name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._query_api(name, cql_field)
        self._cache[cache_key] = result
        return result

    def _query_api(self, name: str, cql_field: str) -> Optional[str]:
        # Respect rate limit
        elapsed = time.monotonic() - self._last_request
        if elapsed < _RATE_LIMIT:
            time.sleep(_RATE_LIMIT - elapsed)

        params = {
            "query": f'{cql_field} all "{name}"',
            "maximumRecords": "3",
        }
        try:
            resp = self._session.get(
                _VIAF_SEARCH,
                params=params,
                timeout=_TIMEOUT,
            )
            self._last_request = time.monotonic()
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("VIAF request failed for %r: %s", name, exc)
            self._last_request = time.monotonic()
            return None

        sru = data.get("searchRetrieveResponse", {})
        records_wrapper = sru.get("records")
        if not records_wrapper:
            return None

        # records_wrapper is {"record": [...]} or {"record": {...}}
        record_list = records_wrapper.get("record") if isinstance(records_wrapper, dict) else records_wrapper
        if not record_list:
            return None

        first = record_list[0] if isinstance(record_list, list) else record_list

        # viafID lives at recordData.ns2:VIAFCluster.ns2:viafID
        record_data = first.get("recordData", {})
        viaf_id = (
            record_data.get("ns2:VIAFCluster", {}).get("ns2:viafID")
            or record_data.get("viafID")
        )
        if not viaf_id:
            return None
        return f"https://viaf.org/viaf/{viaf_id}"
