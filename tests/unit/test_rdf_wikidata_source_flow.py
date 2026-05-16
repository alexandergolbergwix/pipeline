"""Tests for reviewed-authority RDF flow and HMO-driven Wikidata projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from converter.config.namespaces import LRMOO
from converter.transformer.mapper import MarcToRdfMapper
from rdflib.namespace import RDFS


def test_stage4_prefers_reviewed_authority_json(tmp_path: Path) -> None:
    from converter.transformer.mapper import select_rdf_source_path

    raw = tmp_path / "authority_enriched.json"
    reviewed = tmp_path / "authority_enriched_reviewed.json"
    raw.write_text("[]", encoding="utf-8")
    reviewed.write_text("[]", encoding="utf-8")

    selected, marker = select_rdf_source_path(raw)

    assert selected == reviewed
    assert marker == "user-reviewed authority enriched"


def test_stage4_falls_back_to_raw_authority_json(tmp_path: Path) -> None:
    from converter.transformer.mapper import select_rdf_source_path

    raw = tmp_path / "authority_enriched.json"
    raw.write_text("[]", encoding="utf-8")

    selected, marker = select_rdf_source_path(raw)

    assert selected == raw
    assert marker == "raw authority enriched"


def test_stage4_rejects_wikidata_verified_json(tmp_path: Path) -> None:
    from converter.transformer.mapper import select_rdf_source_path

    verified = tmp_path / "authority_enriched_wikidata_verified.json"
    verified.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="Wikidata Studio review file"):
        select_rdf_source_path(verified)


def test_stage4_rejects_wikidata_review_shape(tmp_path: Path) -> None:
    from converter.transformer.mapper import validate_rdf_json_records

    verified = tmp_path / "manual_name.json"
    data = [{"item": {"local_id": "x"}, "validation": {"status": "new"}}]

    with pytest.raises(ValueError, match="Wikidata Studio review file"):
        validate_rdf_json_records(data, verified)


def test_stage4_mapper_builds_from_reviewed_json_when_available(tmp_path: Path) -> None:
    raw = tmp_path / "authority_enriched.json"
    reviewed = tmp_path / "authority_enriched_reviewed.json"
    raw.write_text(
        json.dumps(
            [{"_control_number": "990000000003205171", "title": "כותרת גולמית"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reviewed.write_text(
        json.dumps(
            [{"_control_number": "990000000003205171", "title": "כותרת מאושרת"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    graph = MarcToRdfMapper().map_file(raw)
    manuscript = next(graph.subjects(predicate=None, object=LRMOO.F4_Manifestation_Singleton))
    labels = {str(label) for label in graph.objects(manuscript, RDFS.label)}

    assert "כותרת מאושרת" in labels
    assert "כותרת גולמית" not in labels


def test_hmo_crosswalk_uses_reviewed_sidecar(tmp_path: Path) -> None:
    from converter.wikidata.hmo_crosswalk import build_items_from_hmo_ttl

    ttl = tmp_path / "output.ttl"
    ttl.write_text(
        """
        @prefix hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#> .
        @prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

        hm:MS_990000000000205171
            a lrmoo:F4_Manifestation_Singleton ;
            hm:external_identifier_nli "990000000000205171" ;
            rdfs:label "כתב יד לדוגמה"@he .
        """,
        encoding="utf-8",
    )
    reviewed = tmp_path / "authority_enriched_reviewed.json"
    reviewed.write_text(
        json.dumps(
            [
                {
                    "_control_number": "990000000000205171",
                    "title": "כתב יד לדוגמה",
                    "shelfmark": "Ms. test 1",
                    "dates": {"year": 1600},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_items_from_hmo_ttl(ttl)

    assert result.sidecar_path == reviewed
    assert result.provenance_marker == "HMO RDF + user-reviewed authority enriched"
    assert any(item.local_id == "990000000000205171" for item in result.items)


def test_hmo_crosswalk_has_rdf_only_fallback(tmp_path: Path) -> None:
    from converter.wikidata.hmo_crosswalk import build_items_from_hmo_ttl

    ttl = tmp_path / "output.ttl"
    ttl.write_text(
        """
        @prefix hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#> .
        @prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

        hm:MS_990000000001205171
            a lrmoo:F4_Manifestation_Singleton ;
            hm:external_identifier_nli "990000000001205171" ;
            hm:ownership_history "Owned by example collector." ;
            rdfs:label "כתב יד נוסף"@he .
        """,
        encoding="utf-8",
    )

    result = build_items_from_hmo_ttl(ttl)

    assert result.sidecar_path is None
    assert result.provenance_marker == "HMO RDF"
    assert any(item.local_id == "990000000001205171" for item in result.items)
