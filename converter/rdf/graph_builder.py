"""RDF graph builder for Hebrew Manuscripts Ontology.

Fully updated for v1.4 ontology with comprehensive support for:
- Epistemological framework (fact vs interpretation, evidence chains)
- Certainty levels and attribution sources
- Dual paradigm support (cataloging + philological views)
- Nested codicological unit structure
- Text tradition and transmission witness modeling
- Canonical hierarchies for Jewish texts
- Scribal interventions and variants
"""

import re
from typing import Any

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from ..config.namespaces import CIDOC, HM, LRMOO, bind_namespaces
from ..config.vocabularies import (
    DATA_CATEGORIES,
    DATA_FACTUALITY,
    LANGUAGE_CODES,
)
from ..transformer.field_handlers import ExtractedData
from ..transformer.uri_generator import UriGenerator


class GraphBuilder:
    """Builds RDF graphs from extracted MARC data.

    Supports Hebrew Manuscripts Ontology v1.4 features including:
    - Epistemological framework for distinguishing facts from interpretations
    - Certainty levels and attribution sources
    - Cataloging view paradigm (bibliographic approach)
    """

    def __init__(
        self,
        uri_generator: UriGenerator | None = None,
        add_epistemological_status: bool = True,
        add_cataloging_view: bool = True,
    ):
        """Initialize the graph builder.

        Args:
            uri_generator: Optional custom URI generator
            add_epistemological_status: Whether to add epistemological metadata
            add_cataloging_view: Whether to add cataloging paradigm view
        """
        self.uri_gen = uri_generator or UriGenerator()
        self.add_epistemological_status = add_epistemological_status
        self.add_cataloging_view = add_cataloging_view

    def build_graph(self, data: ExtractedData, control_number: str) -> Graph:
        """Build complete RDF graph from extracted data.

        Args:
            data: Extracted data from MARC record
            control_number: MARC control number

        Returns:
            RDF graph representing the manuscript
        """
        graph = Graph()
        bind_namespaces(graph)

        ms_uri = self.uri_gen.manuscript_uri(control_number)
        work_expression_pairs: list[dict[str, Any]] = []
        structural_cu_uris: list[URIRef] = []
        scribe_entity_uris: list[URIRef] = []

        self._add_manuscript(graph, ms_uri, data, control_number)

        work_uri = None
        expression_uri = None
        if data.title:
            author_name = data.authors[0]["name"] if data.authors else None
            work_uri = self._add_work(graph, data, author_name)

            expression_uri = self._add_expression(graph, work_uri, ms_uri, data, control_number)

            graph.add((ms_uri, LRMOO.R4_embodies, expression_uri))
            graph.add((ms_uri, HM.has_expression, expression_uri))
            graph.add((ms_uri, HM.has_work, work_uri))
            work_expression_pairs.append(
                {
                    "work": work_uri,
                    "expression": expression_uri,
                    "title": data.title or f"MS {control_number}",
                }
            )

            main_cu_uri = URIRef(f"{HM}CU_{control_number}_main")
            graph.add((main_cu_uri, RDF.type, HM.Codicological_Unit))
            graph.add(
                (
                    main_cu_uri,
                    RDFS.label,
                    Literal(f"Main codicological unit of MS {control_number}", lang="en"),
                )
            )
            graph.add((ms_uri, HM.is_composed_of, main_cu_uri))
            graph.add((main_cu_uri, HM.forms_part_of, ms_uri))
            graph.add((main_cu_uri, HM.has_expression, expression_uri))
            graph.add((main_cu_uri, HM.has_work, work_uri))
            structural_cu_uris.append(main_cu_uri)

        prod_uri = self._add_production_event(graph, ms_uri, data, control_number)

        if data.provenance:
            acquisition_uri = URIRef(f"{HM}Acquisition_{control_number}_01")
            graph.add((acquisition_uri, RDF.type, CIDOC.E8_Acquisition))
            graph.add((acquisition_uri, RDFS.comment, Literal(data.provenance, lang="he")))
            graph.add((ms_uri, HM.has_acquisition_event, acquisition_uri))

        for author in data.authors:
            person_uri = self._add_person(
                graph, author, work_uri, "author", related_work_title=data.title
            )
            if person_uri and work_uri:
                graph.add((work_uri, HM.has_author, person_uri))

        for contributor in data.contributors:
            role = contributor.get("role", "contributor")
            person_uri = self._add_person(graph, contributor, ms_uri, role)
            if person_uri:
                graph.add((person_uri, HM.has_role, Literal(role, datatype=XSD.string)))
                if role in ("scribe", "copyist"):
                    graph.add((prod_uri, CIDOC.P14_carried_out_by, person_uri))
                    graph.add((prod_uri, HM.has_scribe, person_uri))
                    scribe_entity_uris.append(person_uri)
                elif role in ("current_owner", "former_owner"):
                    graph.add((ms_uri, HM.has_owner, person_uri))
                    person_local_id = str(person_uri).split("#")[-1]
                    custody_uri = URIRef(
                        f"{HM}TransferOfCustody_{control_number}_{person_local_id}"
                    )
                    graph.add((custody_uri, RDF.type, CIDOC.E10_Transfer_of_Custody))
                    graph.add((ms_uri, HM.has_transfer_of_custody, custody_uri))

        for index, content in enumerate(data.contents, 1):
            content_record = self._add_content_work(graph, content, ms_uri, control_number)
            if not content_record:
                continue
            work_expression_pairs.append(content_record)
            cu_uri = URIRef(f"{HM}CU_{control_number}_{index:02d}")
            graph.add((cu_uri, RDF.type, HM.Codicological_Unit))
            graph.add(
                (
                    cu_uri,
                    RDFS.label,
                    Literal(f"Codicological unit {index} of MS {control_number}", lang="en"),
                )
            )
            graph.add((ms_uri, HM.is_composed_of, cu_uri))
            graph.add((cu_uri, HM.forms_part_of, ms_uri))
            graph.add((cu_uri, HM.has_expression, content_record["expression"]))
            graph.add((cu_uri, HM.has_work, content_record["work"]))
            if content_record.get("folio_range"):
                graph.add(
                    (
                        cu_uri,
                        HM.has_folio_range,
                        Literal(content_record["folio_range"], datatype=XSD.string),
                    )
                )
            structural_cu_uris.append(cu_uri)

        if not structural_cu_uris:
            default_cu_uri = URIRef(f"{HM}CU_{control_number}_01")
            graph.add((default_cu_uri, RDF.type, HM.Codicological_Unit))
            graph.add(
                (
                    default_cu_uri,
                    RDFS.label,
                    Literal(f"Codicological unit 1 of MS {control_number}", lang="en"),
                )
            )
            graph.add((ms_uri, HM.is_composed_of, default_cu_uri))
            graph.add((default_cu_uri, HM.forms_part_of, ms_uri))
            if expression_uri:
                graph.add((default_cu_uri, HM.has_expression, expression_uri))
            if work_uri:
                graph.add((default_cu_uri, HM.has_work, work_uri))
            structural_cu_uris.append(default_cu_uri)

        script_values = list(data.script_types) if hasattr(data, "script_types") else []
        pu_count = max(1, len(script_values), len(scribe_entity_uris))
        for idx in range(pu_count):
            pu_uri = URIRef(f"{HM}PU_{control_number}_{idx + 1:02d}")
            parent_cu = structural_cu_uris[idx % len(structural_cu_uris)]
            graph.add((pu_uri, RDF.type, HM.Paleographical_Unit))
            graph.add(
                (
                    pu_uri,
                    RDFS.label,
                    Literal(f"Paleographical unit {idx + 1} of MS {control_number}", lang="en"),
                )
            )
            graph.add((parent_cu, HM.is_composed_of, pu_uri))
            graph.add((pu_uri, HM.forms_part_of, parent_cu))

            if script_values:
                graph.add((pu_uri, HM.has_script_type, HM[script_values[idx % len(script_values)]]))

            if idx < len(scribe_entity_uris):
                scribe_uri = scribe_entity_uris[idx]
            else:
                scribe_uri = URIRef(f"{HM}Unknown_Scribe_{control_number}_{idx + 1:02d}")
                graph.add((scribe_uri, RDF.type, CIDOC.E21_Person))
                graph.add(
                    (
                        scribe_uri,
                        RDFS.label,
                        Literal(f"Unknown scribe {idx + 1} (MS {control_number})", lang="en"),
                    )
                )
            graph.add((pu_uri, HM.has_scribe, scribe_uri))
            graph.add((prod_uri, HM.has_scribe, scribe_uri))

        if data.is_multi_volume:
            self._add_multi_volume_set(graph, ms_uri, data, control_number)

        if data.is_anthology and len(data.contents) > 1:
            self._add_anthology_structure(graph, ms_uri, control_number, len(data.contents))

        for subject in data.subjects:
            self._add_subject(graph, subject, ms_uri, work_uri)

        for ref in data.catalog_references:
            self._add_catalog_reference(graph, ref, ms_uri)

        if data.colophon_text:
            self._add_colophon(graph, ms_uri, data.colophon_text, control_number)

        if data.binding_info:
            self._add_binding(graph, ms_uri, data.binding_info, control_number)

        # Add cataloging view paradigm (v1.4)
        if self.add_cataloging_view and work_uri:
            self._add_cataloging_view(graph, ms_uri, work_uri, expression_uri, control_number)

        if work_expression_pairs:
            phil_view_uri = self.add_philological_view(
                graph, ms_uri, control_number, is_primary=False
            )
            for pair in work_expression_pairs:
                tradition_name = f"{pair['title']} tradition"
                tradition_uri = self.add_text_tradition(graph, tradition_name)
                self.add_transmission_witness(
                    graph,
                    ms_uri,
                    tradition_uri,
                    control_number,
                    pair["title"],
                    expression_uri=pair["expression"],
                    philological_view_uri=phil_view_uri,
                )
                self.add_paradigm_bridge(
                    graph,
                    pair["work"],
                    tradition_uri,
                    pair["title"],
                    tradition_name,
                    justification="Cataloging and philological alignment for generated pilot data",
                )

        # Add epistemological metadata for catalog-derived data (v1.4)
        if self.add_epistemological_status:
            self._add_epistemological_metadata(graph, ms_uri, control_number)

        # v1.5 coverage extensions
        if data.scribal_interventions:
            self._add_scribal_interventions(
                graph, ms_uri, data.scribal_interventions, control_number
            )
        if data.canonical_references:
            self._add_canonical_references(
                graph, ms_uri, data.canonical_references, work_uri, control_number
            )
        if data.digital_url:
            self._add_digital_access(
                graph, ms_uri, data.digital_url, control_number, data.iiif_manifest_url
            )
        if data.rights_statement or data.copyright_notice:
            self._add_rights_determination(
                graph,
                ms_uri,
                data.rights_statement,
                data.copyright_notice,
                data.usage_restriction,
                data.restriction_url,
                control_number,
            )
        if data.holding_institution:
            self._add_physical_holding(
                graph, ms_uri, data.holding_institution, data.shelfmark, control_number
            )
        self._add_physical_features(graph, ms_uri, data, control_number)
        if data.related_works:
            self._add_related_works(graph, ms_uri, data.related_works, work_uri)
        if data.related_places:
            self._add_related_places(graph, ms_uri, data.related_places, prod_uri)
        if data.condition_notes:
            self._add_condition_notes(graph, ms_uri, data.condition_notes, control_number)
        self._add_codicological_hierarchy_from_data(graph, ms_uri, data, control_number)

        return graph

    def _add_manuscript(
        self, graph: Graph, ms_uri: URIRef, data: ExtractedData, control_number: str
    ):
        """Add manuscript entity to graph.

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            data: Extracted data
            control_number: MARC control number
        """
        graph.add((ms_uri, RDF.type, LRMOO.F4_Manifestation_Singleton))
        graph.add((ms_uri, RDF.type, HM.Bibliographic_Unit))

        graph.add(
            (ms_uri, HM.external_identifier_nli, Literal(control_number, datatype=XSD.string))
        )

        label = data.title or f"MS {control_number}"
        graph.add((ms_uri, RDFS.label, Literal(label, lang="he")))

        if data.extent:
            graph.add(
                (ms_uri, HM.has_number_of_folios, Literal(int(data.extent), datatype=XSD.integer))
            )

        if data.height_mm:
            graph.add(
                (ms_uri, HM.has_height_mm, Literal(int(data.height_mm), datatype=XSD.integer))
            )

        if data.width_mm:
            graph.add((ms_uri, HM.has_width_mm, Literal(int(data.width_mm), datatype=XSD.integer)))

        for material in data.materials:
            material_uri = self.uri_gen.material_uri(material)
            graph.add((ms_uri, HM.has_material, material_uri))
            graph.add((material_uri, RDF.type, CIDOC.E57_Material))
            graph.add((material_uri, RDFS.label, Literal(material, lang="en")))

        if data.script_type:
            script_uri = self.uri_gen.script_type_uri(data.script_type)
            graph.add((ms_uri, HM.has_script_type, script_uri))
            graph.add((script_uri, RDF.type, HM.TypeScriptType))
            graph.add((script_uri, RDFS.label, Literal(data.script_type, lang="en")))

        if data.script_mode:
            mode_uri = self.uri_gen.script_type_uri(data.script_mode)
            graph.add((ms_uri, HM.has_script_mode, mode_uri))
            graph.add((mode_uri, RDF.type, HM.ModeScriptType))
            graph.add((mode_uri, RDFS.label, Literal(data.script_mode, lang="en")))

        if data.digital_url:
            graph.add(
                (
                    ms_uri,
                    HM.has_digital_representation_url,
                    Literal(data.digital_url, datatype=XSD.anyURI),
                )
            )

        if data.provenance:
            graph.add((ms_uri, HM.ownership_history, Literal(data.provenance, datatype=XSD.string)))

        for note in data.notes:
            graph.add((ms_uri, RDFS.comment, Literal(note, lang="he")))

    def _add_work(
        self, graph: Graph, data: ExtractedData, author_name: str | None = None
    ) -> URIRef:
        """Add Work entity to graph.

        Args:
            graph: RDF graph
            data: Extracted data
            author_name: Optional author name for URI

        Returns:
            Work URI
        """
        work_uri = self.uri_gen.work_uri(data.title, author_name)

        graph.add((work_uri, RDF.type, LRMOO.F1_Work))

        graph.add((work_uri, HM.has_title, Literal(data.title, lang="he")))
        graph.add((work_uri, RDFS.label, Literal(data.title, lang="he")))

        if data.subtitle:
            full_title = f"{data.title} : {data.subtitle}"
            graph.add((work_uri, HM.has_title, Literal(full_title, lang="he")))

        for variant in data.variant_titles:
            graph.add((work_uri, HM.has_title, Literal(variant, lang="he")))

        for genre in data.genres:
            genre_uri = self.uri_gen.subject_uri(genre)
            graph.add((work_uri, CIDOC.P2_has_type, genre_uri))
            graph.add((work_uri, HM.has_genre, genre_uri))
            graph.add((genre_uri, RDF.type, HM.SubjectType))
            graph.add((genre_uri, RDFS.label, Literal(genre, lang="he")))

        return work_uri

    def _add_expression(
        self,
        graph: Graph,
        work_uri: URIRef,
        ms_uri: URIRef,
        data: ExtractedData,
        control_number: str,
    ) -> URIRef:
        """Add Expression entity to graph.

        Args:
            graph: RDF graph
            work_uri: Work URI
            ms_uri: Manuscript URI
            data: Extracted data
            control_number: MARC control number

        Returns:
            Expression URI
        """
        expression_uri = self.uri_gen.expression_uri(data.title, control_number)

        graph.add((expression_uri, RDF.type, LRMOO.F2_Expression))

        # Add label for Expression
        expr_label = (
            f"{data.title} (in MS {control_number})"
            if data.title
            else f"Expression in MS {control_number}"
        )
        graph.add((expression_uri, RDFS.label, Literal(expr_label, lang="he")))

        graph.add((expression_uri, LRMOO.R3_is_realised_in, work_uri))

        for lang_code in data.languages:
            lang_name = LANGUAGE_CODES.get(lang_code, lang_code)
            lang_uri = self.uri_gen.language_uri(lang_name)
            # Type the language as E56_Language
            graph.add((lang_uri, RDF.type, CIDOC.E56_Language))
            graph.add((lang_uri, RDFS.label, Literal(lang_name)))
            graph.add((expression_uri, CIDOC.P72_has_language, lang_uri))

        return expression_uri

    def _add_production_event(
        self, graph: Graph, ms_uri: URIRef, data: ExtractedData, control_number: str
    ):
        """Add Production event to graph.

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            data: Extracted data
            control_number: MARC control number
        """
        prod_uri = self.uri_gen.production_event_uri(control_number)

        graph.add((prod_uri, RDF.type, CIDOC.E12_Production))
        graph.add((prod_uri, LRMOO.R27_materialized, ms_uri))
        graph.add((ms_uri, HM.has_production_event, prod_uri))

        if data.place:
            place_uri = self.uri_gen.place_uri(data.place)
            graph.add((prod_uri, CIDOC.P7_took_place_at, place_uri))
            graph.add((prod_uri, HM.has_production_place, place_uri))
            graph.add((place_uri, RDF.type, CIDOC.E53_Place))
            graph.add((place_uri, RDFS.label, Literal(data.place, lang="he")))

        if data.dates:
            time_label = self._format_time_label(data.dates)
            time_uri = self.uri_gen.time_span_uri(time_label)

            graph.add((prod_uri, CIDOC.P4_has_time_span, time_uri))
            graph.add((prod_uri, HM.has_production_time, time_uri))
            graph.add((time_uri, RDF.type, CIDOC["E52_Time-Span"]))

            if "date_start" in data.dates:
                graph.add(
                    (
                        time_uri,
                        HM.earliest_possible_date,
                        Literal(data.dates["date_start"], datatype=XSD.integer),
                    )
                )
            elif "year" in data.dates:
                graph.add(
                    (
                        time_uri,
                        HM.earliest_possible_date,
                        Literal(data.dates["year"], datatype=XSD.integer),
                    )
                )

            if "date_end" in data.dates:
                graph.add(
                    (
                        time_uri,
                        HM.latest_possible_date,
                        Literal(data.dates["date_end"], datatype=XSD.integer),
                    )
                )
            elif "year" in data.dates:
                graph.add(
                    (
                        time_uri,
                        HM.latest_possible_date,
                        Literal(data.dates["year"], datatype=XSD.integer),
                    )
                )

            graph.add((time_uri, RDFS.label, Literal(time_label)))

            # Add date format type (v1.4)
            if "date_format" in data.dates:
                date_format = data.dates["date_format"]
                date_format_uri = HM[date_format]
                graph.add((time_uri, HM.has_date_format, date_format_uri))

            # Store original date string
            if "original_string" in data.dates:
                graph.add(
                    (time_uri, HM.date_original_string, Literal(data.dates["original_string"]))
                )
            graph.add(
                (
                    prod_uri,
                    HM.has_production_date_certain,
                    Literal(bool("year" in data.dates), datatype=XSD.boolean),
                )
            )

        for contributor in data.contributors:
            if contributor.get("role") in ("scribe", "copyist"):
                person_uri = self.uri_gen.person_uri(contributor["name"])
                graph.add((prod_uri, CIDOC.P14_carried_out_by, person_uri))
                graph.add((prod_uri, HM.has_scribe, person_uri))

        return prod_uri

    @staticmethod
    def _is_http_uri(value: str) -> bool:
        return bool(re.match(r"^https?://\\S+$", value.strip(), re.IGNORECASE))

    def _extract_authority_identifiers(self, raw_values: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {"same_as_uris": []}

        def add_same_as(uri: str) -> None:
            if uri not in result["same_as_uris"]:
                result["same_as_uris"].append(uri)

        for raw in raw_values:
            if not raw:
                continue
            value = raw.strip()
            if not value:
                continue

            viaf_uri_match = re.search(
                r"https?://(?:www\\.)?viaf\\.org/viaf/(\\d+)", value, re.IGNORECASE
            )
            viaf_id_match = re.search(r"\\(VIAF\\)\\s*(\\d+)", value, re.IGNORECASE)
            viaf_plain_match = re.fullmatch(r"\\d{5,}", value)
            if viaf_uri_match:
                viaf_id = viaf_uri_match.group(1)
                result["viaf_id"] = viaf_id
                add_same_as(f"https://viaf.org/viaf/{viaf_id}")
            elif viaf_id_match:
                result["viaf_id"] = viaf_id_match.group(1)
            elif viaf_plain_match:
                result["viaf_id"] = value

            wikidata_uri_match = re.search(
                r"https?://(?:www\\.)?wikidata\\.org/entity/(Q\\d+)", value, re.IGNORECASE
            )
            wikidata_id_match = re.fullmatch(r"Q\\d+", value, re.IGNORECASE)
            if wikidata_uri_match:
                qid = wikidata_uri_match.group(1).upper()
                result["wikidata_id"] = qid
                add_same_as(f"https://www.wikidata.org/entity/{qid}")
            elif wikidata_id_match:
                result["wikidata_id"] = value.upper()

            if self._is_http_uri(value) and "nli.org.il" in value.lower():
                result["external_uri_nli"] = value
                add_same_as(value)

        return result

    def _add_person(
        self,
        graph: Graph,
        person_data: dict[str, Any],
        related_uri: URIRef | None,
        role: str,
        related_work_title: str | None = None,
    ) -> URIRef | None:
        """Add Person entity to graph.

        Args:
            graph: RDF graph
            person_data: Person data dictionary
            related_uri: URI of related entity (Work or Manuscript)
            role: Person's role
        """
        if not person_data.get("name"):
            return None

        person_uri = self.uri_gen.person_uri(person_data["name"])

        if person_data.get("type") == "organization":
            graph.add((person_uri, RDF.type, CIDOC.E74_Group))
        else:
            graph.add((person_uri, RDF.type, CIDOC.E21_Person))

        graph.add((person_uri, RDFS.label, Literal(person_data["name"], lang="he")))

        if "birth_year" in person_data:
            graph.add(
                (
                    person_uri,
                    CIDOC.P82a_begin_of_the_begin,
                    Literal(person_data["birth_year"], datatype=XSD.integer),
                )
            )

        if "death_year" in person_data:
            graph.add(
                (
                    person_uri,
                    CIDOC.P82b_end_of_the_end,
                    Literal(person_data["death_year"], datatype=XSD.integer),
                )
            )

        raw_authority_values: list[str] = []
        if person_data.get("authority_id"):
            raw_authority_values.append(str(person_data["authority_id"]))
        raw_authority_values.extend(person_data.get("authority_source_values", []))
        if person_data.get("external_uri_nli"):
            raw_authority_values.append(person_data["external_uri_nli"])
        if person_data.get("viaf_id"):
            raw_authority_values.append(str(person_data["viaf_id"]))
        if person_data.get("wikidata_id"):
            raw_authority_values.append(str(person_data["wikidata_id"]))
        raw_authority_values.extend(person_data.get("same_as_uris", []))

        auth_data = self._extract_authority_identifiers(raw_authority_values)
        if auth_data.get("external_uri_nli"):
            graph.add(
                (
                    person_uri,
                    HM.external_uri_nli,
                    Literal(auth_data["external_uri_nli"], datatype=XSD.anyURI),
                )
            )
        if auth_data.get("viaf_id"):
            graph.add((person_uri, HM.viaf_id, Literal(auth_data["viaf_id"], datatype=XSD.string)))
        if auth_data.get("wikidata_id"):
            graph.add(
                (person_uri, HM.wikidata_id, Literal(auth_data["wikidata_id"], datatype=XSD.string))
            )
        for same_as_uri in auth_data.get("same_as_uris", []):
            if self._is_http_uri(same_as_uri):
                graph.add((person_uri, OWL.sameAs, URIRef(same_as_uri)))

        if related_uri and role == "author" and related_work_title:
            creation_uri = self.uri_gen.work_creation_event_uri(
                related_work_title, person_data["name"]
            )
            graph.add((creation_uri, RDF.type, LRMOO.F27_Work_Creation))
            graph.add((creation_uri, LRMOO.R16_created, related_uri))
            graph.add((creation_uri, CIDOC.P14_carried_out_by, person_uri))

        return person_uri

    def _add_content_work(
        self, graph: Graph, content: dict[str, Any], ms_uri: URIRef, control_number: str
    ) -> dict[str, Any] | None:
        """Add content work from 505 field.

        Args:
            graph: RDF graph
            content: Content item dictionary
            ms_uri: Manuscript URI
            control_number: MARC control number
        """
        if not content.get("title"):
            return None

        work_uri = self.uri_gen.work_uri(content["title"])
        graph.add((work_uri, RDF.type, LRMOO.F1_Work))
        graph.add((work_uri, HM.has_title, Literal(content["title"], lang="he")))
        graph.add((work_uri, RDFS.label, Literal(content["title"], lang="he")))

        expression_uri = self.uri_gen.expression_uri(content["title"], control_number)
        graph.add((expression_uri, RDF.type, LRMOO.F2_Expression))
        graph.add(
            (
                expression_uri,
                RDFS.label,
                Literal(f"{content['title']} (in MS {control_number})", lang="he"),
            )
        )
        graph.add((expression_uri, LRMOO.R3_is_realised_in, work_uri))

        if content.get("folio_range"):
            graph.add(
                (
                    expression_uri,
                    HM.has_folio_range,
                    Literal(content["folio_range"], datatype=XSD.string),
                )
            )

        if content.get("sequence") is not None:
            position_bnode = BNode()
            graph.add((position_bnode, RDF.type, HM.AnthologyPosition))
            graph.add(
                (
                    position_bnode,
                    HM.anthology_order,
                    Literal(content["sequence"], datatype=XSD.integer),
                )
            )
            graph.add((expression_uri, HM.has_anthology_position, position_bnode))

        graph.add((ms_uri, LRMOO.R4_embodies, expression_uri))
        graph.add((ms_uri, HM.has_expression, expression_uri))
        graph.add((ms_uri, HM.has_work, work_uri))

        return {
            "work": work_uri,
            "expression": expression_uri,
            "title": content["title"],
            "folio_range": content.get("folio_range"),
        }

    def _add_multi_volume_set(
        self, graph: Graph, ms_uri: URIRef, data: ExtractedData, control_number: str
    ):
        """Add MultiVolumeSet entity and explicit inverse links."""
        set_uri = self.uri_gen.multi_volume_set_uri(control_number)

        graph.add((set_uri, RDF.type, HM.MultiVolumeSet))
        graph.add(
            (
                set_uri,
                RDFS.label,
                Literal(f"Multi-volume set: {data.title or control_number}", lang="en"),
            )
        )
        graph.add((ms_uri, HM.is_volume_of, set_uri))
        graph.add((set_uri, HM.has_volume, ms_uri))

        if data.volume_info:
            graph.add((set_uri, RDFS.comment, Literal(data.volume_info, lang="he")))

    def _add_anthology_structure(
        self, graph: Graph, ms_uri: URIRef, control_number: str, works_count: int
    ):
        """Add AnthologyStructure for manuscripts containing multiple works."""
        anthology_uri = self.uri_gen.anthology_structure_uri(control_number)
        graph.add((anthology_uri, RDF.type, HM.AnthologyStructure))
        graph.add(
            (
                anthology_uri,
                RDFS.label,
                Literal(f"Anthology structure of MS {control_number}", lang="en"),
            )
        )
        graph.add((ms_uri, HM.has_anthology_structure, anthology_uri))
        graph.add((anthology_uri, HM.number_of_works, Literal(works_count, datatype=XSD.integer)))

    def _add_subject(
        self, graph: Graph, subject: dict[str, Any], ms_uri: URIRef, work_uri: URIRef | None
    ):
        """Add subject entity to graph.

        Args:
            graph: RDF graph
            subject: Subject data dictionary
            ms_uri: Manuscript URI
            work_uri: Optional Work URI
        """
        if not subject.get("term"):
            return

        subject_type = subject.get("type", "topic")

        if subject_type == "person":
            subject_uri = self.uri_gen.person_uri(subject["term"])
            graph.add((subject_uri, RDF.type, CIDOC.E21_Person))
        elif subject_type == "organization":
            subject_uri = self.uri_gen.group_uri(subject["term"])
            graph.add((subject_uri, RDF.type, CIDOC.E74_Group))
        elif subject_type == "place":
            subject_uri = self.uri_gen.place_uri(subject["term"])
            graph.add((subject_uri, RDF.type, CIDOC.E53_Place))
        else:
            subject_uri = self.uri_gen.subject_uri(subject["term"])
            graph.add((subject_uri, RDF.type, HM.SubjectType))

        graph.add((subject_uri, RDFS.label, Literal(subject["term"], lang="he")))

        if subject.get("authority_id"):
            graph.add(
                (
                    subject_uri,
                    HM.external_uri_nli,
                    Literal(subject["authority_id"], datatype=XSD.anyURI),
                )
            )

        target = work_uri if work_uri else ms_uri
        graph.add((target, CIDOC.P129_is_about, subject_uri))
        if work_uri:
            graph.add((work_uri, HM.has_subject, subject_uri))

    def _add_catalog_reference(self, graph: Graph, ref: dict[str, str], ms_uri: URIRef):
        """Add catalog reference to graph.

        Args:
            graph: RDF graph
            ref: Catalog reference dictionary
            ms_uri: Manuscript URI
        """
        if not ref.get("name"):
            return

        catalog_uri = self.uri_gen.catalog_uri(ref["name"])

        graph.add((catalog_uri, RDF.type, LRMOO.F3_Manifestation))
        graph.add((catalog_uri, RDFS.label, Literal(ref["name"])))

        graph.add((ms_uri, CIDOC.P70i_is_documented_in, catalog_uri))
        graph.add((ms_uri, HM.is_documented_in, catalog_uri))
        graph.add((catalog_uri, CIDOC.P70_documents, ms_uri))

    def _add_colophon(self, graph: Graph, ms_uri: URIRef, colophon_text: str, control_number: str):
        """Add colophon to graph.

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            colophon_text: Colophon text
            control_number: MARC control number
        """
        colophon_uri = self.uri_gen.colophon_uri(control_number)

        graph.add((colophon_uri, RDF.type, HM.Colophon))
        graph.add((colophon_uri, HM.colophon_text, Literal(colophon_text, lang="he")))
        graph.add((colophon_uri, HM.has_colophon_text, Literal(colophon_text, lang="he")))

        graph.add((ms_uri, HM.has_colophon, colophon_uri))

    def _add_binding(self, graph: Graph, ms_uri: URIRef, binding_info: str, control_number: str):
        """Add binding information to graph.

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            binding_info: Binding description
            control_number: MARC control number
        """
        binding_uri = self.uri_gen.binding_uri(control_number)

        graph.add((binding_uri, RDF.type, HM.Binding))
        graph.add((binding_uri, RDFS.comment, Literal(binding_info, lang="he")))

        graph.add((ms_uri, HM.has_binding, binding_uri))

    # ── v1.5 new emission methods ─────────────────────────────────────────────

    def _add_scribal_interventions(
        self, graph: Graph, ms_uri: URIRef, interventions: list[dict[str, Any]], control_number: str
    ) -> None:
        """Emit ScribalIntervention instances (TextCorrection, MarginalAddition,
        HandChange, Marginalia) for each detected intervention."""
        type_map = {
            "Correction_type": HM.TextCorrection,
            "Erasure_type": HM.ScribalIntervention,
            "Interlinear_addition_type": HM.MarginalAddition,
            "Marginal_gloss_type": HM.Marginalia,
            "Later_hand_type": HM.HandChange,
        }
        seen: set[str] = set()
        for _idx, iv in enumerate(interventions, 1):
            iv_type = iv.get("type", "Correction_type")
            if iv_type in seen:
                continue
            seen.add(iv_type)
            rdf_class = type_map.get(iv_type, HM.ScribalIntervention)
            iv_uri = URIRef(f"{HM}Intervention_{control_number}_{iv_type}")
            graph.add((iv_uri, RDF.type, rdf_class))
            graph.add((iv_uri, RDF.type, HM.ScribalIntervention))
            label_en = iv_type.replace("_type", "").replace("_", " ")
            graph.add((iv_uri, RDFS.label, Literal(label_en, lang="en")))
            if iv.get("source_note"):
                graph.add(
                    (iv_uri, HM.intervention_description, Literal(iv["source_note"], lang="he"))
                )
            graph.add((ms_uri, HM.has_scribal_intervention, iv_uri))
            graph.add((iv_uri, HM.is_intervention_in, ms_uri))

    def _add_canonical_references(
        self,
        graph: Graph,
        ms_uri: URIRef,
        refs: list[dict[str, Any]],
        work_uri: URIRef | None,
        control_number: str,
    ) -> None:
        """Emit BiblicalReference / TalmudicReference / MishnaicReference /
        HalachicReference instances for each detected canonical reference."""
        hierarchy_class_map = {
            "Bible": HM.BiblicalReference,
            "Talmud_Bavli": HM.TalmudicReference,
            "Mishnah": HM.MishnaicReference,
            "Halacha": HM.HalachicReference,
        }
        seen: set[str] = set()
        for ref in refs:
            hier = ref.get("hierarchy", "Bible")
            key = hier + ref.get("book", ref.get("tractate", ""))
            if key in seen:
                continue
            seen.add(key)
            rdf_class = hierarchy_class_map.get(hier, HM.CanonicalReference)
            book_id = ref.get("book") or ref.get("tractate", "unknown")
            ref_uri = URIRef(f"{HM}CanonRef_{control_number}_{hier}_{book_id}".replace(" ", "_"))
            graph.add((ref_uri, RDF.type, rdf_class))
            graph.add((ref_uri, RDF.type, HM.CanonicalReference))
            graph.add((ref_uri, RDFS.label, Literal(f"{hier}: {book_id}", lang="en")))
            # Specific datatype properties
            if ref.get("book"):
                graph.add((ref_uri, HM.book_name, Literal(ref["book"], datatype=XSD.string)))
            if ref.get("chapter"):
                graph.add(
                    (ref_uri, HM.chapter_number, Literal(int(ref["chapter"]), datatype=XSD.integer))
                )
            if ref.get("verse"):
                graph.add(
                    (ref_uri, HM.verse_number, Literal(int(ref["verse"]), datatype=XSD.integer))
                )
            if ref.get("tractate"):
                graph.add(
                    (ref_uri, HM.tractate_name, Literal(ref["tractate"], datatype=XSD.string))
                )
            if ref.get("folio"):
                graph.add((ref_uri, HM.talmud_folio, Literal(ref["folio"], datatype=XSD.string)))
            target = work_uri if work_uri else ms_uri
            graph.add((target, HM.covers_canonical_range, ref_uri))
            graph.add((target, HM.is_commentary_on_canonical, ref_uri))

    def _add_digital_access(
        self,
        graph: Graph,
        ms_uri: URIRef,
        digital_url: str,
        control_number: str,
        iiif_url: str | None = None,
    ) -> None:
        """Emit a DigitalAccess instance linked to the manuscript."""
        da_uri = URIRef(f"{HM}DigitalAccess_{control_number}")
        graph.add((da_uri, RDF.type, HM.DigitalAccess))
        graph.add(
            (da_uri, RDFS.label, Literal(f"Digital access for MS {control_number}", lang="en"))
        )
        graph.add((da_uri, HM.digital_access_url, Literal(digital_url, datatype=XSD.anyURI)))
        if iiif_url:
            graph.add((da_uri, HM.iiif_manifest_url, Literal(iiif_url, datatype=XSD.anyURI)))
            graph.add((da_uri, HM.digital_access_type, Literal("IIIF", datatype=XSD.string)))
        graph.add((ms_uri, HM.has_digital_access, da_uri))
        graph.add((da_uri, HM.is_digital_access_of, ms_uri))

    def _add_rights_determination(
        self,
        graph: Graph,
        ms_uri: URIRef,
        rights_statement: str | None,
        copyright_notice: str | None,
        usage_restriction: str | None,
        restriction_url: str | None,
        control_number: str,
    ) -> None:
        """Emit RightsDetermination and UsageRestriction instances."""
        if rights_statement or copyright_notice:
            rd_uri = URIRef(f"{HM}Rights_{control_number}")
            graph.add((rd_uri, RDF.type, HM.RightsDetermination))
            graph.add((rd_uri, RDFS.label, Literal(f"Rights for MS {control_number}", lang="en")))
            if rights_statement:
                graph.add(
                    (rd_uri, HM.rights_status, Literal(rights_statement, datatype=XSD.string))
                )
                graph.add(
                    (
                        rd_uri,
                        HM.has_rights_statement,
                        Literal(rights_statement, datatype=XSD.string),
                    )
                )
            if copyright_notice:
                graph.add(
                    (rd_uri, HM.copyright_notice, Literal(copyright_notice, datatype=XSD.string))
                )
            graph.add((ms_uri, HM.has_rights_determination, rd_uri))
            graph.add((rd_uri, HM.is_rights_determination_of, ms_uri))

        if usage_restriction:
            ur_uri = URIRef(f"{HM}UsageRestriction_{control_number}")
            graph.add((ur_uri, RDF.type, HM.UsageRestriction))
            graph.add(
                (ur_uri, HM.usage_restriction_note, Literal(usage_restriction, datatype=XSD.string))
            )
            if restriction_url:
                graph.add(
                    (ur_uri, HM.restriction_url, Literal(restriction_url, datatype=XSD.anyURI))
                )
            graph.add((ms_uri, HM.has_usage_restriction, ur_uri))
            graph.add((ur_uri, HM.is_usage_restriction_of, ms_uri))

    def _add_physical_holding(
        self,
        graph: Graph,
        ms_uri: URIRef,
        holding_institution: str,
        shelfmark: str | None,
        control_number: str,
    ) -> None:
        """Emit a PhysicalHolding instance for the manuscript's location."""
        ph_uri = URIRef(f"{HM}Holding_{control_number}")
        graph.add((ph_uri, RDF.type, HM.PhysicalHolding))
        graph.add((ph_uri, RDFS.label, Literal(f"Holding of MS {control_number}", lang="en")))
        graph.add(
            (ph_uri, HM.holding_institution, Literal(holding_institution, datatype=XSD.string))
        )
        if shelfmark:
            graph.add((ph_uri, HM.shelfmark, Literal(shelfmark, datatype=XSD.string)))
            graph.add((ms_uri, HM.shelfmark, Literal(shelfmark, datatype=XSD.string)))
        graph.add((ms_uri, HM.has_physical_holding, ph_uri))
        graph.add((ph_uri, HM.holds_manuscript, ms_uri))
        # Place: held_at links manuscript to place
        inst_place_uri = self.uri_gen.place_uri(holding_institution)
        graph.add((inst_place_uri, RDF.type, CIDOC.E53_Place))
        graph.add((inst_place_uri, RDFS.label, Literal(holding_institution, lang="en")))
        graph.add((ms_uri, HM.held_at, inst_place_uri))

    def _add_physical_features(
        self, graph: Graph, ms_uri: URIRef, data: "ExtractedData", control_number: str
    ) -> None:
        """Emit Watermark, Decoration, Marginalia, and HandChange class instances
        detected from 500/546 notes."""
        if data.has_watermark:
            wm_uri = URIRef(f"{HM}Watermark_{control_number}")
            graph.add((wm_uri, RDF.type, HM.Watermark))
            graph.add((wm_uri, RDFS.label, Literal(f"Watermark in MS {control_number}", lang="en")))
            graph.add((ms_uri, HM.has_decoration, wm_uri))

        if data.has_decoration:
            dec_uri = URIRef(f"{HM}Decoration_{control_number}")
            graph.add((dec_uri, RDF.type, HM.Decoration))
            graph.add(
                (dec_uri, RDFS.label, Literal(f"Decoration in MS {control_number}", lang="en"))
            )
            graph.add((ms_uri, HM.has_decoration, dec_uri))

        if data.has_multiple_hands:
            hc_uri = URIRef(f"{HM}HandChange_{control_number}")
            graph.add((hc_uri, RDF.type, HM.HandChange))
            graph.add(
                (hc_uri, RDFS.label, Literal(f"Hand change in MS {control_number}", lang="en"))
            )
            graph.add((ms_uri, HM.has_scribal_intervention, hc_uri))
            graph.add((hc_uri, HM.is_intervention_in, ms_uri))

        if data.has_vocalization:
            graph.add((ms_uri, HM.has_vocalization, Literal(True, datatype=XSD.boolean)))

        if data.has_cantillation:
            graph.add((ms_uri, HM.has_cantillation, Literal(True, datatype=XSD.boolean)))

        if data.has_incipit:
            graph.add((ms_uri, HM.has_incipit, Literal(data.has_incipit, datatype=XSD.string)))

        if data.has_explicit:
            graph.add((ms_uri, HM.has_explicit, Literal(data.has_explicit, datatype=XSD.string)))

        if data.has_multiple_hands:
            graph.add((ms_uri, HM.has_multiple_hands, Literal(True, datatype=XSD.boolean)))

    def _add_related_works(
        self,
        graph: Graph,
        ms_uri: URIRef,
        related_works: list[dict[str, Any]],
        work_uri: URIRef | None,
    ) -> None:
        """Emit has_linked_work links for 730/related-title entries."""
        for rel in related_works:
            title = rel.get("title")
            if not title:
                continue
            rw_uri = self.uri_gen.work_uri(title)
            graph.add((rw_uri, RDF.type, LRMOO.F1_Work))
            graph.add((rw_uri, HM.has_title, Literal(title, lang="he")))
            graph.add((rw_uri, RDFS.label, Literal(title, lang="he")))
            target = work_uri if work_uri else ms_uri
            graph.add((target, HM.has_linked_work, rw_uri))

    def _add_related_places(
        self, graph: Graph, ms_uri: URIRef, related_places: list[str], prod_uri: URIRef | None
    ) -> None:
        """Emit additional place associations from 751 geographic added entries."""
        for place_name in related_places:
            place_uri = self.uri_gen.place_uri(place_name)
            graph.add((place_uri, RDF.type, CIDOC.E53_Place))
            graph.add((place_uri, RDFS.label, Literal(place_name, lang="he")))
            graph.add((ms_uri, HM.mentions_place, place_uri))

    def _add_condition_notes(
        self, graph: Graph, ms_uri: URIRef, condition_notes: list[str], control_number: str
    ) -> None:
        """Emit ConditionType instances from 583 action notes."""
        for idx, note in enumerate(condition_notes, 1):
            cond_uri = URIRef(f"{HM}Condition_{control_number}_{idx:02d}")
            graph.add((cond_uri, RDF.type, HM.ConditionType))
            graph.add((cond_uri, RDFS.comment, Literal(note, lang="en")))
            graph.add((ms_uri, CIDOC.P44_has_condition, cond_uri))

    def _add_codicological_hierarchy_from_data(
        self, graph: Graph, ms_uri: URIRef, data: "ExtractedData", control_number: str
    ) -> None:
        """Emit CodicologicalHierarchy, AtomicCodicologicalUnit /
        CompositeCodicologicalUnit instances when we have multi-text structure."""
        if not (data.hierarchy_type or data.is_anthology or data.is_multi_volume):
            return
        hier_type = data.hierarchy_type or (
            "ComplexHierarchy" if data.is_anthology else "SimpleHierarchy"
        )
        self.add_codicological_hierarchy(
            graph,
            ms_uri,
            control_number,
            hierarchy_type=hier_type,
            max_depth=2 if data.is_anthology else 1,
        )

    def _format_time_label(self, dates: dict[str, Any]) -> str:
        """Format dates dictionary into a readable label.

        Args:
            dates: Dates dictionary

        Returns:
            Formatted date string
        """
        if "year" in dates:
            return str(dates["year"])

        if "date_start" in dates and "date_end" in dates:
            if dates["date_start"] == dates["date_end"]:
                return str(dates["date_start"])
            return f"{dates['date_start']}-{dates['date_end']}"

        if "date_start" in dates:
            return f"after {dates['date_start']}"

        if "date_end" in dates:
            return f"before {dates['date_end']}"

        if "century" in dates:
            return f"{dates['century']}th century"

        return "unknown"

    # =========================================================================
    # v1.4 ONTOLOGY FEATURES: Cataloging View and Epistemological Framework
    # =========================================================================

    def _add_cataloging_view(
        self,
        graph: Graph,
        ms_uri: URIRef,
        work_uri: URIRef,
        expression_uri: URIRef | None,
        control_number: str,
    ):
        """Add cataloging view paradigm to manuscript.

        This implements the dual paradigm support from v1.4, using the
        bibliographic/cataloging approach (Work-Expression-Manifestation).

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            work_uri: Work URI
            expression_uri: Expression URI (may be None)
            control_number: MARC control number
        """
        # Create cataloging view instance
        cat_view_uri = URIRef(f"{HM}CatalogingView_{control_number}")

        graph.add((cat_view_uri, RDF.type, HM.CatalogingView))
        graph.add(
            (
                cat_view_uri,
                RDFS.label,
                Literal(f"Cataloging view for MS {control_number}", lang="en"),
            )
        )

        # Link manuscript to cataloging view
        graph.add((ms_uri, HM.has_cataloging_perspective, cat_view_uri))

        # Link view to Work and Expression
        graph.add((cat_view_uri, HM.cataloging_work, work_uri))
        if expression_uri:
            graph.add((cat_view_uri, HM.cataloging_expression, expression_uri))

        # Mark as primary paradigm (catalog data uses bibliographic approach)
        graph.add((cat_view_uri, HM.is_primary_paradigm, Literal(True, datatype=XSD.boolean)))

        # Add paradigm note explaining this is catalog-derived data
        graph.add(
            (
                cat_view_uri,
                HM.paradigm_note,
                Literal(
                    "Data derived from NLI catalog using bibliographic Work-Expression model",
                    lang="en",
                ),
            )
        )

    def _add_epistemological_metadata(self, graph: Graph, ms_uri: URIRef, control_number: str):
        """Add epistemological status metadata to indicate catalog source.

        This implements the epistemological framework from v1.4, marking
        catalog-derived data as inherited from catalog (requiring verification).

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            control_number: MARC control number
        """
        # Create an evidence chain for the catalog data
        evidence_chain_uri = URIRef(f"{HM}EvidenceChain_{control_number}")

        graph.add((evidence_chain_uri, RDF.type, HM.EvidenceChain))
        graph.add(
            (
                evidence_chain_uri,
                RDFS.label,
                Literal(f"Evidence chain for MS {control_number}", lang="en"),
            )
        )

        # Link manuscript to evidence chain
        graph.add((ms_uri, HM.has_evidence_chain, evidence_chain_uri))

        # Add catalog source step
        catalog_step_uri = URIRef(f"{HM}CatalogStep_{control_number}")

        graph.add((catalog_step_uri, RDF.type, HM.EvidenceStep))
        graph.add((evidence_chain_uri, HM.evidence_step, catalog_step_uri))

        # Mark as catalog-inherited data
        graph.add(
            (
                catalog_step_uri,
                HM.reasoning_text,
                Literal("Data imported from National Library of Israel MARC catalog", lang="en"),
            )
        )

        # Add attribution source
        graph.add((ms_uri, HM.attribution_source, HM.CatalogAttribution))

        # Add overall epistemological status for the record
        # Catalog data is inherited - not independently verified
        graph.add((ms_uri, HM.has_epistemological_status, HM.CatalogInherited))

        # Physical measurements are factual, attributions are interpretive
        # This is a general marker - specific properties would need individual treatment
        graph.add(
            (ms_uri, HM.is_factual, Literal(False, datatype=XSD.boolean))
        )  # Mixed data - conservative

    def _add_certainty(
        self,
        graph: Graph,
        subject_uri: URIRef,
        certainty_level: str,
        certainty_note: str | None = None,
    ):
        """Add certainty level to an assertion.

        Args:
            graph: RDF graph
            subject_uri: URI of the entity being qualified
            certainty_level: One of "Certain", "Probable", "Possible", "Uncertain"
            certainty_note: Optional textual explanation
        """
        # Map to ontology instance
        certainty_uri = getattr(HM, certainty_level, HM.Uncertain)
        graph.add((subject_uri, HM.has_certainty, certainty_uri))

        if certainty_note:
            graph.add(
                (subject_uri, HM.certainty_note, Literal(certainty_note, datatype=XSD.string))
            )

    def _add_attribution_source(
        self, graph: Graph, subject_uri: URIRef, source_type: str, attributed_by: str | None = None
    ):
        """Add attribution source to an assertion.

        Args:
            graph: RDF graph
            subject_uri: URI of the entity being qualified
            source_type: Type of attribution source (e.g., "CatalogAttribution")
            attributed_by: Optional name/URI of person/system making attribution
        """
        source_uri = getattr(HM, source_type, HM.CatalogAttribution)
        graph.add((subject_uri, HM.attribution_source, source_uri))

        if attributed_by:
            agent_uri = self.uri_gen.person_uri(attributed_by)
            graph.add((subject_uri, HM.attributed_by, agent_uri))

    # =========================================================================
    # v1.4 ONTOLOGY FEATURES: Comprehensive Support
    # =========================================================================

    def add_codicological_hierarchy(
        self,
        graph: Graph,
        ms_uri: URIRef,
        control_number: str,
        hierarchy_type: str = "SimpleHierarchy",
        max_depth: int = 1,
    ) -> URIRef:
        """Add a codicological hierarchy structure to a manuscript.

        This supports the nested CU feature from v1.4, allowing arbitrary
        depth of codicological unit nesting (MTM, Sammelband, etc.).

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            control_number: MARC control number
            hierarchy_type: Type of hierarchy (Simple, Nested, Complex, Fragmentary)
            max_depth: Maximum nesting depth

        Returns:
            URI of the hierarchy entity
        """
        hierarchy_uri = self.uri_gen.codicological_hierarchy_uri(control_number)

        graph.add((hierarchy_uri, RDF.type, HM.CodicologicalHierarchy))
        graph.add((ms_uri, HM.has_hierarchy, hierarchy_uri))

        hierarchy_type_uri = getattr(HM, hierarchy_type, HM.SimpleHierarchy)
        graph.add((hierarchy_uri, HM.hierarchy_type, hierarchy_type_uri))
        graph.add((hierarchy_uri, HM.max_nesting_depth, Literal(max_depth, datatype=XSD.integer)))

        return hierarchy_uri

    def add_codicological_unit(
        self,
        graph: Graph,
        ms_uri: URIRef,
        control_number: str,
        sequence: int,
        is_atomic: bool = True,
        nesting_level: int = 0,
        parent_uri: URIRef | None = None,
        folio_range: str | None = None,
        unit_status: str = "CoreUnit_status",
    ) -> URIRef:
        """Add a codicological unit to a manuscript.

        Supports both atomic (leaf) and composite (with sub-units) CUs.

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            control_number: MARC control number
            sequence: Unit sequence number
            is_atomic: Whether this is an atomic (leaf) unit
            nesting_level: Level in the hierarchy (0 = root)
            parent_uri: Optional parent CU URI for nested units
            folio_range: Optional folio range for this unit
            unit_status: Status of the unit (core, later addition, etc.)

        Returns:
            URI of the codicological unit
        """
        cu_uri = self.uri_gen.codicological_unit_uri(control_number, sequence)

        if is_atomic:
            graph.add((cu_uri, RDF.type, HM.AtomicCodicologicalUnit))
            graph.add((cu_uri, HM.is_atomic_unit, Literal(True, datatype=XSD.boolean)))
        else:
            graph.add((cu_uri, RDF.type, HM.CompositeCodicologicalUnit))
            graph.add((cu_uri, HM.is_atomic_unit, Literal(False, datatype=XSD.boolean)))

        graph.add((cu_uri, RDF.type, HM.Codicological_Unit))
        graph.add((cu_uri, HM.nesting_level, Literal(nesting_level, datatype=XSD.integer)))
        graph.add((cu_uri, HM.unit_sequence, Literal(sequence, datatype=XSD.integer)))

        if parent_uri:
            graph.add((parent_uri, HM.has_sub_unit, cu_uri))
            graph.add((cu_uri, HM.is_sub_unit_of, parent_uri))
        else:
            graph.add((ms_uri, LRMOO.R5_has_component, cu_uri))

        if folio_range:
            graph.add((cu_uri, HM.has_folio_range, Literal(folio_range, datatype=XSD.string)))

        status_uri = getattr(HM, unit_status, HM.CoreUnit_status)
        graph.add((cu_uri, HM.has_unit_status, status_uri))

        return cu_uri

    def add_philological_view(
        self, graph: Graph, ms_uri: URIRef, control_number: str, is_primary: bool = False
    ) -> URIRef:
        """Add a philological view (New Philology paradigm) to a manuscript.

        This treats the manuscript as a unique cultural event rather than
        just a copy of an abstract Work.

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            control_number: MARC control number
            is_primary: Whether this is the primary paradigm for this record

        Returns:
            URI of the philological view
        """
        phil_view_uri = self.uri_gen.philological_view_uri(control_number)

        graph.add((phil_view_uri, RDF.type, HM.PhilologicalView))
        graph.add((ms_uri, HM.has_philological_perspective, phil_view_uri))
        graph.add((phil_view_uri, HM.view_type, HM.PhilologicalParadigm))
        graph.add(
            (phil_view_uri, HM.is_primary_paradigm, Literal(is_primary, datatype=XSD.boolean))
        )
        graph.add(
            (
                phil_view_uri,
                HM.paradigm_note,
                Literal("New Philology view: manuscript as unique cultural event", lang="en"),
            )
        )

        return phil_view_uri

    def add_text_tradition(
        self, graph: Graph, tradition_name: str, description: str | None = None
    ) -> URIRef:
        """Add a Text Tradition entity.

        Text traditions represent transmission streams independent of
        the concept of an "original Work".

        Args:
            graph: RDF graph
            tradition_name: Name of the text tradition
            description: Optional description of the tradition

        Returns:
            URI of the text tradition
        """
        tradition_uri = self.uri_gen.text_tradition_uri(tradition_name)

        graph.add((tradition_uri, RDF.type, HM.TextTradition))
        graph.add((tradition_uri, RDFS.label, Literal(tradition_name, lang="he")))
        graph.add((tradition_uri, HM.tradition_name, Literal(tradition_name, datatype=XSD.string)))

        if description:
            graph.add(
                (tradition_uri, HM.tradition_description, Literal(description, datatype=XSD.string))
            )

        return tradition_uri

    def add_transmission_witness(
        self,
        graph: Graph,
        ms_uri: URIRef,
        tradition_uri: URIRef,
        control_number: str,
        work_title: str,
        expression_uri: URIRef | None = None,
        philological_view_uri: URIRef | None = None,
    ) -> URIRef:
        """Add a Transmission Witness linking manuscript to text tradition.

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            tradition_uri: Text tradition URI
            control_number: MARC control number
            work_title: Title of the work
            expression_uri: Optional Expression URI to link tradition membership
            philological_view_uri: Optional PhilologicalView URI to attach full chain

        Returns:
            URI of the transmission witness
        """
        witness_uri = self.uri_gen.transmission_witness_uri(control_number, work_title)

        graph.add((witness_uri, RDF.type, HM.TransmissionWitness))
        graph.add(
            (
                witness_uri,
                RDFS.label,
                Literal(f"Witness of {work_title} in MS {control_number}", lang="en"),
            )
        )

        graph.add((ms_uri, HM.witnesses, tradition_uri))
        graph.add((ms_uri, HM.has_text_tradition, tradition_uri))
        graph.add((ms_uri, HM.has_philological_witness, witness_uri))
        graph.add((tradition_uri, HM.has_transmission_witness, witness_uri))

        if expression_uri is not None:
            graph.add((expression_uri, HM.belongs_to_tradition, tradition_uri))
            graph.add((tradition_uri, HM.tradition_includes, expression_uri))

        if philological_view_uri is None:
            philological_view_uri = self.add_philological_view(
                graph, ms_uri, control_number, is_primary=False
            )

        graph.add((philological_view_uri, HM.philological_tradition, tradition_uri))
        graph.add((philological_view_uri, HM.philological_witness, witness_uri))

        return witness_uri

    def add_paradigm_bridge(
        self,
        graph: Graph,
        work_uri: URIRef,
        tradition_uri: URIRef,
        work_title: str,
        tradition_name: str,
        justification: str | None = None,
    ) -> URIRef:
        """Add a Paradigm Bridge linking a Work to a TextTradition.

        This explicitly connects bibliographic and philological paradigms.

        Args:
            graph: RDF graph
            work_uri: Work URI (bibliographic paradigm)
            tradition_uri: Text tradition URI (philological paradigm)
            work_title: Title of the work
            tradition_name: Name of the text tradition
            justification: Optional explanation for the link

        Returns:
            URI of the paradigm bridge
        """
        bridge_uri = self.uri_gen.paradigm_bridge_uri(work_title, tradition_name)

        graph.add((bridge_uri, RDF.type, HM.ParadigmBridge))
        graph.add((bridge_uri, HM.has_linked_work, work_uri))
        graph.add((bridge_uri, HM.has_linked_tradition, tradition_uri))
        graph.add((work_uri, HM.paradigm_bridge, bridge_uri))
        graph.add((tradition_uri, HM.paradigm_bridge, bridge_uri))

        if justification:
            graph.add(
                (bridge_uri, HM.paradigm_justification, Literal(justification, datatype=XSD.string))
            )

        return bridge_uri

    def add_textual_variant(
        self,
        graph: Graph,
        ms_uri: URIRef,
        control_number: str,
        location: str,
        variant_text: str,
        standard_text: str | None = None,
        significance: str = "Lexical_variant",
        expression_uri: URIRef | None = None,
    ) -> URIRef:
        """Add a Textual Variant to the manuscript.

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            control_number: MARC control number
            location: Location of the variant (e.g., "15r_L10")
            variant_text: The variant reading
            standard_text: Optional standard/expected reading
            significance: Type of variant (Orthographic, Lexical, etc.)

        Returns:
            URI of the textual variant
        """
        variant_uri = self.uri_gen.textual_variant_uri(control_number, location)

        graph.add((variant_uri, RDF.type, HM.TextualVariant))
        if expression_uri is not None:
            graph.add((expression_uri, HM.has_variant_reading, variant_uri))
        else:
            graph.add((ms_uri, HM.has_variant_reading, variant_uri))

        graph.add((variant_uri, HM.variant_text, Literal(variant_text, lang="he")))

        if standard_text:
            graph.add((variant_uri, HM.standard_text, Literal(standard_text, lang="he")))

        significance_uri = getattr(HM, significance, HM.Lexical_variant)
        graph.add((variant_uri, HM.variant_significance, significance_uri))

        location_uri = self.uri_gen.text_location_uri(control_number, location)
        graph.add((variant_uri, HM.variant_at_location, location_uri))

        return variant_uri

    def add_scribal_intervention(
        self,
        graph: Graph,
        ms_uri: URIRef,
        control_number: str,
        sequence: int,
        intervention_type: str,
        location: str | None = None,
        description: str | None = None,
        by_scribe: str | None = None,
    ) -> URIRef:
        """Add a Scribal Intervention (correction, addition, etc.).

        Args:
            graph: RDF graph
            ms_uri: Manuscript URI
            control_number: MARC control number
            sequence: Intervention sequence number
            intervention_type: Type (Correction_type, Erasure_type, etc.)
            location: Optional location in manuscript
            description: Optional description of the intervention
            by_scribe: Optional name of the scribe who made the intervention

        Returns:
            URI of the scribal intervention
        """
        intervention_uri = self.uri_gen.scribal_intervention_uri(control_number, sequence)

        graph.add((intervention_uri, RDF.type, HM.ScribalIntervention))
        graph.add((ms_uri, HM.has_scribal_intervention, intervention_uri))

        type_uri = getattr(HM, intervention_type, HM.Correction_type)
        graph.add((intervention_uri, RDF.type, type_uri))

        if location:
            location_uri = self.uri_gen.text_location_uri(control_number, location)
            graph.add((intervention_uri, HM.intervention_location, location_uri))

        if description:
            graph.add(
                (
                    intervention_uri,
                    HM.intervention_description,
                    Literal(description, datatype=XSD.string),
                )
            )

        if by_scribe:
            scribe_uri = self.uri_gen.person_uri(by_scribe)
            graph.add((intervention_uri, HM.intervention_by, scribe_uri))

        return intervention_uri

    def add_canonical_reference(
        self,
        graph: Graph,
        expression_uri: URIRef,
        hierarchy_type: str,
        canonical_start: str,
        canonical_end: str | None = None,
    ) -> URIRef:
        """Add a Canonical Reference to an expression (e.g., Bible Genesis 1:1-2:4).

        Args:
            graph: RDF graph
            expression_uri: Expression URI
            hierarchy_type: Type of hierarchy (Bible, Mishnah, Talmud, etc.)
            canonical_start: Start of canonical range (e.g., "Genesis_1_1")
            canonical_end: Optional end of range

        Returns:
            URI of the canonical reference
        """
        ref_string = (
            canonical_start if not canonical_end else f"{canonical_start}_to_{canonical_end}"
        )
        ref_uri = self.uri_gen.canonical_reference_uri(hierarchy_type, ref_string)

        graph.add((ref_uri, RDF.type, HM.CanonicalReference))
        graph.add((expression_uri, HM.covers_canonical_range, ref_uri))

        hierarchy_uri = getattr(HM, f"{hierarchy_type}_hierarchy", None)
        if hierarchy_uri:
            graph.add((ref_uri, HM.canonical_hierarchy, hierarchy_uri))

        graph.add((ref_uri, HM.canonical_start, Literal(canonical_start, datatype=XSD.string)))

        if canonical_end:
            graph.add((ref_uri, HM.canonical_end, Literal(canonical_end, datatype=XSD.string)))

        return ref_uri

    def add_detailed_evidence_chain(
        self,
        graph: Graph,
        subject_uri: URIRef,
        control_number: str,
        data_field: str,
        epistemological_status: str,
        interpretation_method: str | None = None,
        evidence_strength: float | None = None,
        reasoning_text: str | None = None,
    ) -> URIRef:
        """Add a detailed evidence chain with full provenance.

        This implements the epistemological framework for distinguishing
        facts from interpretations.

        Args:
            graph: RDF graph
            subject_uri: URI of the entity being evidenced
            control_number: MARC control number
            data_field: Name of the data field
            epistemological_status: Status (DirectObservation, ScholarlyInterpretation, etc.)
            interpretation_method: Method used for interpretation
            evidence_strength: Strength as decimal 0-1
            reasoning_text: Textual explanation of reasoning

        Returns:
            URI of the evidence chain
        """
        chain_uri = self.uri_gen.evidence_chain_uri(control_number, data_field)

        graph.add((chain_uri, RDF.type, HM.EvidenceChain))
        graph.add((subject_uri, HM.has_evidence_chain, chain_uri))
        graph.add((chain_uri, HM.supports_assertion, subject_uri))

        status_uri = getattr(HM, epistemological_status, HM.CatalogInherited)
        graph.add((subject_uri, HM.has_epistemological_status, status_uri))

        data_cat = DATA_CATEGORIES.get(data_field.lower(), "WorkIdentification")
        data_cat_uri = getattr(HM, data_cat, HM.WorkIdentification)
        graph.add((subject_uri, HM.data_category, data_cat_uri))

        is_factual = DATA_FACTUALITY.get(data_field.lower(), False)
        graph.add((subject_uri, HM.is_factual, Literal(is_factual, datatype=XSD.boolean)))

        if interpretation_method:
            method_uri = getattr(HM, interpretation_method, None)
            if method_uri:
                graph.add((subject_uri, HM.interpretation_method, method_uri))

        if evidence_strength is not None:
            graph.add(
                (chain_uri, HM.evidence_strength, Literal(evidence_strength, datatype=XSD.decimal))
            )

        if reasoning_text:
            graph.add((chain_uri, HM.reasoning_text, Literal(reasoning_text, datatype=XSD.string)))

        return chain_uri
