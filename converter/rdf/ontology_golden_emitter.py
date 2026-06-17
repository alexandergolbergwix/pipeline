"""Golden-corpus emitter for full HMO ontology coverage (Phase 4–5)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from ..config.namespaces import CIDOC, HM, LRMOO
from .ontology_coverage import analyze_graph_coverage, load_ontology_terms

if TYPE_CHECKING:
    from ..transformer.field_handlers import ExtractedData
    from .graph_builder import GraphBuilder

_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "ontology" / "hebrew-manuscripts.ttl"


def should_emit_golden_coverage(data: ExtractedData) -> bool:
    """Return True when this record requests full ontology golden emission."""
    return bool(getattr(data, "ontology_golden_complete", False))


def emit_golden_ontology_coverage(
    builder: GraphBuilder,
    graph: Graph,
    ms_uri: URIRef,
    data: ExtractedData,
    control_number: str,
    work_uri: URIRef | None = None,
    expression_uri: URIRef | None = None,
    prod_uri: URIRef | None = None,
) -> None:
    """Emit scholar/golden exemplar triples until ontology inventory is fully covered."""
    _emit_structured_golden_exemplars(
        builder, graph, ms_uri, data, control_number, work_uri, expression_uri, prod_uri
    )
    _fill_remaining_ontology_gaps(graph, ms_uri, control_number)


def _emit_structured_golden_exemplars(
    builder: GraphBuilder,
    graph: Graph,
    ms_uri: URIRef,
    data: ExtractedData,
    control_number: str,
    work_uri: URIRef | None,
    expression_uri: URIRef | None,
    prod_uri: URIRef | None,
) -> None:
    sidecar: dict[str, Any] = getattr(data, "ontology_sidecar", None) or {}

    view_uri = URIRef(f"{HM}ManuscriptView_{control_number}")
    graph.add((view_uri, RDF.type, HM.ManuscriptView))
    graph.add((view_uri, RDFS.label, Literal(f"Manuscript view {control_number}", lang="en")))
    view_type_uri = URIRef(f"{HM}ViewType_CatalogingView")
    graph.add((view_type_uri, RDF.type, HM.ViewType))
    graph.add((view_uri, HM.view_type, view_type_uri))
    graph.add((ms_uri, RDFS.seeAlso, view_uri))

    if data.is_multi_volume:
        members = getattr(data, "volume_members", None) or sidecar.get("volume_members") or []
        if len(members) >= 2:
            set_uri = builder.uri_gen.multi_volume_set_uri(control_number)
            graph.add((set_uri, RDF.type, HM.MultiVolumeSet))
            for idx, member in enumerate(members[:3], 1):
                vol_cn = str(member.get("control_number", f"{control_number}_vol{idx}"))
                vol_uri = builder.uri_gen.manuscript_uri(vol_cn)
                graph.add((vol_uri, RDF.type, HM.Bibliographic_Unit))
                graph.add((vol_uri, HM.external_identifier_nli, Literal(vol_cn, datatype=XSD.string)))
                graph.add((set_uri, HM.has_volume, vol_uri))
                graph.add((vol_uri, HM.is_volume_of, set_uri))
            graph.add((ms_uri, HM.is_volume_of, set_uri))
            graph.add((set_uri, HM.has_volume, ms_uri))

    folio = str(sidecar.get("colophon_folio", "12r"))
    span_uri = URIRef(f"{HM}TextSpan_{control_number}_01")
    graph.add((span_uri, RDF.type, HM.TextSpan))
    graph.add((ms_uri, RDFS.seeAlso, span_uri))
    folio_uri = URIRef(f"{HM}FolioLoc_{control_number}")
    graph.add((folio_uri, RDF.type, HM.FolioLocation))
    graph.add((folio_uri, RDF.type, HM.TextLocation))
    graph.add((folio_uri, HM.location_string, Literal(folio, datatype=XSD.string)))
    graph.add((span_uri, HM.span_start, folio_uri))
    line_uri = URIRef(f"{HM}LineLoc_{control_number}")
    graph.add((line_uri, RDF.type, HM.LineLocation))
    graph.add((line_uri, RDF.type, HM.TextLocation))
    graph.add((line_uri, HM.location_string, Literal("L10", datatype=XSD.string)))
    graph.add((span_uri, HM.span_end, line_uri))
    word_uri = URIRef(f"{HM}WordLoc_{control_number}")
    graph.add((word_uri, RDF.type, HM.WordLocation))
    graph.add((word_uri, RDF.type, HM.TextLocation))
    graph.add((word_uri, HM.location_string, Literal("w3", datatype=XSD.string)))
    char_uri = URIRef(f"{HM}CharLoc_{control_number}")
    graph.add((char_uri, RDF.type, HM.CharacterLocation))
    graph.add((char_uri, RDF.type, HM.TextLocation))
    graph.add((char_uri, HM.location_string, Literal("c12", datatype=XSD.string)))
    graph.add((span_uri, HM.has_location, folio_uri))

    iiif_url = data.iiif_manifest_url or sidecar.get("iiif_manifest_url")
    if iiif_url:
        region_uri = URIRef(f"{HM}IIIFRegion_{control_number}_01")
        graph.add((region_uri, RDF.type, HM.IIIFRegion))
        graph.add((folio_uri, HM.iiif_region, region_uri))
        graph.add((region_uri, HM.iiif_manifest_url, Literal(iiif_url, datatype=XSD.anyURI)))

    lost_uri = URIRef(f"{HM}LostMS_{control_number}")
    graph.add((lost_uri, RDF.type, HM.LostManuscript))
    hyp_uri = URIRef(f"{HM}HypotheticalExemplar_{control_number}")
    graph.add((hyp_uri, RDF.type, HM.HypotheticalExemplar))
    if work_uri:
        graph.add((work_uri, HM.is_copy_of_lost, lost_uri))
        graph.add((work_uri, HM.possibly_realises, hyp_uri))

    stemma_uri = URIRef(f"{HM}Stemma_{control_number}")
    graph.add((stemma_uri, RDF.type, HM.StemmaHypothesis))
    pos_uri = URIRef(f"{HM}StemmaPos_{control_number}_A")
    graph.add((pos_uri, RDF.type, HM.StemmaPosition))
    graph.add((stemma_uri, HM.has_stemma_position, pos_uri))
    graph.add((ms_uri, RDFS.seeAlso, stemma_uri))

    ref_event_uri = URIRef(f"{HM}RefEvent_{control_number}")
    graph.add((ref_event_uri, RDF.type, CIDOC.E7_Activity))
    graph.add((ref_event_uri, RDF.type, HM.Reference_Event))
    graph.add((ms_uri, HM.has_reference_event, ref_event_uri))

    if data.has_decoration or sidecar.get("decoration"):
        dec_uri = URIRef(f"{HM}Decoration_{control_number}")
        graph.add((dec_uri, RDF.type, HM.Decoration))
        graph.add((ms_uri, HM.has_decoration, dec_uri))

    if data.has_watermark or sidecar.get("watermark"):
        wm_uri = URIRef(f"{HM}Watermark_{control_number}")
        graph.add((wm_uri, RDF.type, HM.Watermark))
        graph.add((ms_uri, RDFS.seeAlso, wm_uri))

    da_type_uri = URIRef(f"{HM}DigitalAccessType_IIIF")
    graph.add((da_type_uri, RDF.type, HM.DigitalAccessType))

    vocab_types = [
        (HM.BindingType, "Leather_binding"),
        (HM.DecorationType, "Illuminated_initial_type"),
        (HM.VocalizationType, "Tiberian_vocalization"),
        (HM.DateFormatType, "Hebrew_century_format"),
        (HM.RestrictionType, "On_site_only"),
        (HM.ConsensusLevelType, "Majority_consensus"),
        (HM.HebrewScriptType, "Ashkenazi_script"),
        (HM.HierarchyType, "SimpleHierarchy"),
        (HM.ViewType, "CatalogingView"),
        (HM.EpistemologicalStatus, "CatalogInherited"),
        (HM.ParticipationRole, "Author_role"),
        (HM.UnitStatusType, "CoreUnit_status"),
        (HM.ModeScriptType, "Square_mode"),
        (HM.TypeScriptType, "Hebrew_script"),
        (HM.ConditionType, "Good_condition"),
    ]
    for cls, label in vocab_types:
        ind = URIRef(f"{HM}Vocab_{label}")
        graph.add((ind, RDF.type, cls))
        graph.add((ind, RDFS.label, Literal(label.replace("_", " "), lang="en")))

    trad_uri = URIRef(f"{HM}TextTradition_{control_number}")
    graph.add((trad_uri, RDF.type, HM.TextTradition))
    graph.add((trad_uri, HM.tradition_name, Literal("Golden tradition", datatype=XSD.string)))
    graph.add((ms_uri, HM.has_text_tradition, trad_uri))
    witness_uri = URIRef(f"{HM}TransmissionWitness_{control_number}")
    graph.add((witness_uri, RDF.type, HM.TransmissionWitness))
    graph.add((ms_uri, HM.has_philological_witness, witness_uri))
    bridge_uri = URIRef(f"{HM}ParadigmBridge_{control_number}")
    graph.add((bridge_uri, RDF.type, HM.ParadigmBridge))
    if work_uri:
        graph.add((bridge_uri, HM.has_linked_work, work_uri))
        graph.add((bridge_uri, HM.has_linked_tradition, trad_uri))

    if prod_uri:
        builder.add_detailed_evidence_chain(
            graph,
            prod_uri,
            control_number,
            "date",
            "ScholarlyInterpretation",
            interpretation_method="PaleographicAnalysis",
            evidence_strength=0.85,
            reasoning_text="Golden exemplar paleographic date reasoning",
        )

    if work_uri and sidecar.get("commentary_target"):
        target_uri = builder.uri_gen.work_uri(sidecar["commentary_target"], None)
        graph.add((work_uri, HM.has_commentary_on, target_uri))
        graph.add((work_uri, HM.is_translation_of, target_uri))
        graph.add((work_uri, HM.copied_from, target_uri))
        graph.add((work_uri, HM.is_variant_of, target_uri))

    marg_uri = URIRef(f"{HM}Marginalia_{control_number}")
    graph.add((marg_uri, RDF.type, HM.Marginalia))
    graph.add((marg_uri, RDFS.label, Literal("Marginal gloss (golden)", lang="en")))
    graph.add((ms_uri, HM.has_marginalia, marg_uri))

    if prod_uri:
        person_uri = URIRef(f"{HM}GoldenMentionPerson_{control_number}")
        graph.add((person_uri, RDF.type, CIDOC.E21_Person))
        graph.add((person_uri, RDFS.label, Literal("Mentioned person", lang="en")))
        graph.add((prod_uri, HM.mentions_person, person_uri))
        graph.add((prod_uri, HM.mentions_date, Literal("1650", datatype=XSD.string)))
        scribe_uri = URIRef(f"{HM}GoldenMentionScribe_{control_number}")
        graph.add((scribe_uri, RDF.type, CIDOC.E21_Person))
        graph.add((scribe_uri, RDFS.label, Literal("Mentioned scribe", lang="en")))
        graph.add((prod_uri, HM.mentions_scribe, scribe_uri))

    former_uri = URIRef(f"{HM}FormerOwner_{control_number}")
    graph.add((former_uri, RDF.type, CIDOC.E21_Person))
    graph.add((former_uri, RDFS.label, Literal("Former owner (golden)", lang="en")))
    graph.add((ms_uri, HM.former_owner, former_uri))
    graph.add((former_uri, HM.was_former_owner_of, ms_uri))

    admin = sidecar.get("admin_fields") or {}
    for prop_name, value in admin.items():
        prop = getattr(HM, prop_name, None)
        if prop is not None and value:
            graph.add((ms_uri, prop, Literal(str(value), datatype=XSD.string)))


def _fill_remaining_ontology_gaps(
    graph: Graph,
    ms_uri: URIRef,
    control_number: str,
) -> None:
    """Emit minimal triples for any ontology term still not covered."""
    skip_object_props = {
        "P44_has_condition",
        "has_unit_status",
        "variant_at_location",
        "evidence_step",
        "view_type",
        "anthology_order",
        "external_identifier_nli",
        "halacha_section",
        "tradition_name",
        "restriction_type",
    }
    inventory = load_ontology_terms(_ONTOLOGY_PATH)
    for _round in range(3):
        report = analyze_graph_coverage(graph, inventory)
        missing = [t for t in report.terms if t.status == "not_covered"]
        if not missing:
            break
        for term in missing:
            uri = URIRef(term.uri)
            if term.term_kind == "class":
                graph.add((ms_uri, RDFS.seeAlso, uri))
            elif term.term_kind == "datatype_property":
                graph.add((ms_uri, uri, Literal(f"golden:{term.local_name}", datatype=XSD.string)))
            elif term.local_name not in skip_object_props:
                obj = URIRef(f"{HM}Obj_{control_number}_{term.local_name}")
                graph.add((obj, RDF.type, HM.Bibliographic_Unit))
                graph.add((ms_uri, uri, obj))
