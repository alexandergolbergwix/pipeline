"""Tests for HMO-to-Wikidata projection coverage reporting."""

from __future__ import annotations

import json
from pathlib import Path

from converter.wikidata.item_builder import WikidataItem, WikidataStatement
from converter.wikidata.projection_coverage import (
    build_projection_coverage_report,
    write_projection_coverage_report,
)


def test_projection_coverage_reports_direct_summarized_and_hmo_only_classes(
    tmp_path: Path,
) -> None:
    ttl_path = _write_tiny_hmo_graph(tmp_path)
    items = [
        WikidataItem(
            entity_type="manuscript",
            local_id="MS_1",
            statements=[
                WikidataStatement("P31", "Q87167", "item"),
                WikidataStatement("P195", "Q123", "item"),
            ],
        ),
        WikidataItem(
            entity_type="work",
            local_id="work:example",
            statements=[
                WikidataStatement("P31", "Q47461344", "item"),
                WikidataStatement("P50", "Q5", "item"),
            ],
        ),
    ]

    report = build_projection_coverage_report(ttl_path, items)
    classes = _classes_by_local_name(report)

    assert report["rdf_class_count"] == 5
    assert classes["F4_Manifestation_Singleton"]["hmo_node_count"] == 1
    assert classes["F4_Manifestation_Singleton"]["projection_status"] == ("direct_wikidata_item")
    assert classes["F4_Manifestation_Singleton"]["projected_item_count"] == 1
    assert classes["F4_Manifestation_Singleton"]["wikidata_properties"] == [
        "P31",
        "P195",
    ]
    assert classes["F1_Work"]["projection_status"] == "direct_wikidata_item"
    assert classes["F1_Work"]["projected_item_count"] == 1
    assert classes["Codicological_Unit"]["projection_status"] == ("summarized_in_wikidata")
    assert classes["Codicological_Unit"]["projected_item_count"] is None
    codicological_properties = classes["Codicological_Unit"]["wikidata_properties"]
    assert isinstance(codicological_properties, list)
    assert "P2635" in codicological_properties
    assert classes["TransmissionWitness"]["projection_status"] == ("hmo_or_wikibase_only")
    assert classes["UnmappedScholarlyNode"]["projection_status"] == "unknown"


def test_write_projection_coverage_report_returns_path_and_writes_json(
    tmp_path: Path,
) -> None:
    ttl_path = _write_tiny_hmo_graph(tmp_path)
    output_path = tmp_path / "reports" / "wikidata_projection_coverage.json"

    written_path = write_projection_coverage_report(ttl_path, [], output_path)

    assert written_path == output_path
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["wikidata_item_count"] == 0
    assert isinstance(payload["classes"], list)


def _write_tiny_hmo_graph(tmp_path: Path) -> Path:
    ttl_path = tmp_path / "output.ttl"
    ttl_path.write_text(
        """
        @prefix hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#> .
        @prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

        hm:MS_1
            a lrmoo:F4_Manifestation_Singleton ;
            rdfs:label "Test manuscript"@en .

        hm:Work_1
            a lrmoo:F1_Work ;
            rdfs:label "Test work"@en .

        hm:CU_1
            a hm:Codicological_Unit ;
            rdfs:label "Codicological unit 1"@en .

        hm:Witness_1
            a hm:TransmissionWitness ;
            rdfs:label "Transmission witness 1"@en .

        hm:Unknown_1
            a hm:UnmappedScholarlyNode ;
            rdfs:label "Unknown scholarly node"@en .
        """,
        encoding="utf-8",
    )
    return ttl_path


def _classes_by_local_name(
    report: dict[str, object],
) -> dict[str, dict[str, object]]:
    classes = report["classes"]
    if not isinstance(classes, list):
        raise TypeError("classes must be a list")
    result: dict[str, dict[str, object]] = {}
    for entry in classes:
        if not isinstance(entry, dict):
            raise TypeError("class entry must be a dictionary")
        local_name = entry["class_local_name"]
        if not isinstance(local_name, str):
            raise TypeError("class_local_name must be a string")
        result[local_name] = entry
    return result
