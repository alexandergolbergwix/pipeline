"""Project HMO RDF into Wikidata item objects.

Stage 6 should start from the validated HMO graph whenever possible.  The
graph is the scholarly representation, while Wikidata is the public projection.
When the RDF was built next to ``authority_enriched_reviewed.json`` we reuse
that reviewed JSON as a provenance sidecar so the projection preserves the full
set of current Wikidata mappings without treating raw JSON as the primary input.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from converter.config.namespaces import CIDOC, HM, LRMOO
from converter.wikidata.item_builder import WikidataItem, WikidataItemBuilder

logger = logging.getLogger(__name__)


REVIEWED_AUTHORITY_FILENAME = "authority_enriched_reviewed.json"
RAW_AUTHORITY_FILENAME = "authority_enriched.json"


@dataclass(frozen=True)
class HmoWikidataBuildResult:
    """Wikidata projection result plus provenance metadata."""

    items: list[WikidataItem]
    provenance_marker: str
    ttl_path: Path
    sidecar_path: Path | None = None


def build_items_from_hmo_ttl(
    ttl_path: Path,
    progress_cb: Callable[[int, int], None] | None = None,
) -> HmoWikidataBuildResult:
    """Build Wikidata items from an HMO RDF file.

    The RDF file is always parsed first.  If a reviewed authority sidecar exists
    beside the TTL, that reviewed data is used for the actual Wikidata item
    construction because it contains the user-approved authority decisions and
    all fields required by the existing rich ``WikidataItemBuilder``.  Without a
    sidecar, a conservative RDF-only fallback builds manuscript records from the
    graph itself.
    """
    graph = Graph()
    graph.parse(ttl_path)

    sidecar = _select_sidecar(ttl_path)
    if sidecar is not None:
        records = _load_authority_records(sidecar)
        builder = WikidataItemBuilder()
        items = builder.build_all(records, progress_cb=progress_cb)
        marker = (
            "HMO RDF + user-reviewed authority enriched"
            if sidecar.name == REVIEWED_AUTHORITY_FILENAME
            else "HMO RDF + raw authority enriched"
        )
        return HmoWikidataBuildResult(
            items=items,
            provenance_marker=marker,
            ttl_path=ttl_path,
            sidecar_path=sidecar,
        )

    records = _records_from_rdf(graph)
    builder = WikidataItemBuilder()
    items = builder.build_all(records, progress_cb=progress_cb)
    return HmoWikidataBuildResult(
        items=items,
        provenance_marker="HMO RDF",
        ttl_path=ttl_path,
        sidecar_path=None,
    )


def _select_sidecar(ttl_path: Path) -> Path | None:
    """Prefer reviewed authority data, then raw authority data, beside the TTL."""
    for name in (REVIEWED_AUTHORITY_FILENAME, RAW_AUTHORITY_FILENAME):
        candidate = ttl_path.with_name(name)
        if candidate.exists():
            return candidate
    return None


def _load_authority_records(path: Path) -> list[dict[str, object]]:
    """Load an authority-enriched JSON sidecar and reject Studio review state."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path.name} must contain a JSON list of manuscript records")
    if raw and isinstance(raw[0], dict) and "item" in raw[0] and "validation" in raw[0]:
        raise ValueError(
            f"{path.name} is Wikidata Studio review state. Use "
            "authority_enriched_reviewed.json as the Stage 6 sidecar."
        )
    return [dict(entry) for entry in raw if isinstance(entry, dict)]


def _records_from_rdf(graph: Graph) -> list[dict[str, object]]:
    """Create minimal authority-record dictionaries from HMO RDF alone."""
    records: list[dict[str, object]] = []
    manuscripts = sorted(
        set(graph.subjects(RDF.type, LRMOO.F4_Manifestation_Singleton)),
        key=lambda uri: str(uri),
    )
    total = len(manuscripts)
    logger.info("Building RDF-only Wikidata projection for %d manuscripts", total)

    for ms_uri in manuscripts:
        if not isinstance(ms_uri, URIRef):
            continue
        control_number = _literal_text(
            graph.value(ms_uri, HM.external_identifier_nli)
        ) or _control_number_from_uri(ms_uri)
        record: dict[str, object] = {
            "_control_number": control_number,
            "title": _literal_text(graph.value(ms_uri, RDFS.label)),
        }

        comments = [
            str(obj) for obj in graph.objects(ms_uri, RDFS.comment)
            if isinstance(obj, Literal)
        ]
        if comments:
            record["notes"] = comments

        provenance = _literal_text(graph.value(ms_uri, HM.ownership_history))
        if provenance:
            record["provenance"] = provenance

        dates = _dates_from_rdf(graph, ms_uri)
        if dates:
            record["dates"] = dates

        place = _production_place_from_rdf(graph, ms_uri)
        if place:
            record["place"] = place

        contents = _contents_from_rdf(graph, ms_uri)
        if contents:
            record["contents"] = contents

        related_places = _related_places_from_rdf(graph, ms_uri)
        if related_places:
            record["related_places"] = related_places

        digital_url = _literal_text(
            graph.value(ms_uri, HM.has_digital_representation_url)
        ) or _digital_access_url_from_rdf(graph, ms_uri)
        if digital_url:
            record["digital_url"] = digital_url

        rights = _rights_from_rdf(graph, ms_uri)
        if rights:
            record["rights_statement"] = rights

        records.append(record)
    return records


def _literal_text(value: object) -> str:
    """Return a stripped literal/resource string or an empty string."""
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _control_number_from_uri(uri: URIRef) -> str:
    """Extract a stable manuscript control number from a generated HMO URI."""
    fragment = str(uri).rsplit("#", maxsplit=1)[-1]
    match = re.search(r"(\d{8,})", fragment)
    return match.group(1) if match else fragment


def _dates_from_rdf(graph: Graph, ms_uri: URIRef) -> dict[str, object]:
    """Extract production-date bounds from the HMO production event."""
    for prod_uri in graph.objects(ms_uri, HM.has_production_event):
        time_uri = graph.value(prod_uri, HM.has_production_time)
        if time_uri is None:
            time_uri = graph.value(prod_uri, CIDOC.P4_has_time_span)
        if time_uri is None:
            continue
        start = _literal_text(graph.value(time_uri, HM.earliest_possible_date))
        end = _literal_text(graph.value(time_uri, HM.latest_possible_date))
        original = _literal_text(graph.value(time_uri, HM.date_original_string))
        label = _literal_text(graph.value(time_uri, RDFS.label))
        dates: dict[str, object] = {}
        if start and end and start == end and start.lstrip("-").isdigit():
            dates["year"] = int(start)
        else:
            if start and start.lstrip("-").isdigit():
                dates["date_start"] = int(start)
            if end and end.lstrip("-").isdigit():
                dates["date_end"] = int(end)
        if original or label:
            dates["original_string"] = original or label
        return dates
    return {}


def _production_place_from_rdf(graph: Graph, ms_uri: URIRef) -> str:
    """Return a production-place label when one is present."""
    for prod_uri in graph.objects(ms_uri, HM.has_production_event):
        place_uri = graph.value(prod_uri, HM.has_production_place)
        if place_uri is None:
            place_uri = graph.value(prod_uri, CIDOC.P7_took_place_at)
        if place_uri is not None:
            label = _literal_text(graph.value(place_uri, RDFS.label))
            if label:
                return label
    return ""


def _contents_from_rdf(graph: Graph, ms_uri: URIRef) -> list[dict[str, str]]:
    """Extract work labels linked from the manuscript."""
    contents: list[dict[str, str]] = []
    seen: set[str] = set()
    for work_uri in graph.objects(ms_uri, HM.has_work):
        title = _literal_text(graph.value(work_uri, RDFS.label)) or _literal_text(
            graph.value(work_uri, HM.has_title)
        )
        if not title or title in seen or title.startswith("Unidentified textual content"):
            continue
        seen.add(title)
        contents.append({"title": title})
    return contents


def _related_places_from_rdf(graph: Graph, ms_uri: URIRef) -> list[str]:
    """Extract labels of places mentioned by the manuscript."""
    places: list[str] = []
    seen: set[str] = set()
    for place_uri in graph.objects(ms_uri, HM.mentions_place):
        label = _literal_text(graph.value(place_uri, RDFS.label))
        if label and label not in seen:
            seen.add(label)
            places.append(label)
    return places


def _digital_access_url_from_rdf(graph: Graph, ms_uri: URIRef) -> str:
    """Extract a DigitalAccess URL linked to the manuscript."""
    for da_uri in graph.objects(ms_uri, HM.has_digital_access):
        url = _literal_text(graph.value(da_uri, HM.digital_access_url))
        if url:
            return url
    for da_uri in graph.subjects(HM.is_digital_access_of, ms_uri):
        url = _literal_text(graph.value(da_uri, HM.digital_access_url))
        if url:
            return url
    return ""


def _rights_from_rdf(graph: Graph, ms_uri: URIRef) -> str:
    """Extract a rights statement linked to the manuscript."""
    for rights_uri in graph.objects(ms_uri, HM.has_rights_determination):
        rights = _literal_text(graph.value(rights_uri, HM.rights_status)) or _literal_text(
            graph.value(rights_uri, HM.has_rights_statement)
        )
        if rights:
            return rights
    for rights_uri in graph.subjects(HM.is_rights_determination_of, ms_uri):
        rights = _literal_text(graph.value(rights_uri, HM.rights_status)) or _literal_text(
            graph.value(rights_uri, HM.has_rights_statement)
        )
        if rights:
            return rights
    return ""
