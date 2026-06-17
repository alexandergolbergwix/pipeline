"""Desktop vs golden fixture class/property parity smoke test."""

from __future__ import annotations

import json
from pathlib import Path

from converter.rdf.ontology_coverage import build_coverage_report
from converter.transformer.mapper import MarcToRdfMapper

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures" / "ontology_golden"
ONTOLOGY_PATH = REPO_ROOT / "ontology" / "hebrew-manuscripts.ttl"


def test_desktop_mapper_matches_golden_coverage_set() -> None:
    records = json.loads((FIXTURE_DIR / "records.json").read_text(encoding="utf-8"))
    graph = MarcToRdfMapper().map_json_records(records)
    report = build_coverage_report(None, ONTOLOGY_PATH, graph=graph)
    assert report.classes_covered == report.inventory.class_count
    assert report.properties_covered == report.inventory.property_count
