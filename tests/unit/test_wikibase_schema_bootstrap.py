"""Tests for offline HMO Wikibase schema bootstrap drafts."""

from __future__ import annotations

import json
from pathlib import Path

from converter.wikibase.schema_bootstrap import (
    build_default_hmo_schema_bootstrap,
    export_schema_bootstrap_to_file,
)


def test_default_bootstrap_contains_core_hmo_classes() -> None:
    bootstrap = build_default_hmo_schema_bootstrap()
    labels = {schema_class.label for schema_class in bootstrap.classes}

    assert labels == {
        "Manuscript",
        "Work",
        "Expression",
        "Codicological Unit",
        "Person",
        "Place",
        "Organization",
        "Event",
        "Transmission Witness",
        "Text Tradition",
    }


def test_default_bootstrap_contains_practical_first_properties() -> None:
    bootstrap = build_default_hmo_schema_bootstrap()
    properties_by_label = {schema_property.label: schema_property for schema_property in bootstrap.properties}

    for label in {
        "instance of",
        "label",
        "source URI",
        "local HMO URI",
        "has part",
        "part of",
        "carried by",
        "embodies",
        "created by",
        "associated person",
        "associated place",
        "associated date",
        "authority ID",
        "external identifier",
        "evidence/source note",
    }:
        assert label in properties_by_label

    assert properties_by_label["instance of"].datatype == "wikibase-item"
    assert properties_by_label["authority ID"].datatype == "external-id"
    assert properties_by_label["associated date"].datatype == "time"


def test_bootstrap_to_dict_has_json_ready_shape() -> None:
    data = build_default_hmo_schema_bootstrap().to_dict()

    assert set(data) == {"classes", "properties", "notes"}
    assert isinstance(data["classes"], list)
    assert isinstance(data["properties"], list)
    assert isinstance(data["notes"], list)

    first_class = data["classes"][0]
    first_property = data["properties"][0]

    assert isinstance(first_class, dict)
    assert set(first_class) == {"local_id", "label", "description", "source_uri", "aliases"}
    assert isinstance(first_property, dict)
    assert {
        "local_id",
        "label",
        "description",
        "datatype",
        "source_uri",
        "aliases",
    }.issubset(first_property)


def test_export_schema_bootstrap_to_file_writes_json(tmp_path: Path) -> None:
    output_path = tmp_path / "hmo_schema_bootstrap.json"

    returned_path = export_schema_bootstrap_to_file(output_path)
    parsed = json.loads(output_path.read_text(encoding="utf-8"))

    assert returned_path == output_path
    assert parsed["classes"][0]["label"] == "Manuscript"
    assert any(entry["label"] == "Transmission Witness" for entry in parsed["classes"])
    assert any(entry["label"] == "evidence/source note" for entry in parsed["properties"])
    assert "Offline schema bootstrap only" in parsed["notes"][0]
