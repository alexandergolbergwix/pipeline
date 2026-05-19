"""Tests for offline HMO-to-project-Wikibase export drafts."""

from __future__ import annotations

import json
from pathlib import Path

from converter.config.namespaces import CIDOC, HM, LRMOO
from converter.wikibase import HmoWikibaseExporter, LocalQuickStatementsExporter
from converter.wikibase.models import WikibaseEntityDraft
from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS


def test_hmo_exporter_keeps_cu_and_transmission_witness_as_first_class_drafts() -> None:
    graph = _sample_hmo_graph()

    drafts = HmoWikibaseExporter().from_graph(graph)
    drafts_by_type = {draft.entity_type: draft for draft in drafts}

    assert "Codicological_Unit" in drafts_by_type
    assert "TransmissionWitness" in drafts_by_type
    assert drafts_by_type["Codicological_Unit"].class_uri == str(HM.Codicological_Unit)
    assert drafts_by_type["TransmissionWitness"].class_uri == str(HM.TransmissionWitness)


def test_hmo_exporter_preserves_literal_and_object_statements() -> None:
    graph = _sample_hmo_graph()

    drafts = HmoWikibaseExporter().from_graph(graph)
    manuscript = _draft_with_type(drafts, "F4_Manifestation_Singleton")
    cu = _draft_with_type(drafts, "Codicological_Unit")

    assert manuscript.labels == {"he": "כתב יד לדוגמה"}
    assert any(
        statement.property_name == "is_composed_of"
        and statement.value_type == "entity"
        and statement.value_entity_id == cu.local_id
        for statement in manuscript.statements
    )
    assert any(
        statement.property_name == "extent_folios"
        and statement.value == 12
        and statement.value_type == "literal"
        for statement in cu.statements
    )


def test_hmo_exporter_reads_ttl_and_exports_json(tmp_path: Path) -> None:
    ttl_path = tmp_path / "output.ttl"
    _sample_hmo_graph().serialize(destination=ttl_path, format="turtle")

    exporter = HmoWikibaseExporter()
    drafts = exporter.from_ttl(ttl_path)
    json_text = exporter.export_json(drafts)
    parsed = json.loads(json_text)

    assert isinstance(parsed, list)
    assert any(entry["entity_type"] == "TextTradition" for entry in parsed)


def test_local_quickstatements_export_is_offline_tsv() -> None:
    drafts = HmoWikibaseExporter().from_graph(_sample_hmo_graph())

    text = LocalQuickStatementsExporter().export(drafts)

    assert "# Offline only" in text
    assert "CREATE\tQDraft_MS_1" in text
    assert "\tP31\t<http://iflastandards.info/ns/lrm/lrmoo/F4_Manifestation_Singleton>" in text
    assert "\tis_composed_of\tQDraft_CU_1\t" in text


def _sample_hmo_graph() -> Graph:
    graph = Graph()
    manuscript = HM.MS_1
    cu = HM.CU_1
    work = HM.Work_1
    expression = HM.Expression_1
    tradition = HM.Tradition_1
    witness = HM.Witness_1
    bridge = HM.Bridge_1
    view = HM.PhilologicalView_1
    production = HM.Production_1
    access = HM.DigitalAccess_1

    graph.add((manuscript, RDF.type, LRMOO.F4_Manifestation_Singleton))
    graph.add((manuscript, RDFS.label, Literal("כתב יד לדוגמה", lang="he")))
    graph.add((manuscript, HM.is_composed_of, cu))
    graph.add((manuscript, HM.has_digital_access, access))
    graph.add((manuscript, HM.has_production_event, production))

    graph.add((cu, RDF.type, HM.Codicological_Unit))
    graph.add((cu, RDFS.label, Literal("Codicological unit 1", lang="en")))
    graph.add((cu, HM.extent_folios, Literal(12)))

    graph.add((work, RDF.type, LRMOO.F1_Work))
    graph.add((expression, RDF.type, LRMOO.F2_Expression))
    graph.add((bridge, RDF.type, HM.ParadigmBridge))
    graph.add((bridge, HM.bridges_work, work))
    graph.add((bridge, HM.bridges_tradition, tradition))

    graph.add((view, RDF.type, HM.PhilologicalView))
    graph.add((view, HM.views_text_tradition, tradition))
    graph.add((view, HM.views_transmission_witness, witness))

    graph.add((tradition, RDF.type, HM.TextTradition))
    graph.add((tradition, RDFS.label, Literal("Example text tradition", lang="en")))
    graph.add((tradition, HM.has_witness, witness))

    graph.add((witness, RDF.type, HM.TransmissionWitness))
    graph.add((witness, RDFS.label, Literal("Witness in manuscript MS 1", lang="en")))
    graph.add((witness, HM.witnessed_in, manuscript))

    graph.add((production, RDF.type, CIDOC.E12_Production))
    graph.add((production, RDFS.label, Literal("Production event", lang="en")))

    graph.add((access, RDF.type, HM.DigitalAccess))
    graph.add((access, HM.access_url, Literal("https://example.org/iiif/ms1")))

    return graph


def _draft_with_type(
    drafts: list[WikibaseEntityDraft],
    entity_type: str,
) -> WikibaseEntityDraft:
    for draft in drafts:
        if draft.entity_type == entity_type:
            return draft
    raise AssertionError(f"Missing draft entity type: {entity_type}")
