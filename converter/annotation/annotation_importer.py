#!/usr/bin/env python3
"""
Scholarly Annotation Importer

This module allows scholars to add annotations to converted manuscripts.
Annotations can be provided via CSV files and merged into the TTL output.

Supported annotation types:
1. Certainty/Attribution - confidence levels and sources
2. Text Traditions - transmission witnesses and variants
3. Scribal Interventions - hand changes, corrections, marginalia
4. Canonical References - biblical, talmudic, halachic links
5. Textual Relationships - variants, parallels, abridgments
6. Foreign Unit Marking - core vs. added content
"""

import csv
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from rdflib import RDF, XSD, Graph, Literal, URIRef

from ..config.namespaces import HM, bind_namespaces
from ..rdf.graph_builder import GraphBuilder
from ..transformer.uri_generator import UriGenerator


class AnnotationType(Enum):
    CERTAINTY = "certainty"
    TEXT_TRADITION = "text_tradition"
    SCRIBAL_INTERVENTION = "scribal_intervention"
    CANONICAL_REFERENCE = "canonical_reference"
    TEXTUAL_RELATIONSHIP = "textual_relationship"
    FOREIGN_UNIT = "foreign_unit"


@dataclass
class CertaintyAnnotation:
    """Annotation for certainty/confidence levels."""

    manuscript_id: str
    certainty_level: str  # Certain, Probable, Possible, Uncertain
    attribution_source: str  # ExpertAttribution, AIAttribution, CatalogAttribution, ColophonAttribution, PaleographicAttribution
    certainty_percentage: int | None = None
    note: str | None = None


@dataclass
class TextTraditionAnnotation:
    """Annotation for text tradition/philological data."""

    manuscript_id: str
    tradition_name: str
    siglum: str | None = None
    work_title: str | None = None
    variant_text: str | None = None
    standard_text: str | None = None
    folio: str | None = None
    line: int | None = None
    significance: str | None = (
        None  # Orthographic_variant, Lexical_variant, Syntactic_variant, Semantic_variant, Addition_variant, Omission_variant
    )


@dataclass
class ScribalInterventionAnnotation:
    """Annotation for scribal interventions."""

    manuscript_id: str
    intervention_type: str  # HandChange, TextCorrection, MarginalAddition, or *_type (Correction_type, Erasure_type, etc.)
    folio_range: str | None = None
    description: str | None = None
    scribe_name: str | None = None


@dataclass
class CanonicalReferenceAnnotation:
    """Annotation for canonical text references."""

    manuscript_id: str
    reference_type: str  # Biblical, Talmudic, Mishnaic, Halachic, Zoharic
    book_name: str | None = None
    chapter: str | None = None
    verse: str | None = None
    tractate: str | None = None
    folio: str | None = None


@dataclass
class TextualRelationshipAnnotation:
    """Annotation for relationships between texts."""

    source_id: str
    target_id: str
    relationship_type: str  # variant_of, abridgment_of, adaptation_of, parallels, possibly_realises


@dataclass
class ForeignUnitAnnotation:
    """Annotation for marking foreign/added units."""

    manuscript_id: str
    unit_id: str
    is_foreign: bool
    status: str  # CoreUnit_status, LaterAddition_status, BinderAddition_status, UnrelatedFragment_status, ProtectiveLeaf_status
    addition_period: str | None = None
    folio_range: str | None = None


class AnnotationImporter:
    """Import scholarly annotations into RDF graphs."""

    def __init__(self, graph: Graph | None = None):
        self.graph = graph or Graph()
        self._bind_namespaces()
        self.uri_gen = UriGenerator()
        self.graph_builder = GraphBuilder(
            self.uri_gen, add_epistemological_status=False, add_cataloging_view=False
        )
        self._annotation_count = 0

    def _bind_namespaces(self):
        bind_namespaces(self.graph)

    def _ms_uri(self, ms_id: str) -> URIRef:
        """Generate manuscript URI from ID."""
        if ms_id.startswith("http"):
            return URIRef(ms_id)
        normalized = ms_id[3:] if ms_id.startswith("MS_") else ms_id
        return HM[f"MS_{normalized}"]

    def add_certainty(self, ann: CertaintyAnnotation):
        """Add certainty/attribution annotation."""
        ms_uri = self._ms_uri(ann.manuscript_id)

        # Map certainty level to ontology individual
        level_map = {
            "certain": HM.Certain,
            "probable": HM.Probable,
            "possible": HM.Possible,
            "uncertain": HM.Uncertain,
        }

        source_map = {
            "expert": HM.ExpertAttribution,
            "expertattribution": HM.ExpertAttribution,
            "ai": HM.AIAttribution,
            "aiattribution": HM.AIAttribution,
            "catalog": HM.CatalogAttribution,
            "catalogattribution": HM.CatalogAttribution,
            "colophon": HM.ColophonAttribution,
            "colophonation": HM.ColophonAttribution,
            "colophonationtribution": HM.ColophonAttribution,
            "paleographic": HM.PaleographicAttribution,
            "paleographicattribution": HM.PaleographicAttribution,
            "scholarly": HM.ExpertAttribution,
        }

        level_key = ann.certainty_level.strip().lower()
        source_key = ann.attribution_source.strip().lower().replace(" ", "").replace("_", "")
        level = level_map.get(level_key)
        source = source_map.get(source_key)

        if level:
            self.graph.add((ms_uri, HM.has_certainty, level))
        if source:
            self.graph.add((ms_uri, HM.attribution_source, source))
        if ann.certainty_percentage is not None:
            self.graph.add(
                (
                    ms_uri,
                    HM.certainty_percentage,
                    Literal(ann.certainty_percentage, datatype=XSD.integer),
                )
            )
        if ann.note:
            self.graph.add((ms_uri, HM.certainty_note, Literal(ann.note)))

        self._annotation_count += 1

    def add_text_tradition(self, ann: TextTraditionAnnotation):
        """Add text tradition annotation."""
        ms_uri = self._ms_uri(ann.manuscript_id)
        tradition_uri = self.graph_builder.add_text_tradition(self.graph, ann.tradition_name)

        work_title = ann.work_title or ann.tradition_name
        expression_uri = (
            self.uri_gen.expression_uri(work_title, ann.manuscript_id) if work_title else None
        )

        self.graph_builder.add_transmission_witness(
            self.graph,
            ms_uri,
            tradition_uri,
            ann.manuscript_id,
            work_title,
            expression_uri=expression_uri,
        )

        # Add variant reading if provided
        if ann.variant_text:
            location = ann.folio or "unknown"
            if ann.folio and ann.line is not None:
                location = f"{ann.folio}_L{ann.line}"

            significance = "Lexical_variant"
            if ann.significance:
                sig_key = ann.significance.strip().lower().replace(" ", "_")
                sig_map = {
                    "major": "Lexical_variant",
                    "minor": "Orthographic_variant",
                    "orthographic": "Orthographic_variant",
                    "orthographic_variant": "Orthographic_variant",
                    "lexical": "Lexical_variant",
                    "lexical_variant": "Lexical_variant",
                    "syntactic": "Syntactic_variant",
                    "syntactic_variant": "Syntactic_variant",
                    "semantic": "Semantic_variant",
                    "semantic_variant": "Semantic_variant",
                    "addition": "Addition_variant",
                    "addition_variant": "Addition_variant",
                    "omission": "Omission_variant",
                    "omission_variant": "Omission_variant",
                }
                significance = sig_map.get(sig_key, significance)

            self.graph_builder.add_textual_variant(
                self.graph,
                ms_uri,
                ann.manuscript_id,
                location,
                ann.variant_text,
                standard_text=ann.standard_text,
                significance=significance,
                expression_uri=expression_uri,
            )

        self._annotation_count += 1

    def add_scribal_intervention(self, ann: ScribalInterventionAnnotation):
        """Add scribal intervention annotation."""
        ms_uri = self._ms_uri(ann.manuscript_id)
        seq = self._annotation_count + 1
        raw_type = ann.intervention_type.strip()
        type_key = raw_type.lower().replace(" ", "").replace("_", "")
        type_map = {
            "handchange": "HandChange",
            "textcorrection": "TextCorrection",
            "marginaladdition": "MarginalAddition",
            "illumination": "ScribalIntervention",
            "correction": "Correction_type",
            "correctiontype": "Correction_type",
            "erasure": "Erasure_type",
            "erasuretype": "Erasure_type",
            "interlinearaddition": "Interlinear_addition_type",
            "interlinearadditiontype": "Interlinear_addition_type",
            "marginalgloss": "Marginal_gloss_type",
            "marginalglosstype": "Marginal_gloss_type",
            "laterhand": "Later_hand_type",
            "laterhandtype": "Later_hand_type",
        }
        intervention_type = type_map.get(type_key, raw_type)

        self.graph_builder.add_scribal_intervention(
            self.graph,
            ms_uri,
            ann.manuscript_id,
            seq,
            intervention_type,
            location=ann.folio_range,
            description=ann.description,
            by_scribe=ann.scribe_name,
        )

        self._annotation_count += 1

    def add_canonical_reference(self, ann: CanonicalReferenceAnnotation):
        """Add canonical text reference."""
        ms_uri = self._ms_uri(ann.manuscript_id)
        reference_parts = []
        if ann.book_name:
            reference_parts.append(ann.book_name)
        if ann.chapter:
            reference_parts.append(str(ann.chapter))
        if ann.verse:
            reference_parts.append(str(ann.verse))
        if ann.tractate:
            reference_parts.append(ann.tractate)
        if ann.folio:
            reference_parts.append(ann.folio)
        reference_string = (
            "_".join(reference_parts)
            if reference_parts
            else f"{ann.manuscript_id}_{self._annotation_count}"
        )
        ref_uri = self.uri_gen.canonical_reference_uri(ann.reference_type, reference_string)

        type_map = {
            "biblical": HM.BiblicalReference,
            "talmudic": HM.TalmudicReference,
            "mishnaic": HM.MishnaicReference,
            "halachic": HM.HalachicReference,
            "zoharic": HM.CanonicalReference,
        }

        ref_type = type_map.get(ann.reference_type.lower(), HM.CanonicalReference)

        self.graph.add((ref_uri, RDF.type, ref_type))
        self.graph.add((ms_uri, HM.covers_canonical_range, ref_uri))

        if ann.book_name:
            self.graph.add((ref_uri, HM.book_name, Literal(ann.book_name)))
        if ann.chapter:
            self.graph.add((ref_uri, HM.chapter_number, Literal(ann.chapter)))
        if ann.verse:
            self.graph.add((ref_uri, HM.verse_number, Literal(ann.verse)))
        if ann.tractate:
            self.graph.add((ref_uri, HM.tractate_name, Literal(ann.tractate)))
        if ann.folio:
            self.graph.add((ref_uri, HM.talmud_folio, Literal(ann.folio)))

        self._annotation_count += 1

    def add_textual_relationship(self, ann: TextualRelationshipAnnotation):
        """Add textual relationship annotation."""
        source_uri = self._ms_uri(ann.source_id)
        target_uri = self._ms_uri(ann.target_id)

        rel_map = {
            "variant_of": HM.is_variant_of,
            "abridgment_of": HM.is_abridgment_of,
            "adaptation_of": HM.is_adaptation_of,
            "parallels": HM.parallels,
            "possibly_realises": HM.possibly_realises,
        }

        rel = rel_map.get(ann.relationship_type.lower())
        if rel:
            self.graph.add((source_uri, rel, target_uri))

        self._annotation_count += 1

    def add_foreign_unit(self, ann: ForeignUnitAnnotation):
        """Add foreign unit marking."""
        self._ms_uri(ann.manuscript_id)
        if ann.unit_id.startswith("http"):
            unit_uri = URIRef(ann.unit_id)
        elif ann.unit_id.startswith(("CU_", "PU_", "BibUnit_", "MS_")):
            unit_uri = HM[ann.unit_id]
        elif ann.unit_id.isdigit():
            unit_uri = HM[f"CU_{ann.manuscript_id}_{ann.unit_id}"]
        else:
            unit_uri = HM[f"Unit_{ann.unit_id}"]

        status_map = {
            "coreunit": HM.CoreUnit_status,
            "lateraddition": HM.LaterAddition_status,
            "binderaddition": HM.BinderAddition_status,
            "unrelatedfragment": HM.UnrelatedFragment_status,
            "replacementleaf": HM.UnrelatedFragment_status,
            "protectiveleaf": HM.ProtectiveLeaf_status,
        }

        status = status_map.get(ann.status.lower().replace(" ", ""))

        self.graph.add(
            (unit_uri, HM.is_foreign_addition, Literal(ann.is_foreign, datatype=XSD.boolean))
        )

        if status:
            self.graph.add((unit_uri, HM.has_unit_status, status))

        if ann.addition_period:
            self.graph.add((unit_uri, HM.addition_period, Literal(ann.addition_period)))

        if ann.folio_range:
            self.graph.add((unit_uri, HM.has_folio_range, Literal(ann.folio_range)))

        self._annotation_count += 1

    def import_from_csv(self, csv_path: Path, annotation_type: AnnotationType) -> int:
        """Import annotations from a CSV file.

        Returns number of annotations imported.
        """
        count_before = self._annotation_count

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                if annotation_type == AnnotationType.CERTAINTY:
                    ann = CertaintyAnnotation(
                        manuscript_id=row["manuscript_id"],
                        certainty_level=row["certainty_level"],
                        attribution_source=row["attribution_source"],
                        certainty_percentage=int(row["certainty_percentage"])
                        if row.get("certainty_percentage")
                        else None,
                        note=row.get("note"),
                    )
                    self.add_certainty(ann)

                elif annotation_type == AnnotationType.TEXT_TRADITION:
                    ann = TextTraditionAnnotation(
                        manuscript_id=row["manuscript_id"],
                        tradition_name=row["tradition_name"],
                        siglum=row.get("siglum"),
                        work_title=row.get("work_title"),
                        variant_text=row.get("variant_text"),
                        standard_text=row.get("standard_text"),
                        folio=row.get("folio"),
                        line=int(row["line"]) if row.get("line") else None,
                        significance=row.get("significance"),
                    )
                    self.add_text_tradition(ann)

                elif annotation_type == AnnotationType.SCRIBAL_INTERVENTION:
                    ann = ScribalInterventionAnnotation(
                        manuscript_id=row["manuscript_id"],
                        intervention_type=row["intervention_type"],
                        folio_range=row.get("folio_range"),
                        description=row.get("description"),
                        scribe_name=row.get("scribe_name"),
                    )
                    self.add_scribal_intervention(ann)

                elif annotation_type == AnnotationType.CANONICAL_REFERENCE:
                    ann = CanonicalReferenceAnnotation(
                        manuscript_id=row["manuscript_id"],
                        reference_type=row["reference_type"],
                        book_name=row.get("book_name"),
                        chapter=row.get("chapter"),
                        verse=row.get("verse"),
                        tractate=row.get("tractate"),
                        folio=row.get("folio"),
                    )
                    self.add_canonical_reference(ann)

                elif annotation_type == AnnotationType.TEXTUAL_RELATIONSHIP:
                    ann = TextualRelationshipAnnotation(
                        source_id=row["source_id"],
                        target_id=row["target_id"],
                        relationship_type=row["relationship_type"],
                    )
                    self.add_textual_relationship(ann)

                elif annotation_type == AnnotationType.FOREIGN_UNIT:
                    ann = ForeignUnitAnnotation(
                        manuscript_id=row["manuscript_id"],
                        unit_id=row["unit_id"],
                        is_foreign=row["is_foreign"].lower() in ("true", "yes", "1"),
                        status=row["status"],
                        addition_period=row.get("addition_period"),
                        folio_range=row.get("folio_range"),
                    )
                    self.add_foreign_unit(ann)

        return self._annotation_count - count_before

    def import_from_json(self, json_path: Path) -> int:
        """Import annotations from a JSON file.

        Expected format:
        {
            "certainty": [...],
            "text_tradition": [...],
            "scribal_intervention": [...],
            ...
        }
        """
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        count_before = self._annotation_count

        for ann_data in data.get("certainty", []):
            self.add_certainty(CertaintyAnnotation(**ann_data))

        for ann_data in data.get("text_tradition", []):
            self.add_text_tradition(TextTraditionAnnotation(**ann_data))

        for ann_data in data.get("scribal_intervention", []):
            self.add_scribal_intervention(ScribalInterventionAnnotation(**ann_data))

        for ann_data in data.get("canonical_reference", []):
            self.add_canonical_reference(CanonicalReferenceAnnotation(**ann_data))

        for ann_data in data.get("textual_relationship", []):
            self.add_textual_relationship(TextualRelationshipAnnotation(**ann_data))

        for ann_data in data.get("foreign_unit", []):
            self.add_foreign_unit(ForeignUnitAnnotation(**ann_data))

        return self._annotation_count - count_before

    def merge_with_ttl(self, ttl_path: Path, output_path: Path | None = None) -> Path:
        """Merge annotations with an existing TTL file.

        Returns path to output file.
        """
        # Load existing graph
        existing = Graph()
        existing.parse(ttl_path, format="turtle")

        # Add all annotation triples
        for triple in self.graph:
            existing.add(triple)

        # Determine output path
        if output_path is None:
            output_path = ttl_path.parent / f"{ttl_path.stem}_annotated.ttl"

        # Serialize
        existing.serialize(output_path, format="turtle")

        return output_path

    @property
    def annotation_count(self) -> int:
        return self._annotation_count


def create_sample_annotation_files(output_dir: Path):
    """Create sample CSV templates for each annotation type."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Certainty annotations template
    certainty_csv = output_dir / "certainty_annotations.csv"
    with open(certainty_csv, "w", encoding="utf-8") as f:
        f.write("manuscript_id,certainty_level,attribution_source,certainty_percentage,note\n")
        f.write(
            "990000400180205171,Probable,ExpertAttribution,85,Attribution based on paleographic analysis\n"
        )
        f.write(
            "990000400190205171,Certain,ColophonAttribution,100,Colophon explicitly names the scribe\n"
        )

    # Text tradition template
    tradition_csv = output_dir / "text_tradition_annotations.csv"
    with open(tradition_csv, "w", encoding="utf-8") as f:
        f.write(
            "manuscript_id,tradition_name,work_title,variant_text,standard_text,folio,line,significance\n"
        )
        f.write(
            "990000400180205171,Ashkenazi Prayer Tradition,תפילה,ברוך אתה ה׳,ברוך אתה יי,12r,5,Orthographic_variant\n"
        )

    # Scribal intervention template
    intervention_csv = output_dir / "scribal_intervention_annotations.csv"
    with open(intervention_csv, "w", encoding="utf-8") as f:
        f.write("manuscript_id,intervention_type,folio_range,description,scribe_name\n")
        f.write("990000400180205171,HandChange,45r-end,Different hand from folio 45 onwards,\n")
        f.write("990000400190205171,MarginalAddition,23v,Marginal gloss in later hand,\n")

    # Canonical reference template
    canonical_csv = output_dir / "canonical_reference_annotations.csv"
    with open(canonical_csv, "w", encoding="utf-8") as f:
        f.write("manuscript_id,reference_type,book_name,chapter,verse,tractate,folio\n")
        f.write("990000400180205171,Biblical,Genesis,1,1,,\n")
        f.write("990000400190205171,Talmudic,,,Berakhot,2a\n")

    # Textual relationship template
    relationship_csv = output_dir / "textual_relationship_annotations.csv"
    with open(relationship_csv, "w", encoding="utf-8") as f:
        f.write("source_id,target_id,relationship_type\n")
        f.write("990000400180205171,990000400190205171,variant_of\n")

    # Foreign unit template
    foreign_csv = output_dir / "foreign_unit_annotations.csv"
    with open(foreign_csv, "w", encoding="utf-8") as f:
        f.write("manuscript_id,unit_id,is_foreign,status,addition_period,folio_range\n")
        f.write("990000400180205171,unit_1,false,CoreUnit,,1r-44v\n")
        f.write("990000400180205171,unit_2,true,LaterAddition,15th century,45r-50v\n")

    print(f"Created sample annotation templates in {output_dir}")
    return [
        certainty_csv,
        tradition_csv,
        intervention_csv,
        canonical_csv,
        relationship_csv,
        foreign_csv,
    ]


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--create-templates":
        output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("annotation_templates")
        create_sample_annotation_files(output_dir)
    else:
        print("Usage:")
        print("  python annotation_importer.py --create-templates [output_dir]")
        print("\nThis module provides the AnnotationImporter class for adding")
        print("scholarly annotations to converted TTL files.")
