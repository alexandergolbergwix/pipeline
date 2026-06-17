"""Wikidata projection coverage for golden ontology TTL."""

from __future__ import annotations

import json
from pathlib import Path

from converter.transformer.mapper import MarcToRdfMapper
from converter.wikidata.projection_coverage import build_projection_coverage_report

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures" / "ontology_golden"


def test_golden_ttl_projection_has_no_unknown_classes() -> None:
    records = json.loads((FIXTURE_DIR / "records.json").read_text(encoding="utf-8"))
    graph = MarcToRdfMapper().map_json_records(records)
    ttl_path = FIXTURE_DIR / "golden_projection.ttl"
    graph.serialize(destination=str(ttl_path), format="turtle")
    report = build_projection_coverage_report(ttl_path, items=[])
    unknown = [
        row for row in report["classes"]
        if row.get("projection_status") == "unknown"
    ]
    assert unknown == [], f"Unknown projection classes: {[r['class_local_name'] for r in unknown]}"
