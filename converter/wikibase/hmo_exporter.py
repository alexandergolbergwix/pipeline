"""Export full HMO RDF graphs to offline project-Wikibase draft entities."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS
from rdflib.term import Node

from converter.config.namespaces import CIDOC, HM, LRMOO
from converter.wikibase.models import (
    StatementValue,
    WikibaseEntityDraft,
    WikibaseStatementDraft,
)

SKIPPED_SCHEMA_TYPES: frozenset[URIRef] = frozenset(
    {
        OWL.Class,
        OWL.ObjectProperty,
        OWL.DatatypeProperty,
        OWL.AnnotationProperty,
        RDF.Property,
    }
)

PREFERRED_CLASS_ORDER: tuple[URIRef, ...] = (
    LRMOO.F4_Manifestation_Singleton,
    LRMOO.F1_Work,
    LRMOO.F2_Expression,
    HM.Codicological_Unit,
    HM.TransmissionWitness,
    HM.TextTradition,
    HM.ParadigmBridge,
    HM.PhilologicalView,
    CIDOC.E21_Person,
    CIDOC.E53_Place,
    CIDOC.E74_Group,
    CIDOC.E12_Production,
    CIDOC.E8_Acquisition,
    HM.DigitalAccess,
)


class HmoWikibaseExporter:
    """Build offline Wikibase-ready drafts from canonical HMO Turtle output."""

    def from_ttl(self, ttl_path: Path) -> list[WikibaseEntityDraft]:
        """Parse a Turtle file and return local Wikibase entity drafts."""
        graph = Graph()
        graph.parse(ttl_path)
        return self.from_graph(graph)

    def from_graph(self, graph: Graph) -> list[WikibaseEntityDraft]:
        """Convert typed RDF nodes into full scholarly Wikibase drafts."""
        typed_nodes = _typed_instance_nodes(graph)
        local_ids = _local_ids_for_nodes(typed_nodes)

        drafts: list[WikibaseEntityDraft] = []
        for subject in typed_nodes:
            class_uri = _preferred_class_uri(graph, subject)
            statements = [
                _statement_from_triple(predicate, obj, local_ids)
                for predicate, obj in sorted(
                    graph.predicate_objects(subject),
                    key=lambda pair: (str(pair[0]), str(pair[1])),
                )
                if predicate not in {RDF.type, RDFS.label}
            ]
            drafts.append(
                WikibaseEntityDraft(
                    local_id=local_ids[subject],
                    labels=_labels_for_node(graph, subject),
                    descriptions={"en": f"Offline HMO Wikibase draft for {_local_name(class_uri)}"},
                    entity_type=_local_name(class_uri),
                    class_uri=str(class_uri),
                    source_uri=str(subject),
                    statements=statements,
                )
            )

        return sorted(drafts, key=lambda draft: draft.local_id)

    def export_json(self, entities: list[WikibaseEntityDraft]) -> str:
        """Serialise entity drafts as deterministic UTF-8 JSON text."""
        data = [entity.to_dict() for entity in entities]
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

    def export_json_to_file(
        self,
        entities: list[WikibaseEntityDraft],
        output_path: Path,
    ) -> Path:
        """Write entity drafts to a JSON file and return the output path."""
        output_path.write_text(self.export_json(entities), encoding="utf-8")
        return output_path


def _typed_instance_nodes(graph: Graph) -> list[URIRef | BNode]:
    """Return typed data nodes while skipping ontology/schema declarations."""
    nodes: set[URIRef | BNode] = set()
    for subject, class_uri in graph.subject_objects(RDF.type):
        if not isinstance(subject, URIRef | BNode):
            continue
        if not isinstance(class_uri, URIRef):
            continue
        if class_uri in SKIPPED_SCHEMA_TYPES:
            continue
        nodes.add(subject)
    return sorted(nodes, key=str)


def _local_ids_for_nodes(nodes: list[URIRef | BNode]) -> dict[URIRef | BNode, str]:
    """Create stable local Wikibase draft IDs for RDF resources."""
    seen: defaultdict[str, int] = defaultdict(int)
    local_ids: dict[URIRef | BNode, str] = {}
    for node in nodes:
        base = _safe_local_id(_node_local_name(node))
        seen[base] += 1
        suffix = "" if seen[base] == 1 else f"_{seen[base]}"
        local_ids[node] = f"QDraft_{base}{suffix}"
    return local_ids


def _preferred_class_uri(graph: Graph, subject: URIRef | BNode) -> URIRef:
    """Choose the most informative RDF class for a draft entity."""
    types = {
        class_uri
        for class_uri in graph.objects(subject, RDF.type)
        if isinstance(class_uri, URIRef) and class_uri not in SKIPPED_SCHEMA_TYPES
    }
    for preferred in PREFERRED_CLASS_ORDER:
        if preferred in types:
            return preferred
    if types:
        return sorted(types, key=str)[0]
    return URIRef(f"{HM}UnknownHmoEntity")


def _labels_for_node(graph: Graph, subject: URIRef | BNode) -> dict[str, str]:
    """Collect RDF labels, falling back to a readable URI local name."""
    labels: dict[str, str] = {}
    for label in graph.objects(subject, RDFS.label):
        if not isinstance(label, Literal):
            continue
        language = label.language or "und"
        labels.setdefault(language, str(label))
    if labels:
        return labels
    return {"en": _node_local_name(subject).replace("_", " ")}


def _statement_from_triple(
    predicate: Node,
    obj: Node,
    local_ids: dict[URIRef | BNode, str],
) -> WikibaseStatementDraft:
    """Convert an RDF predicate/object pair into a local statement draft."""
    property_uri = str(predicate)
    property_name = _local_name(predicate)
    if isinstance(obj, Literal):
        value, datatype = _literal_value(obj)
        return WikibaseStatementDraft(
            property_name=property_name,
            property_uri=property_uri,
            value=value,
            value_type="literal",
            datatype=datatype,
            language=obj.language,
        )
    if isinstance(obj, URIRef | BNode) and obj in local_ids:
        return WikibaseStatementDraft(
            property_name=property_name,
            property_uri=property_uri,
            value=local_ids[obj],
            value_type="entity",
            value_entity_id=local_ids[obj],
        )
    if isinstance(obj, URIRef):
        return WikibaseStatementDraft(
            property_name=property_name,
            property_uri=property_uri,
            value=str(obj),
            value_type="uri",
        )
    return WikibaseStatementDraft(
        property_name=property_name,
        property_uri=property_uri,
        value=str(obj),
        value_type="blank",
    )


def _literal_value(literal: Literal) -> tuple[StatementValue, str | None]:
    """Convert an RDF literal into a JSON-safe scalar plus datatype URI."""
    value = literal.toPython()
    datatype = str(literal.datatype) if literal.datatype is not None else None
    if isinstance(value, bool | int | float | str):
        return value, datatype
    return str(value), datatype


def _node_local_name(node: URIRef | BNode) -> str:
    """Return a readable local name for a URI or blank node."""
    if isinstance(node, BNode):
        return f"BlankNode_{node}"
    return _local_name(node)


def _local_name(node: Node) -> str:
    """Extract the final local component of an RDF URI-like node."""
    text = str(node)
    if "#" in text:
        return text.rsplit("#", maxsplit=1)[-1]
    if "/" in text:
        return text.rstrip("/").rsplit("/", maxsplit=1)[-1]
    return text


def _safe_local_id(value: str) -> str:
    """Normalise an RDF local name into a safe local draft identifier."""
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_")
    return cleaned or "Entity"
