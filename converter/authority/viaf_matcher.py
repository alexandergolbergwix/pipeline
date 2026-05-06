"""VIAF (Virtual International Authority File) matcher.

Queries the VIAF SRU/JSON API to resolve person names to VIAF cluster URIs.
Results are cached in memory to avoid repeated HTTP calls within a run.

VIAF SRU endpoint: https://viaf.org/viaf/search
Documentation: https://www.oclc.org/developer/api/oclc-apis/viaf/authority-cluster.en.html
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_VIAF_SEARCH = "https://viaf.org/viaf/search"
_TIMEOUT = 8  # seconds per request
_RATE_LIMIT = 0.5  # seconds between requests (2 req/s — VIAF rate limit)


def _year_from(value: object) -> int | None:
    """Extract a 4-digit year from a freeform date string (None-safe)."""
    if value is None:
        return None
    s = str(value)
    m = re.search(r"\d{3,4}", s)
    if not m:
        return None
    try:
        yr = int(m.group(0))
    except ValueError:
        return None
    return yr if 100 < yr < 2100 else None


def _extract_latin_main_heading(cluster_raw: dict[str, Any]) -> str | None:
    """Return the Latin-script preferred name from a VIAF cluster blob.

    Looks at ``ns1:mainHeadings.ns1:data[].ns1:text`` for the first
    string that contains ASCII letters. VIAF clusters often carry
    multiple language forms; the Latin form is the one we cross-validate
    against the source MARC name.
    """
    headings = cluster_raw.get("ns1:mainHeadings", cluster_raw.get("mainHeadings", {}))
    if not isinstance(headings, dict):
        return None
    data = headings.get("ns1:data", headings.get("data", []))
    if isinstance(data, dict):
        data = [data]
    for entry in data:
        if not isinstance(entry, dict):
            continue
        text = entry.get("ns1:text", entry.get("text", ""))
        if isinstance(text, str) and re.search(r"[A-Za-z]", text):
            return text.strip()
    return None


class VIAFMatcher:
    """Match entity names against the VIAF authority file.

    All results are cached per-instance so repeated calls for the same name
    hit the cache rather than the network.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}
        self._cluster_cache: dict[str, dict[str, str]] = {}
        # Raw cluster JSON cache — shared across identifier + biodata
        # consumers so one HTTP fetch serves both.
        self._cluster_raw_cache: dict[str, dict | None] = {}
        self._last_request: float = 0.0
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"

    # ── public API ────────────────────────────────────────────────────

    def match_person(self, name: str) -> str | None:
        """Return the VIAF cluster URI for *name*, or None if not found.

        Searches VIAF personal-name headings.  Returns the URI of the
        top-ranked cluster, e.g. ``https://viaf.org/viaf/97804603``.

        Clusters whose ``nameType`` is not ``"Personal"`` are rejected —
        this prevents corporate or geographic clusters (which VIAF sometimes
        returns via ``local.personalNames``) from being attached to person items.
        """
        return self._search(name, cql_field="local.personalNames", expected_name_type="Personal")

    def match_person_with_metadata(
        self, name: str, source_dates: str | None = None
    ) -> dict[str, Any] | None:
        """Match a personal name and return cluster metadata in one call.

        Wraps :meth:`match_person` + :meth:`get_cluster_identifiers` so
        the AuthorityWorker can score Stage 3 confidence guards without
        a second round-trip.

        Returns ``None`` if no cluster matched. Otherwise a dict with::

            {
                "viaf_uri": str,
                "viaf_id": str,        # numeric portion only
                "preferred_name_lat": str | None,
                "birth_year": int | None,
                "death_year": int | None,
                "name_type": str,      # always "Personal" if returned
                "gnd": str | None,
                "lc": str | None,
                "isni": str | None,
                "bnf": str | None,
                "j9u": str | None,
            }

        ``source_dates`` is currently unused (Mazal uses it for
        disambiguation; VIAF SRU does not expose a date filter) but
        is accepted so callers don't need to know which matcher
        consumes it.
        """
        del source_dates  # accepted for API symmetry; unused by VIAF SRU
        viaf_uri = self.match_person(name)
        if not viaf_uri:
            return None
        m = re.search(r"/viaf/(\d+)", viaf_uri)
        if not m:
            return None
        viaf_id = m.group(1)
        cluster = self.get_cluster_identifiers(viaf_id)
        # Prefer a Latin "main heading" out of the cluster if present.
        cluster_raw = self.get_cluster_raw(viaf_id) or {}
        preferred_lat = _extract_latin_main_heading(cluster_raw)

        out: dict[str, Any] = {
            "viaf_uri": viaf_uri,
            "viaf_id": viaf_id,
            "preferred_name_lat": preferred_lat,
            "birth_year": _year_from(cluster.get("birth_date")),
            "death_year": _year_from(cluster.get("death_date")),
            "name_type": cluster.get("name_type", "Personal"),
            "gnd": cluster.get("gnd"),
            "lc": cluster.get("lc"),
            "isni": cluster.get("isni"),
            "bnf": cluster.get("bnf"),
            "j9u": cluster.get("j9u"),
        }
        return out

    def match_place(self, name: str) -> str | None:
        """Return the VIAF cluster URI for a geographic name, or None."""
        return self._search(name, cql_field="local.geographicNames", expected_name_type="Geographic")

    def match_work(self, title: str) -> str | None:
        """Return the VIAF cluster URI for a uniform title, or None."""
        return self._search(title, cql_field="local.uniformTitleWorks")

    def get_cluster_raw(self, viaf_id: str) -> dict | None:
        """Fetch + cache the raw VIAF cluster JSON (unwrapped from
        ``ns1:VIAFCluster``). Shared by :meth:`get_cluster_identifiers`
        and :meth:`get_cluster_biodata` so a single HTTP call serves
        both callers.

        Returns ``None`` on network failure so callers can degrade
        gracefully — the review dialog shows "VIAF fetch failed" rather
        than crashing.
        """
        if viaf_id in self._cluster_raw_cache:
            return self._cluster_raw_cache[viaf_id]

        url = f"https://viaf.org/viaf/{viaf_id}"
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
            self._cluster_raw_cache[viaf_id] = None
            return None
        cluster = data.get("ns1:VIAFCluster", data)
        self._cluster_raw_cache[viaf_id] = cluster
        return cluster

    def get_cluster_biodata(self, viaf_id: str) -> dict | None:
        """Return the raw cluster blob shaped for
        :func:`converter.authority.biodata.extract_viaf_biodata`."""
        return self.get_cluster_raw(viaf_id)

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
        cluster = self.get_cluster_raw(viaf_id)
        if cluster is None:
            self._cluster_cache[viaf_id] = ids
            return ids

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
            # Bug fix 2026-04-16 (deeper audit Fixes #4-#6): VIAF returns
            # raw identifier strings that do NOT match Wikidata's strict
            # P244/P213/P268 format constraints. Normalise here and drop
            # entries that cannot be normalised (rather than emit them and
            # generate constraint-violation reports). DNB/J9U pass through.
            if prefix == "DNB" and "gnd" not in ids:
                ids["gnd"] = sid
            elif prefix == "LC" and "lc" not in ids:
                # Lazy import to avoid cyclic dep through wikidata package
                from converter.wikidata.property_mapping import normalize_lccn  # noqa: PLC0415

                normalised = normalize_lccn(sid)
                if normalised:
                    ids["lc"] = normalised
            elif prefix == "BNF" and "bnf" not in ids:
                from converter.wikidata.property_mapping import normalize_bnf  # noqa: PLC0415

                normalised = normalize_bnf(sid)
                if normalised:
                    ids["bnf"] = normalised
            elif prefix == "ISNI" and "isni" not in ids:
                from converter.wikidata.property_mapping import normalize_isni  # noqa: PLC0415

                normalised = normalize_isni(sid)
                if normalised:
                    ids["isni"] = normalised
            elif prefix == "J9U" and "j9u" not in ids:
                ids["j9u"] = sid

        # Extract dates
        birth = cluster.get("ns1:birthDate", cluster.get("birthDate", ""))
        death = cluster.get("ns1:deathDate", cluster.get("deathDate", ""))
        if birth and str(birth) not in ("0",):
            ids["birth_date"] = str(birth)
        if death and str(death) not in ("0",):
            ids["death_date"] = str(death)

        # Extract nameType — returned to callers so they can validate entity type
        name_type = cluster.get("ns1:nameType", cluster.get("nameType", ""))
        if name_type:
            ids["name_type"] = str(name_type)

        self._cluster_cache[viaf_id] = ids
        logger.debug("VIAF cluster %s: extracted %d identifiers", viaf_id, len(ids))
        return ids

    # ── internals ─────────────────────────────────────────────────────

    def _search(self, name: str, cql_field: str, expected_name_type: str | None = None) -> str | None:
        cache_key = f"{cql_field}:{name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._query_api(name, cql_field, expected_name_type=expected_name_type)
        self._cache[cache_key] = result
        return result

    def _query_api(self, name: str, cql_field: str, expected_name_type: str | None = None) -> str | None:
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
        record_list = (
            records_wrapper.get("record") if isinstance(records_wrapper, dict) else records_wrapper
        )
        if not record_list:
            return None

        first = record_list[0] if isinstance(record_list, list) else record_list

        # viafID lives at recordData.ns2:VIAFCluster.ns2:viafID
        record_data = first.get("recordData", {})
        cluster_data = record_data.get("ns2:VIAFCluster", {})
        viaf_id = cluster_data.get("ns2:viafID") or record_data.get("viafID")
        if not viaf_id:
            return None

        # Guard (2026-05-04 audit): reject SRU "ephemeral" search IDs.
        # Real VIAF cluster identifiers are 8–15-digit decimal strings
        # (https://www.oclc.org/developer/api/oclc-apis/viaf.en.html).
        # The SRU response sometimes returns longer composite strings
        # (e.g. ``9696171732610409080007`` — 22 digits) which do NOT
        # resolve to a single cluster and produce wrong matches when
        # used downstream. Refuse them.
        viaf_id_str = str(viaf_id).strip()
        if not viaf_id_str.isdigit() or not (8 <= len(viaf_id_str) <= 15):
            logger.debug(
                "VIAF: rejecting non-cluster ID %r for %r (len=%d, must be 8-15 digits)",
                viaf_id_str, name, len(viaf_id_str),
            )
            return None

        # Guard: reject cross-type matches (e.g. Corporate cluster returned by
        # local.personalNames search). When nameType is absent from the response
        # (older API versions), we accept the result rather than reject on uncertainty.
        if expected_name_type is not None:
            name_type = cluster_data.get("ns2:nameType", "") or record_data.get("nameType", "")
            if name_type and name_type != expected_name_type:
                logger.debug(
                    "VIAF: rejecting cluster %s (nameType=%r, expected=%r) for %r",
                    viaf_id_str,
                    name_type,
                    expected_name_type,
                    name,
                )
                return None

        return f"https://viaf.org/viaf/{viaf_id_str}"
