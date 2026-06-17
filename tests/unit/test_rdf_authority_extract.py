"""Regression tests for authority identifier parsing in GraphBuilder."""

from __future__ import annotations

from converter.rdf.graph_builder import GraphBuilder
from converter.transformer.uri_generator import UriGenerator


def test_extract_plain_viaf_and_wikidata_ids() -> None:
    gb = GraphBuilder(UriGenerator())
    parsed = gb._extract_authority_identifiers(["987654321", "Q127334"])
    assert parsed["viaf_id"] == "987654321"
    assert parsed["wikidata_id"] == "Q127334"
    assert "https://viaf.org/viaf/987654321" in parsed["same_as_uris"]
    assert "https://www.wikidata.org/entity/Q127334" in parsed["same_as_uris"]
