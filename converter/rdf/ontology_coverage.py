"""Measure HMO ontology class/property coverage in generated RDF graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF

from ..config.namespaces import HM

CoverageStatus = Literal["covered", "not_covered"]


@dataclass(frozen=True)
class TermCoverage:
    """Coverage status for one ontology term."""

    uri: str
    local_name: str
    term_kind: Literal["class", "object_property", "datatype_property"]
    used_as_type: bool = False
    used_as_predicate: bool = False
    mentioned: bool = False
    status: CoverageStatus = "not_covered"


@dataclass
class OntologyInventory:
    """Declared hm: terms from the ontology TTL."""

    namespace: str
    classes: dict[str, URIRef] = field(default_factory=dict)
    object_properties: dict[str, URIRef] = field(default_factory=dict)
    datatype_properties: dict[str, URIRef] = field(default_factory=dict)

    @property
    def class_count(self) -> int:
        return len(self.classes)

    @property
    def object_property_count(self) -> int:
        return len(self.object_properties)

    @property
    def datatype_property_count(self) -> int:
        return len(self.datatype_properties)

    @property
    def property_count(self) -> int:
        return self.object_property_count + self.datatype_property_count


@dataclass
class CoverageReport:
    """Coverage analysis result."""

    inventory: OntologyInventory
    terms: list[TermCoverage] = field(default_factory=list)

    @property
    def classes_covered(self) -> int:
        return sum(1 for t in self.terms if t.term_kind == "class" and t.status == "covered")

    @property
    def object_properties_covered(self) -> int:
        return sum(
            1 for t in self.terms
            if t.term_kind == "object_property" and t.status == "covered"
        )

    @property
    def datatype_properties_covered(self) -> int:
        return sum(
            1 for t in self.terms
            if t.term_kind == "datatype_property" and t.status == "covered"
        )

    @property
    def properties_covered(self) -> int:
        return self.object_properties_covered + self.datatype_properties_covered

    @property
    def missing_classes(self) -> list[str]:
        return sorted(
            t.local_name for t in self.terms
            if t.term_kind == "class" and t.status == "not_covered"
        )

    @property
    def missing_properties(self) -> list[str]:
        return sorted(
            t.local_name for t in self.terms
            if t.term_kind != "class" and t.status == "not_covered"
        )

    def to_dict(self) -> dict[str, object]:
        inv = self.inventory
        return {
            "report_version": 1,
            "namespace": inv.namespace,
            "classes_covered": self.classes_covered,
            "classes_total": inv.class_count,
            "object_properties_covered": self.object_properties_covered,
            "object_properties_total": inv.object_property_count,
            "datatype_properties_covered": self.datatype_properties_covered,
            "datatype_properties_total": inv.datatype_property_count,
            "properties_covered": self.properties_covered,
            "properties_total": inv.property_count,
            "missing_classes": self.missing_classes,
            "missing_properties": self.missing_properties,
            "terms": [
                {
                    "uri": t.uri,
                    "local_name": t.local_name,
                    "term_kind": t.term_kind,
                    "used_as_type": t.used_as_type,
                    "used_as_predicate": t.used_as_predicate,
                    "mentioned": t.mentioned,
                    "status": t.status,
                }
                for t in self.terms
            ],
        }


def _local_name(uri: URIRef | str, namespace: str) -> str:
    text = str(uri)
    if text.startswith(namespace):
        return text[len(namespace):]
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rsplit("/", 1)[-1]


def load_ontology_terms(ontology_path: Path) -> OntologyInventory:
    """Parse ontology TTL and collect declared hm: classes and properties."""
    graph = Graph()
    graph.parse(ontology_path, format="turtle")
    ns = str(HM)
    inventory = OntologyInventory(namespace=ns)

    for subject, predicate, obj in graph:
        if predicate != RDF.type or not isinstance(subject, URIRef):
            continue
        if not str(subject).startswith(ns):
            continue
        name = _local_name(subject, ns)
        if obj == OWL.Class:
            inventory.classes[name] = subject
        elif obj == OWL.ObjectProperty:
            inventory.object_properties[name] = subject
        elif obj == OWL.DatatypeProperty:
            inventory.datatype_properties[name] = subject

    return inventory


def analyze_graph_coverage(graph: Graph, inventory: OntologyInventory) -> CoverageReport:
    """Compare a data graph against the ontology inventory."""
    ns = inventory.namespace
    used_types: set[str] = set()
    used_predicates: set[str] = set()
    mentioned: set[str] = set()

    for subject, predicate, obj in graph:
        if isinstance(predicate, URIRef) and str(predicate).startswith(ns):
            used_predicates.add(_local_name(predicate, ns))
        for term in (subject, obj):
            if isinstance(term, URIRef) and str(term).startswith(ns):
                mentioned.add(_local_name(term, ns))
        if predicate == RDF.type and isinstance(obj, URIRef) and str(obj).startswith(ns):
            used_types.add(_local_name(obj, ns))

    terms: list[TermCoverage] = []
    for name, uri in sorted(inventory.classes.items()):
        used_as_type = name in used_types
        is_mentioned = name in mentioned
        covered = used_as_type or is_mentioned
        terms.append(TermCoverage(
            uri=str(uri),
            local_name=name,
            term_kind="class",
            used_as_type=used_as_type,
            mentioned=is_mentioned,
            status="covered" if covered else "not_covered",
        ))

    for kind, mapping in (
        ("object_property", inventory.object_properties),
        ("datatype_property", inventory.datatype_properties),
    ):
        for name, uri in sorted(mapping.items()):
            used_as_predicate = name in used_predicates
            terms.append(TermCoverage(
                uri=str(uri),
                local_name=name,
                term_kind=kind,
                used_as_predicate=used_as_predicate,
                status="covered" if used_as_predicate else "not_covered",
            ))

    return CoverageReport(inventory=inventory, terms=terms)


def build_coverage_report_from_graph(
    graph: Graph,
    ontology_path: Path,
) -> CoverageReport:
    """Load ontology and analyze graph coverage."""
    inventory = load_ontology_terms(ontology_path)
    return analyze_graph_coverage(graph, inventory)


def build_coverage_report(
    ttl_path: Path | None,
    ontology_path: Path,
    graph: Graph | None = None,
) -> CoverageReport:
    """Build a coverage report from TTL path or an in-memory graph."""
    if graph is None:
        if ttl_path is None:
            raise ValueError("Either ttl_path or graph must be provided")
        graph = Graph()
        graph.parse(ttl_path, format="turtle")
    return build_coverage_report_from_graph(graph, ontology_path)


def write_coverage_report(
    report: CoverageReport,
    output_path: Path,
) -> Path:
    """Serialize coverage report to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def check_against_baseline(
    report: CoverageReport,
    baseline: dict[str, object],
) -> list[str]:
    """Return human-readable regression messages (empty if OK)."""
    errors: list[str] = []
    min_classes = int(baseline.get("min_classes", 0))
    min_properties = int(baseline.get("min_properties", 0))
    if report.classes_covered < min_classes:
        errors.append(
            f"classes {report.classes_covered}/{report.inventory.class_count} "
            f"< baseline min {min_classes}"
        )
    if report.properties_covered < min_properties:
        errors.append(
            f"properties {report.properties_covered}/{report.inventory.property_count} "
            f"< baseline min {min_properties}"
        )
    required_missing = baseline.get("required_missing_empty")
    if required_missing is True:
        if report.missing_classes:
            errors.append(f"missing classes: {', '.join(report.missing_classes[:10])}")
        if report.missing_properties:
            errors.append(
                f"missing properties: {', '.join(report.missing_properties[:10])}"
            )
    return errors
