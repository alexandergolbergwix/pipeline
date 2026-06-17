"""SHACL validation for the golden ontology corpus TTL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures" / "ontology_golden"
ONTOLOGY_PATH = REPO_ROOT / "ontology" / "hebrew-manuscripts.ttl"
SHAPES_PATH = REPO_ROOT / "ontology" / "shacl-shapes.ttl"


@pytest.fixture(scope="module")
def golden_ttl_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from converter.transformer.mapper import MarcToRdfMapper

    records = json.loads((FIXTURE_DIR / "records.json").read_text(encoding="utf-8"))
    graph = MarcToRdfMapper().map_json_records(records)
    out = tmp_path_factory.mktemp("golden") / "golden.ttl"
    graph.serialize(destination=str(out), format="turtle")
    return out


def test_golden_corpus_shacl_conforms(golden_ttl_path: Path) -> None:
    pyshacl = pytest.importorskip("pyshacl")
    from rdflib import Graph

    data = Graph()
    data.parse(golden_ttl_path, format="turtle")
    shapes = Graph()
    shapes.parse(ONTOLOGY_PATH, format="turtle")
    shapes.parse(SHAPES_PATH, format="turtle")
    conforms, report_graph, report_text = pyshacl.validate(
        data_graph=data,
        shacl_graph=shapes,
        inference="rdfs",
        abort_on_first=False,
    )
    assert conforms is True, report_text
