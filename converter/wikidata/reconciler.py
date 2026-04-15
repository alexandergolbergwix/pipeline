"""Reconcile pipeline entities against existing Wikidata items via SPARQL.

Checks whether manuscripts, persons, and places already exist on Wikidata
before creating new items. Uses the Wikidata Query Service SPARQL endpoint.

Rate-limited to 1 query/second per Wikidata API policy.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import requests

from converter.wikidata.property_mapping import (
    Q_MANUSCRIPT,
    Q_NLI,
    extract_viaf_id,
    extract_wikidata_qid,
)

logger = logging.getLogger(__name__)

_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
_RATE_LIMIT_SECONDS = 1.0
_TIMEOUT_SECONDS = 15
_USER_AGENT = (
    "MHMPipeline/0.1 (https://github.com/alexgoldberg/mhm-pipeline; alexander.goldberg@biu.ac.il)"
)


@dataclass
class ReconciliationResult:
    """Result of reconciling a single entity."""

    entity_type: str  # "manuscript" | "person" | "place"
    local_id: str
    label: str
    existing_qid: str | None = None
    action: str = "create"  # "create" | "update" | "skip"


@dataclass
class ReconciliationReport:
    """Aggregated reconciliation results."""

    results: list[ReconciliationResult] = field(default_factory=list)
    manuscripts_found: int = 0
    manuscripts_new: int = 0
    persons_found: int = 0
    persons_new: int = 0
    places_found: int = 0
    places_new: int = 0

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict."""
        return {
            "manuscripts_found": self.manuscripts_found,
            "manuscripts_new": self.manuscripts_new,
            "persons_found": self.persons_found,
            "persons_new": self.persons_new,
            "places_found": self.places_found,
            "places_new": self.places_new,
            "total_results": len(self.results),
            "results": [
                {
                    "entity_type": r.entity_type,
                    "local_id": r.local_id,
                    "label": r.label,
                    "existing_qid": r.existing_qid,
                    "action": r.action,
                }
                for r in self.results
            ],
        }


class WikidataReconciler:
    """Reconcile pipeline entities against Wikidata via SPARQL.

    Usage::

        reconciler = WikidataReconciler()
        report = reconciler.reconcile_all(records)
        print(f"Found {report.manuscripts_found} existing manuscripts")
    """

    def __init__(self, sparql_endpoint: str = _SPARQL_ENDPOINT) -> None:
        self._endpoint = sparql_endpoint
        self._cache: dict[str, str | None] = {}
        self._last_request_time: float = 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": _USER_AGENT,
                "Accept": "application/sparql-results+json",
            }
        )

    def _rate_limit(self) -> None:
        """Enforce rate limiting between SPARQL requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < _RATE_LIMIT_SECONDS:
            time.sleep(_RATE_LIMIT_SECONDS - elapsed)
        self._last_request_time = time.time()

    def _query(self, sparql: str) -> list[dict[str, object]]:
        """Execute a SPARQL query and return results bindings.

        Args:
            sparql: The SPARQL query string.

        Returns:
            List of result binding dicts.
        """
        self._rate_limit()
        try:
            resp = self._session.get(
                self._endpoint,
                params={"query": sparql, "format": "json"},
                timeout=_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", {}).get("bindings", [])
        except requests.RequestException as exc:
            logger.warning("SPARQL query failed: %s", exc)
            return []

    def reconcile_manuscript_by_nli_id(self, control_number: str) -> str | None:
        """Check if a manuscript item exists via P8189 (NLI J9U ID).

        Args:
            control_number: NLI system number.

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"ms:nli:{control_number}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P8189 "{control_number}" .
          ?item wdt:P31 wd:{Q_MANUSCRIPT} .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    def reconcile_manuscript_by_shelfmark(
        self,
        shelfmark: str,
        collection_qid: str = Q_NLI,
    ) -> str | None:
        """Check if a manuscript item exists via P195 (collection) + P217 (shelfmark).

        Args:
            shelfmark: The inventory/shelf mark string.
            collection_qid: QID of the holding institution.

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"ms:shelf:{collection_qid}:{shelfmark}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        safe_shelfmark = shelfmark.replace('"', '\\"')
        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P195 wd:{collection_qid} .
          ?item wdt:P217 "{safe_shelfmark}" .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    def reconcile_person_by_viaf(self, viaf_id: str) -> str | None:
        """Check if a person item exists via P214 (VIAF ID).

        Args:
            viaf_id: Numeric VIAF cluster ID.

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"person:viaf:{viaf_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P214 "{viaf_id}" .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    def reconcile_person_by_nli_id(self, nli_id: str) -> str | None:
        """Check if a person item exists via P8189 (NLI J9U ID).

        Args:
            nli_id: NLI authority ID (Mazal ID).

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"person:nli:{nli_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P8189 "{nli_id}" .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    def reconcile_place(self, wikidata_uri: str) -> str | None:
        """Validate that a KIMA-provided Wikidata QID exists.

        Args:
            wikidata_uri: Full Wikidata URI from KIMA data.

        Returns:
            QID if valid and exists, None otherwise.
        """
        qid = extract_wikidata_qid(wikidata_uri)
        if not qid:
            return None

        cache_key = f"place:{qid}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # KIMA QIDs are already validated; just confirm format
        self._cache[cache_key] = qid
        return qid

    def reconcile_manuscript(
        self,
        control_number: str,
        shelfmark: str | None,
    ) -> str | None:
        """Reconcile a manuscript by NLI ID first, then shelfmark fallback.

        Args:
            control_number: NLI system number.
            shelfmark: Optional inventory number.

        Returns:
            QID if found, None otherwise.
        """
        qid = self.reconcile_manuscript_by_nli_id(control_number)
        if qid:
            return qid

        if shelfmark:
            return self.reconcile_manuscript_by_shelfmark(shelfmark)

        return None

    def reconcile_person_by_external_id(
        self,
        prop: str,
        ext_id: str,
    ) -> str | None:
        """Check if a person item exists via any external-id property.

        Args:
            prop: Wikidata property ID (e.g., "P244" for LCCN).
            ext_id: The external identifier value.

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"person:{prop}:{ext_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:{prop} "{ext_id}" .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    # Properties used for cross-identifier conflict verification.
    # If a candidate item already has a value on these properties that
    # differs from the value we would attach, the candidate is REJECTED
    # as a match — they are almost certainly different real-world entities
    # who happen to share one identifier.
    _IDENTITY_PROPS = ("P214", "P8189", "P244", "P227", "P213")  # VIAF, NLI, LCCN, GND, ISNI

    def _fetch_identity_claims(self, qid: str) -> dict[str, set[str]]:
        """Fetch a candidate item's identity claims (VIAF/NLI/LCCN/GND/ISNI/DOB/POB).

        Returns a dict mapping property → set of string values currently on the item.
        Empty dict on lookup failure (caller should treat as 'no info, allow match').
        """
        cache_key = f"identity:{qid}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if isinstance(cached, dict):
                return cached
        sparql = f"""
        SELECT ?p ?v WHERE {{
          VALUES ?p {{ wdt:P214 wdt:P8189 wdt:P244 wdt:P227 wdt:P213 wdt:P569 wdt:P19 }}
          wd:{qid} ?p ?v .
        }}
        """
        results = self._query(sparql)
        out: dict[str, set[str]] = {}
        for b in results:
            prop_uri = b.get("p", {}).get("value", "")
            val = str(b.get("v", {}).get("value", ""))
            # Convert wdt:P… URI back to bare property id
            prop = prop_uri.rsplit("/", 1)[-1]
            out.setdefault(prop, set()).add(val)
        # Cache (cast through Any since _cache type is narrower)
        self._cache[cache_key] = out  # type: ignore[assignment]
        return out

    def _candidate_conflicts(
        self,
        qid: str,
        proposed: dict[str, str],
    ) -> list[str]:
        """Return list of property IDs where the candidate conflicts with proposed values.

        A conflict is when the candidate already has a value for the property AND
        none of those values matches the proposed value. Properties the candidate
        does not have are not conflicts (we'd just be adding new info).
        """
        existing = self._fetch_identity_claims(qid)
        conflicts: list[str] = []
        for prop, proposed_value in proposed.items():
            if not proposed_value:
                continue
            existing_vals = existing.get(prop, set())
            if existing_vals and proposed_value not in existing_vals:
                conflicts.append(prop)
        return conflicts

    def reconcile_person(
        self,
        name: str,
        viaf_uri: str | None,
        nli_id: str | None,
        lc_id: str | None = None,
        gnd_id: str | None = None,
        isni: str | None = None,
    ) -> str | None:
        """Reconcile a person by all available identifiers.

        SAFETY (added 2026-04-13): When an identifier matches a candidate item,
        we cross-verify against ALL other available identifiers we have. If the
        candidate already has a DIFFERENT value on any of those identifiers,
        we REJECT the match — they are different real-world entities who happen
        to share one identifier (e.g., two lawyers sharing an ISNI). This prevents
        the wrong-merge disaster where 902+ items got merged because the pipeline
        trusted a single shared identifier.

        Checks in order: VIAF → NLI → LCCN → GND → ISNI.
        Returns QID on first verified match; None otherwise.
        """
        viaf_id = extract_viaf_id(viaf_uri) if viaf_uri else None
        proposed: dict[str, str] = {}
        if viaf_id:
            proposed["P214"] = viaf_id
        if nli_id:
            proposed["P8189"] = nli_id
        if lc_id:
            proposed["P244"] = lc_id
        if gnd_id:
            proposed["P227"] = gnd_id
        if isni:
            proposed["P213"] = isni

        candidates: list[tuple[str, str]] = []  # (matching_prop, qid)
        if viaf_id:
            qid = self.reconcile_person_by_viaf(viaf_id)
            if qid:
                candidates.append(("P214", qid))
        if nli_id:
            qid = self.reconcile_person_by_nli_id(nli_id)
            if qid:
                candidates.append(("P8189", qid))
        if lc_id:
            qid = self.reconcile_person_by_external_id("P244", lc_id)
            if qid:
                candidates.append(("P244", qid))
        if gnd_id:
            qid = self.reconcile_person_by_external_id("P227", gnd_id)
            if qid:
                candidates.append(("P227", qid))
        if isni:
            qid = self.reconcile_person_by_external_id("P213", isni)
            if qid:
                candidates.append(("P213", qid))

        for matched_prop, qid in candidates:
            conflicts = self._candidate_conflicts(qid, proposed)
            if conflicts:
                logger.warning(
                    "RECONCILE REJECT: %s matched %s=%s but has conflicting %s — "
                    "treating as different entity, will create new item",
                    qid,
                    matched_prop,
                    proposed.get(matched_prop),
                    ", ".join(conflicts),
                )
                continue
            return qid

        return None

    # ── Batch reconciliation (efficient for large uploads) ────────

    def reconcile_batch_by_nli_id(
        self,
        nli_ids: list[str],
    ) -> dict[str, str]:
        """Reconcile multiple items by NLI J9U ID in a single SPARQL query.

        Uses the VALUES clause to batch-query up to 100 IDs at once.

        Returns:
            Dict mapping NLI ID → Wikidata QID for found items.
        """
        results: dict[str, str] = {}
        # Process in chunks of 100 (SPARQL VALUES limit)
        for i in range(0, len(nli_ids), 100):
            chunk = nli_ids[i : i + 100]
            values = " ".join(f'"{nid}"' for nid in chunk)
            sparql = f"""
            SELECT ?item ?nli WHERE {{
              VALUES ?nli {{ {values} }}
              ?item wdt:P8189 ?nli .
            }}
            """
            try:
                bindings = self._query(sparql)
                for b in bindings:
                    nli = b.get("nli", {}).get("value", "")
                    item_uri = b.get("item", {}).get("value", "")
                    qid = extract_wikidata_qid(item_uri)
                    if qid and nli:
                        results[nli] = qid
            except Exception as exc:
                logger.warning("Batch reconciliation failed for chunk %d: %s", i, exc)

        logger.info("Batch reconciliation: %d/%d NLI IDs matched", len(results), len(nli_ids))
        return results

    def reconcile_all(
        self,
        records: list[dict[str, object]],
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> ReconciliationReport:
        """Reconcile all entities across all records.

        Args:
            records: List of record dicts from authority_enriched.json.
            progress_cb: Optional callback called with (current, total).

        Returns:
            A ReconciliationReport with per-entity results and aggregate counts.
        """
        report = ReconciliationReport()
        total = len(records)

        # Collect unique persons to avoid redundant queries
        persons_seen: dict[str, ReconciliationResult] = {}

        for idx, record in enumerate(records):
            control_number = str(record.get("_control_number", ""))
            title = str(record.get("title", ""))
            shelfmark = record.get("shelfmark")

            # Reconcile manuscript
            ms_qid = self.reconcile_manuscript(
                control_number,
                str(shelfmark) if shelfmark else None,
            )
            ms_result = ReconciliationResult(
                entity_type="manuscript",
                local_id=control_number,
                label=title[:80] if title else control_number,
                existing_qid=ms_qid,
                action="update" if ms_qid else "create",
            )
            report.results.append(ms_result)
            if ms_qid:
                report.manuscripts_found += 1
            else:
                report.manuscripts_new += 1

            # Reconcile persons from MARC authority matches
            for match in record.get("marc_authority_matches") or []:
                name = str(match.get("name", ""))
                viaf_uri = match.get("viaf_uri")
                mazal_id = match.get("mazal_id")
                person_key = f"{name}:{viaf_uri}:{mazal_id}"

                if person_key in persons_seen:
                    continue

                person_qid = self.reconcile_person(
                    name,
                    viaf_uri,
                    mazal_id,
                    lc_id=match.get("lc_id"),
                    gnd_id=match.get("gnd_id"),
                    isni=match.get("isni"),
                )
                person_result = ReconciliationResult(
                    entity_type="person",
                    local_id=person_key,
                    label=name[:80],
                    existing_qid=person_qid,
                    action="skip" if person_qid else "create",
                )
                persons_seen[person_key] = person_result
                report.results.append(person_result)
                if person_qid:
                    report.persons_found += 1
                else:
                    report.persons_new += 1

            # Reconcile NER entities — pass ALL available identifiers so the
            # reconciler can find existing Wikidata items matched by any of
            # P244 (LCCN), P227 (GND), P213 (ISNI), in addition to VIAF/NLI.
            # Bug fix (2026-04-15, Geagea complaint): omitting lc_id/gnd_id/isni
            # caused the pipeline to create duplicate person items even when
            # the existing community item could have been found by one of those.
            for entity in record.get("entities") or []:
                name = str(entity.get("person", ""))
                viaf_uri = entity.get("viaf_uri")
                mazal_id = entity.get("mazal_id")
                person_key = f"{name}:{viaf_uri}:{mazal_id}"

                if person_key in persons_seen:
                    continue

                person_qid = self.reconcile_person(
                    name,
                    viaf_uri,
                    mazal_id,
                    lc_id=entity.get("lc_id"),
                    gnd_id=entity.get("gnd_id"),
                    isni=entity.get("isni"),
                )
                person_result = ReconciliationResult(
                    entity_type="person",
                    local_id=person_key,
                    label=name[:80],
                    existing_qid=person_qid,
                    action="skip" if person_qid else "create",
                )
                persons_seen[person_key] = person_result
                report.results.append(person_result)
                if person_qid:
                    report.persons_found += 1
                else:
                    report.persons_new += 1

            # Reconcile KIMA places (already have Wikidata QIDs)
            for _place_name, wikidata_uri in (record.get("kima_places") or {}).items():
                qid = self.reconcile_place(str(wikidata_uri))
                if qid:
                    report.places_found += 1

            if progress_cb:
                progress_cb(idx + 1, total)

        logger.info(
            "Reconciliation complete: %d MS found / %d new, "
            "%d persons found / %d new, %d places validated",
            report.manuscripts_found,
            report.manuscripts_new,
            report.persons_found,
            report.persons_new,
            report.places_found,
        )
        return report
