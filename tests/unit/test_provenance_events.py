"""Phase 1 — structured non-production provenance events (CLAUDE.md Rule 60).

Covers the desktop extraction half:
  - FieldHandlers.handle_541_structured / handle_583_structured subfields
  - FieldHandlers._city_from_address (no-NER postal-address city extraction)
  - FieldHandlers.build_provenance_event (place required, date reuse, certainty)
  - extract_all_data populates ExtractedData.provenance_events
  - integrity: no event fabricated when the source subfields are absent
"""
from __future__ import annotations

from converter.parser.marc_reader import MarcField, MarcRecord
from converter.transformer.field_handlers import FieldHandlers, extract_all_data


def _rec(control_number: str = "CN1", **fields: MarcField) -> MarcRecord:
    return MarcRecord(
        control_number=control_number,
        fields={tag: [f] for tag, f in fields.items()},
    )


# ── _city_from_address ──────────────────────────────────────────────────────

class TestCityFromAddress:
    def test_street_dropped_city_kept(self) -> None:
        assert (
            FieldHandlers._city_from_address("458 Yonkers Road, Poughkeepsie, NY 12601")
            == "Poughkeepsie"
        )

    def test_country_stepped_back_to_city(self) -> None:
        assert FieldHandlers._city_from_address("Zurich, Switzerland") == "Zurich"

    def test_dealer_then_city(self) -> None:
        assert FieldHandlers._city_from_address("Sotheby's, London") == "London"

    def test_bare_city(self) -> None:
        assert FieldHandlers._city_from_address("Jerusalem") == "Jerusalem"

    def test_pure_number_is_none(self) -> None:
        assert FieldHandlers._city_from_address("12345") is None

    def test_empty_and_none(self) -> None:
        assert FieldHandlers._city_from_address("") is None
        assert FieldHandlers._city_from_address(None) is None


# ── handle_541_structured / handle_583_structured ────────────────────────────

class TestStructuredHandlers:
    def test_handle_541_structured_reads_subfields(self) -> None:
        f = MarcField(
            tag="541",
            subfields={"a": ["Braginsky Collection"], "b": ["Zurich, Switzerland"],
                       "c": ["purchase"], "d": ["1985"]},
        )
        out = FieldHandlers.handle_541_structured(f)
        assert out == {
            "source": "Braginsky Collection",
            "address": "Zurich, Switzerland",
            "method": "purchase",
            "date": "1985",
        }

    def test_handle_583_structured_reads_site(self) -> None:
        f = MarcField(
            tag="583",
            subfields={"a": ["conserved"], "j": ["Jerusalem"],
                       "h": ["NLI"], "c": ["2010"]},
        )
        out = FieldHandlers.handle_583_structured(f)
        assert out["site"] == "Jerusalem"
        assert out["jurisdiction"] == "NLI"
        assert out["date"] == "2010"


# ── build_provenance_event ───────────────────────────────────────────────────

class TestBuildProvenanceEvent:
    def test_place_required(self) -> None:
        assert FieldHandlers.build_provenance_event(
            event_type="acquisition", place_text="", agent_name="x",
            date_str="1900", source_field="541",
        ) is None

    def test_date_parsed_and_certain(self) -> None:
        ev = FieldHandlers.build_provenance_event(
            event_type="acquisition", place_text="Zurich", agent_name="Braginsky",
            date_str="1985", source_field="541",
        )
        assert ev["year"] == 1985
        assert ev["year_earliest"] == 1985 and ev["year_latest"] == 1985
        assert ev["certain"] is True
        assert ev["lat"] is None and ev["lon"] is None  # never fabricated

    def test_no_date_uncertain(self) -> None:
        ev = FieldHandlers.build_provenance_event(
            event_type="exhibition", place_text="Oxford", agent_name=None,
            date_str=None, source_field="583",
        )
        assert ev["year"] is None
        assert ev["certain"] is False  # Footprints rule: place kept, date uncertain


# ── extract_all_data integration ─────────────────────────────────────────────

class TestExtractAllDataProvenanceEvents:
    def test_acquisition_event_from_541(self) -> None:
        rec = _rec(
            **{"541": MarcField(tag="541", subfields={
                "a": ["Braginsky Collection"], "b": ["Zurich, Switzerland"], "d": ["1985"],
            })}
        )
        data = extract_all_data(rec)
        evs = [e for e in data.provenance_events if e["type"] == "acquisition"]
        assert len(evs) == 1
        assert evs[0]["place_text"] == "Zurich"
        assert evs[0]["year"] == 1985

    def test_conservation_and_exhibition_from_583(self) -> None:
        rec = MarcRecord(
            control_number="CN2",
            fields={"583": [
                MarcField(tag="583", subfields={"a": ["conserved"], "j": ["Jerusalem"]}),
                MarcField(tag="583", subfields={"a": ["exhibited"], "j": ["New York"], "c": ["1999"]}),
            ]},
        )
        data = extract_all_data(rec)
        by_type = {e["type"]: e for e in data.provenance_events}
        assert by_type["conservation"]["place_text"] == "Jerusalem"
        assert by_type["exhibition"]["place_text"] == "New York"
        assert by_type["exhibition"]["year"] == 1999

    def test_no_event_when_subfields_absent(self) -> None:
        # 541 with only a source name (no $b address) and no 583 → no events.
        rec = _rec(**{"541": MarcField(tag="541", subfields={"a": ["Some donor"]})})
        data = extract_all_data(rec)
        assert data.provenance_events == []


# ── Ashkenazi gazetteer (desktop mirror) ─────────────────────────────────────

class TestAshkenaziGazetteer:
    def test_lookup_english_and_hebrew(self) -> None:
        from converter.authority.ashkenazi_gazetteer import lookup

        assert lookup("Prague")["wikidata_id"] == "Q1085"
        assert round(lookup("פראג")["lat"], 2) == 50.09

    def test_wikidata_uri_only_with_qid(self) -> None:
        from converter.authority.ashkenazi_gazetteer import wikidata_uri

        assert wikidata_uri("פראג") == "https://www.wikidata.org/entity/Q1085"
        assert wikidata_uri("Worms") is None  # ships without a verified QID
        assert wikidata_uri("Tokyo") is None


# ── RDF emission (desktop graph builder) ──────────────────────────────────────

class TestGraphBuilderProvenanceEvents:
    def _build(self, events: list[dict]):
        from converter.transformer.field_handlers import ExtractedData
        from converter.transformer.mapper import MarcToRdfMapper

        ex = ExtractedData()
        ex.control_number = "CN1"
        ex.title = "Test MS"
        ex.provenance_events = events
        return MarcToRdfMapper().graph_builder.build_graph(ex, "CN1")

    def test_geo_event_emits_wgs84_and_cidoc(self) -> None:
        g = self._build([
            {"type": "acquisition", "place_text": "Zurich", "lat": 47.37,
             "lon": 8.54, "wikidata_id": "Q72", "year": 1985, "source_field": "541"},
        ])
        ttl = g.serialize(format="turtle")
        assert "has_provenance_event" in ttl
        assert "E8_Acquisition" in ttl
        assert "47.37" in ttl and "8.54" in ttl

    def test_no_coords_no_event(self) -> None:
        g = self._build([
            {"type": "conservation", "place_text": "Nowhere", "lat": None,
             "lon": None, "source_field": "583"},
        ])
        assert "has_provenance_event" not in g.serialize(format="turtle")
