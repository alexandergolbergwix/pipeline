"""Unit tests for converter.rdf.rdf_helpers."""

from __future__ import annotations

from converter.rdf.rdf_helpers import (
    clean_marc_label,
    infer_person_type,
    is_plausible_coords,
    names_overlap,
    normalize_role,
)


def test_clean_marc_label_strips_quotes() -> None:
    assert clean_marc_label('"משה בן מיימון"') == "משה בן מיימון"


def test_normalize_role_maps_current_owner() -> None:
    assert normalize_role('"current owner"') == "current_owner"
    assert normalize_role("former owner") == "former_owner"


def test_infer_person_type_organization_from_field() -> None:
    assert infer_person_type({"name": "ספרייה לאומית", "field": "710"}) == "organization"


def test_is_plausible_coords_rejects_zero_island() -> None:
    assert not is_plausible_coords(0.0, 0.0)


def test_names_overlap_bidirectional() -> None:
    assert names_overlap("משה בן מיימון", "מיימון")
    assert names_overlap("Moses Maimonides", "Maimonides")
