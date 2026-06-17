"""Unit tests for HMO ontology RDF coverage measurement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from converter.config.namespaces import HM, bind_namespaces
from converter.rdf.ontology_coverage import (
    analyze_graph_coverage,
    build_coverage_report,
    check_against_baseline,
    load_ontology_terms,
)
from converter.transformer.mapper import MarcToRdfMapper
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD

REPO_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_PATH = REPO_ROOT / "ontology" / "hebrew-manuscripts.ttl"
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures" / "ontology_golden"
EXPECTED_PATH = FIXTURE_DIR / "expected_coverage.json"


def _golden_graph() -> Graph:
    records = json.loads((FIXTURE_DIR / "records.json").read_text(encoding="utf-8"))
    mapper = MarcToRdfMapper()
    graph = mapper.map_json_records(records)
    bind_namespaces(graph)
    return graph


class TestOntologyInventory:
    def test_loads_seventy_three_classes(self) -> None:
        inv = load_ontology_terms(ONTOLOGY_PATH)
        assert inv.class_count == 73
        assert inv.property_count >= 238

    def test_inventory_contains_manuscript_view(self) -> None:
        inv = load_ontology_terms(ONTOLOGY_PATH)
        assert "ManuscriptView" in inv.classes


class TestCoverageLogic:
    def test_class_covered_when_mentioned_not_only_typed(self) -> None:
        inv = load_ontology_terms(ONTOLOGY_PATH)
        graph = Graph()
        bind_namespaces(graph)
        ms = URIRef(f"{HM}MS_test")
        ind = URIRef(f"{HM}Stemma_test")
        graph.add((ind, RDF.type, HM.StemmaHypothesis))
        graph.add((ms, RDFS.seeAlso, ind))
        report = analyze_graph_coverage(graph, inv)
        stemma = next(t for t in report.terms if t.local_name == "StemmaHypothesis")
        assert stemma.status == "covered"
        assert stemma.mentioned is True
        assert stemma.used_as_type is True

    def test_property_covered_when_used_as_predicate(self) -> None:
        inv = load_ontology_terms(ONTOLOGY_PATH)
        graph = Graph()
        bind_namespaces(graph)
        ms = URIRef(f"{HM}MS_test")
        graph.add((ms, HM.has_title, Literal("test", lang="he")))
        report = analyze_graph_coverage(graph, inv)
        title = next(t for t in report.terms if t.local_name == "has_title")
        assert title.status == "covered"
        assert title.used_as_predicate is True


class TestGoldenFixtureCoverage:
    def test_golden_build_meets_expected_baseline(self) -> None:
        graph = _golden_graph()
        report = build_coverage_report(None, ONTOLOGY_PATH, graph=graph)
        baseline = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
        errors = check_against_baseline(report, baseline)
        assert errors == []
        assert report.classes_covered == baseline["min_classes"]
        assert report.properties_covered >= baseline["min_properties"]

    def test_golden_build_has_no_missing_terms(self) -> None:
        graph = _golden_graph()
        report = build_coverage_report(None, ONTOLOGY_PATH, graph=graph)
        assert report.missing_classes == []
        assert report.missing_properties == []
