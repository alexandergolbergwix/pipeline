"""Offline coverage report for projecting HMO RDF into Wikidata items."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal as TypeLiteral

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from converter.wikidata.item_builder import WikidataItem

ProjectionStatus = TypeLiteral[
    "direct_wikidata_item",
    "summarized_in_wikidata",
    "hmo_or_wikibase_only",
    "unknown",
]


@dataclass(frozen=True)
class ProjectionStrategy:
    """Stable strategy for one RDF class or local-name alias."""

    projection_status: ProjectionStatus
    wikidata_representation: str
    wikidata_properties: tuple[str, ...] = ()
    item_entity_type: str = ""
    notes: str = ""


_MANUSCRIPT_PROPERTIES: tuple[str, ...] = (
    "P31",
    "P195",
    "P217",
    "P1476",
    "P571",
    "P1071",
    "P1574",
    "P407",
    "P282",
    "P5008",
    "P6216",
)

_WORK_PROPERTIES: tuple[str, ...] = ("P31", "P50", "P136", "P407", "P1574")
_PERSON_PROPERTIES: tuple[str, ...] = (
    "P31",
    "P106",
    "P214",
    "P8189",
    "P569",
    "P570",
    "P1559",
)


STRATEGY_BY_LOCAL_NAME: dict[str, ProjectionStrategy] = {
    "F4_Manifestation_Singleton": ProjectionStrategy(
        projection_status="direct_wikidata_item",
        wikidata_representation="Wikidata manuscript item (Q87167)",
        wikidata_properties=_MANUSCRIPT_PROPERTIES,
        item_entity_type="manuscript",
        notes="Primary public Wikidata anchor; HMO remains the scholarly master graph.",
    ),
    "Manuscript": ProjectionStrategy(
        projection_status="direct_wikidata_item",
        wikidata_representation="Wikidata manuscript item (Q87167)",
        wikidata_properties=_MANUSCRIPT_PROPERTIES,
        item_entity_type="manuscript",
        notes="Primary public Wikidata anchor; HMO remains the scholarly master graph.",
    ),
    "F1_Work": ProjectionStrategy(
        projection_status="direct_wikidata_item",
        wikidata_representation="Wikidata written-work item (Q47461344) when identifiable and reusable",
        wikidata_properties=_WORK_PROPERTIES,
        item_entity_type="work",
        notes="Known works should link to existing QIDs; local work items are created conservatively.",
    ),
    "Work": ProjectionStrategy(
        projection_status="direct_wikidata_item",
        wikidata_representation="Wikidata written-work item (Q47461344) when identifiable and reusable",
        wikidata_properties=_WORK_PROPERTIES,
        item_entity_type="work",
        notes="Known works should link to existing QIDs; local work items are created conservatively.",
    ),
    "E21_Person": ProjectionStrategy(
        projection_status="direct_wikidata_item",
        wikidata_representation="Wikidata human item (Q5), or link to an existing person item",
        wikidata_properties=_PERSON_PROPERTIES,
        item_entity_type="person",
        notes="Person projection is conservative and depends on authority evidence.",
    ),
    "Person": ProjectionStrategy(
        projection_status="direct_wikidata_item",
        wikidata_representation="Wikidata human item (Q5), or link to an existing person item",
        wikidata_properties=_PERSON_PROPERTIES,
        item_entity_type="person",
        notes="Person projection is conservative and depends on authority evidence.",
    ),
    "Bibliographic_Unit": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Implicit in the manuscript item rather than a separate Wikidata item",
        wikidata_properties=_MANUSCRIPT_PROPERTIES,
        notes="Separate items are avoided unless a unit is cataloged as a public manuscript object.",
    ),
    "F2_Expression": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Collapsed into manuscript/work statements, labels, language, and qualifiers",
        wikidata_properties=("P1476", "P407", "P50", "P1574", "P958"),
        notes="Expression granularity is preserved in HMO by default.",
    ),
    "Expression": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Collapsed into manuscript/work statements, labels, language, and qualifiers",
        wikidata_properties=("P1476", "P407", "P50", "P1574", "P958"),
        notes="Expression granularity is preserved in HMO by default.",
    ),
    "Codicological_Unit": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Manuscript-level part count and content/folio qualifiers",
        wikidata_properties=("P2635", "P7535", "P1574", "P958"),
        notes="CU nodes remain in HMO; Wikidata uses summary claims unless a part is independently notable.",
    ),
    "Paleographical_Unit": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Script style, inscriptions, and note statements on the manuscript item",
        wikidata_properties=("P9302", "P1684", "P7535"),
        notes="Separate paleographical-unit items are avoided.",
    ),
    "E12_Production": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Date/place/agent claims on the manuscript item",
        wikidata_properties=("P571", "P1071", "P50", "P11603"),
        notes="The event node remains in HMO; Wikidata receives public production facts.",
    ),
    "Production": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Date/place/agent claims on the manuscript item",
        wikidata_properties=("P571", "P1071", "P50", "P11603"),
        notes="The event node remains in HMO; Wikidata receives public production facts.",
    ),
    "E8_Acquisition": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Ownership/provenance claims and qualifiers",
        wikidata_properties=("P127", "P580", "P582", "P1932"),
        notes="Full ownership-event modeling remains in HMO.",
    ),
    "Acquisition": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Ownership/provenance claims and qualifiers",
        wikidata_properties=("P127", "P580", "P582", "P1932"),
        notes="Full ownership-event modeling remains in HMO.",
    ),
    "E53_Place": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Existing place QIDs used as values on manuscript/person statements",
        wikidata_properties=("P1071", "P7153", "P19", "P20", "P17"),
        notes="The pipeline links to existing place items rather than creating local place items.",
    ),
    "Place": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Existing place QIDs used as values on manuscript/person statements",
        wikidata_properties=("P1071", "P7153", "P19", "P20", "P17"),
        notes="The pipeline links to existing place items rather than creating local place items.",
    ),
    "E74_Group": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Collection, institution, and owner values on public items",
        wikidata_properties=("P195", "P127", "P17", "P131"),
        notes="Institutional context is usually represented as values, not HMO group-node items.",
    ),
    "TextLocation": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Folio/section qualifiers on manuscript-work statements",
        wikidata_properties=("P1574", "P958"),
        notes="Structured locations remain in HMO; Wikidata receives concise qualifiers.",
    ),
    "Colophon": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Inscription statements with role/source qualifiers",
        wikidata_properties=("P1684", "P3831", "P887", "P958"),
        notes="The colophon node remains in HMO for richer evidential modeling.",
    ),
    "ScribalIntervention": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Inscription/scope statements with role and folio qualifiers",
        wikidata_properties=("P1684", "P7535", "P3831", "P958"),
        notes="Detailed intervention modeling remains in HMO.",
    ),
    "DigitalAccess": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Catalog, IIIF, and full-work URL statements",
        wikidata_properties=("P973", "P6108", "P953"),
        notes="Access resources are represented as URLs on Wikidata items.",
    ),
    "RightsDetermination": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Copyright-status claim when defensible",
        wikidata_properties=("P6216", "P1001"),
        notes="Rights reasoning remains in HMO; Wikidata receives conservative public-domain claims.",
    ),
    "Attribution": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="References, sourcing-circumstance qualifiers, and heuristic qualifiers",
        wikidata_properties=("P1480", "P887", "P248", "P854"),
        notes="The full evidence chain remains in HMO.",
    ),
    "EpistemologicalStatus": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Sourcing-circumstance and heuristic qualifiers",
        wikidata_properties=("P1480", "P887"),
        notes="Wikidata can expose claim uncertainty but not the full HMO status model.",
    ),
    "TextTradition": ProjectionStrategy(
        projection_status="hmo_or_wikibase_only",
        wikidata_representation="No default Wikidata representation",
        notes="Keep in HMO unless a tradition is independently notable or a future property exists.",
    ),
    "TransmissionWitness": ProjectionStrategy(
        projection_status="hmo_or_wikibase_only",
        wikidata_representation="No default Wikidata representation",
        notes="Potential future modeling through P144/P4969 only when concrete manuscript evidence exists.",
    ),
    "ParadigmBridge": ProjectionStrategy(
        projection_status="hmo_or_wikibase_only",
        wikidata_representation="No Wikidata representation",
        notes="Methodological/interpretive bridge nodes are intentionally HMO-only.",
    ),
    "PhilologicalView": ProjectionStrategy(
        projection_status="hmo_or_wikibase_only",
        wikidata_representation="No direct Wikidata item",
        notes="Wikidata carries references and qualifiers, not full HMO philological-view modeling.",
    ),
    # ── Phase 2 (Rule 44, 2026-05-17): coverage for previously-unmapped classes ──
    "AnthologyPosition": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Folio range and section qualifiers on the manuscript-work statement",
        wikidata_properties=("P958", "P478", "P1574"),
        notes="Position within an anthology is represented as qualifiers on P1574 (exemplar of).",
    ),
    "AnthologyStructure": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Part-count and has-parts statements on the manuscript item",
        wikidata_properties=("P2635", "P527"),
        notes="The structural model remains in HMO; Wikidata receives a part-count summary.",
    ),
    "SubjectType": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Genre and main-subject statements on the manuscript item",
        wikidata_properties=("P136", "P921"),
        notes="HMO subject types map to Wikidata P136 (genre) and P921 (main subject) QIDs.",
    ),
    "E52_Time-Span": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Date qualifiers and direct date statements (P571, P580, P582)",
        wikidata_properties=("P571", "P580", "P582", "P1319", "P1326"),
        notes="Time-span nodes are flattened into Wikidata date claims with calendar/precision qualifiers.",
    ),
    "E56_Language": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Language-of-work statements (P407) plus writing-system (P282)",
        wikidata_properties=("P407", "P282"),
        notes="Languages link to existing Wikidata language items via LANG_TO_QID.",
    ),
    "E57_Material": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P186 (material used) on the manuscript item",
        wikidata_properties=("P186",),
        notes="Material entities are linked to existing Wikidata material items via MATERIAL_TO_QID.",
    ),
    "F27_Work_Creation": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Date/place claims on the work item (P571, P1071)",
        wikidata_properties=("P571", "P1071", "P50"),
        notes="Work-creation events are collapsed into Wikidata work-item claims.",
    ),
    "CanonicalReference": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="Main-subject statements (P921) with Bible/Mishnah/Talmud/Halacha QIDs",
        wikidata_properties=("P921",),
        notes="Canonical references resolve via BIBLE_BOOK_TO_QID, TALMUD_TRACTATE_TO_QID, and related dictionaries.",
    ),
    "BiblicalReference": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P921 (main subject) with Bible book QID",
        wikidata_properties=("P921",),
        notes="Bible-book references map via BIBLE_BOOK_TO_QID.",
    ),
    "MishnaicReference": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P921 (main subject) with Mishnah tractate QID",
        wikidata_properties=("P921",),
        notes="Mishnah-tractate references map via the canonical subject mapping table.",
    ),
    "TalmudicReference": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P921 (main subject) with Talmud tractate QID",
        wikidata_properties=("P921",),
        notes="Talmud-tractate references map via TALMUD_TRACTATE_TO_QID.",
    ),
    "HalachicReference": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P921 (main subject) with halachic-work QID",
        wikidata_properties=("P921",),
        notes="Halachic-work references map via the canonical subject mapping table.",
    ),
    "Decoration": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P136 (genre) tagging illuminated/decorated works plus narrative notes",
        wikidata_properties=("P136", "P1684", "P7535"),
        notes="Detailed decoration taxonomy stays in HMO; Wikidata gets the illuminated-manuscript P31 + summary.",
    ),
    "CodicologicalHierarchy": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P527 (has parts) plus P2635 (number of parts)",
        wikidata_properties=("P527", "P2635"),
        notes="Nested codicological structure beyond two levels stays in HMO.",
    ),
    "ConditionType": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P5816 (state of conservation) with mapped condition QID",
        wikidata_properties=("P5816",),
        notes="Condition keywords map via CONDITION_TO_QID.",
    ),
    "HandChange": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P1684 (inscription) with P3831 role qualifier for the new hand",
        wikidata_properties=("P1684", "P3831"),
        notes="Detailed hand-change taxonomy stays in HMO; Wikidata gets the inscription with role.",
    ),
    "Marginalia": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P1684 (inscription) with P3831=Q1136474 (marginalia)",
        wikidata_properties=("P1684", "P3831"),
        notes="Marginalia text becomes P1684 with the marginalia role qualifier.",
    ),
    "MarginalAddition": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P1684 (inscription) with P3831 (marginalia) and folio qualifier when known",
        wikidata_properties=("P1684", "P3831", "P958"),
        notes="Marginal additions are represented as inscriptions with role and location qualifiers.",
    ),
    "TextCorrection": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P1684 (inscription) with P3831=Q3299332 (correction)",
        wikidata_properties=("P1684", "P3831"),
        notes="Correction text becomes P1684 with the correction role qualifier.",
    ),
    "TypeScriptType": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P9302 (script style) with the mapped script QID",
        wikidata_properties=("P9302",),
        notes="Script types map via SCRIPT_TYPE_TO_QID.",
    ),
    "HebrewScriptType": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P9302 (script style) with the Hebrew-script QID",
        wikidata_properties=("P9302",),
        notes="Hebrew script types map via SCRIPT_TYPE_TO_QID.",
    ),
    "ModeScriptType": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P9302 (script style) with mode-specific qualifier",
        wikidata_properties=("P9302",),
        notes="Script mode (square/semi-cursive/cursive) is documented in P7535 when not in P9302.",
    ),
    "CanonicalHierarchyType": ProjectionStrategy(
        projection_status="hmo_or_wikibase_only",
        wikidata_representation="No Wikidata projection",
        notes="Hierarchy classification metadata is HMO-internal.",
    ),
    "ParticipationRole": ProjectionStrategy(
        projection_status="summarized_in_wikidata",
        wikidata_representation="P3831 (object has role) qualifier on the participating-person statement",
        wikidata_properties=("P3831", "P50", "P11603", "P127"),
        notes="Roles map via ROLE_TO_PID and the occupation QIDs in property_mapping.",
    ),
}


def build_projection_coverage_report(
    ttl_path: Path,
    items: Sequence[WikidataItem],
) -> dict[str, object]:
    """Build a JSON-serializable report for RDF class projection coverage."""
    graph = Graph()
    graph.parse(ttl_path)

    item_type_counts = Counter(item.entity_type for item in items if item.entity_type)
    item_properties = _item_properties_by_type(items)
    class_counts = _rdf_class_counts(graph)
    entries = [
        _class_report_entry(
            graph=graph,
            class_uri=class_uri,
            hmo_node_count=count,
            item_type_counts=item_type_counts,
            item_properties=item_properties,
        )
        for class_uri, count in sorted(
            class_counts.items(),
            key=lambda entry: (_local_name(entry[0]), str(entry[0])),
        )
    ]

    return {
        "report_version": 1,
        "ttl_path": str(ttl_path),
        "strategy_source": _strategy_source(),
        "rdf_class_count": len(entries),
        "wikidata_item_count": len(items),
        "wikidata_item_counts_by_type": dict(sorted(item_type_counts.items())),
        "classes": entries,
    }


def write_projection_coverage_report(
    ttl_path: Path,
    items: Sequence[WikidataItem],
    output_path: Path,
) -> Path:
    """Write a deterministic projection-coverage JSON report and return its path."""
    report = build_projection_coverage_report(ttl_path, items)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _class_report_entry(
    graph: Graph,
    class_uri: URIRef,
    hmo_node_count: int,
    item_type_counts: Counter[str],
    item_properties: dict[str, tuple[str, ...]],
) -> dict[str, object]:
    """Build one class-level report row."""
    local_name = _local_name(class_uri)
    strategy = STRATEGY_BY_LOCAL_NAME.get(
        local_name,
        ProjectionStrategy(
            projection_status="unknown",
            wikidata_representation="No projection strategy recorded",
            notes="No stable HMO-to-Wikidata strategy is known for this class.",
        ),
    )
    properties = strategy.wikidata_properties
    projected_item_count: int | None = None
    if strategy.item_entity_type:
        projected_item_count = item_type_counts.get(strategy.item_entity_type, 0)
        properties = item_properties.get(strategy.item_entity_type, properties)

    return {
        "class_uri": str(class_uri),
        "class_label": _class_label(graph, class_uri, local_name),
        "class_local_name": local_name,
        "hmo_node_count": hmo_node_count,
        "projection_status": strategy.projection_status,
        "wikidata_representation": strategy.wikidata_representation,
        "wikidata_properties": list(properties),
        "projected_item_count": projected_item_count,
        "notes": strategy.notes,
    }


def _rdf_class_counts(graph: Graph) -> dict[URIRef, int]:
    """Count distinct RDF subjects for each URI class in the graph."""
    counts: dict[URIRef, set[object]] = {}
    for subject, class_uri in graph.subject_objects(RDF.type):
        if not isinstance(class_uri, URIRef):
            continue
        counts.setdefault(class_uri, set()).add(subject)
    return {class_uri: len(subjects) for class_uri, subjects in counts.items()}


def _item_properties_by_type(items: Iterable[WikidataItem]) -> dict[str, tuple[str, ...]]:
    """Return sorted statement PIDs grouped by WikidataItem.entity_type."""
    properties_by_type: dict[str, set[str]] = {}
    for item in items:
        if not item.entity_type:
            continue
        properties = properties_by_type.setdefault(item.entity_type, set())
        for statement in item.statements:
            if statement.property_id:
                properties.add(statement.property_id)
    return {
        entity_type: tuple(_sort_property_ids(properties))
        for entity_type, properties in properties_by_type.items()
    }


def _class_label(graph: Graph, class_uri: URIRef, local_name: str) -> str:
    """Return the class label when the graph has one; otherwise the local name."""
    label = graph.value(class_uri, RDFS.label)
    if isinstance(label, Literal) and str(label).strip():
        return str(label).strip()
    return local_name


def _local_name(uri: URIRef) -> str:
    """Extract a stable local name from a URIRef."""
    text = str(uri)
    if "#" in text:
        return text.rsplit("#", maxsplit=1)[-1]
    return text.rstrip("/").rsplit("/", maxsplit=1)[-1]


def _sort_property_ids(property_ids: Iterable[str]) -> list[str]:
    """Sort Wikidata PIDs by numeric property number, then by full string."""
    return sorted(property_ids, key=_property_sort_key)


def _property_sort_key(property_id: str) -> tuple[int, int | str]:
    """Return a natural sort key for Wikidata property identifiers."""
    match = re.fullmatch(r"P(\d+)", property_id)
    if match is not None:
        return (0, int(match.group(1)))
    return (1, property_id)


def _strategy_source() -> str:
    """Describe whether the source gap-analysis note is present in this checkout."""
    repo_root = Path(__file__).resolve().parents[2]
    gap_analysis = repo_root / "docs" / "wikidata" / "HMO_TO_WIKIDATA_GAP_ANALYSIS.md"
    if gap_analysis.exists():
        return str(gap_analysis)
    return "embedded HMO-to-Wikidata projection strategy"
