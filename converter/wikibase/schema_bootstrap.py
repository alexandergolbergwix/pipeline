"""Offline schema bootstrap drafts for an HMO project Wikibase."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_HMO = "https://w3id.org/hmo/"
_CIDOC = "http://www.cidoc-crm.org/cidoc-crm/"
_LRMOO = "http://iflastandards.info/ns/lrm/lrmoo/"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_DCTERMS = "http://purl.org/dc/terms/"


@dataclass(frozen=True)
class WikibaseSchemaClassDraft:
    """Draft description of one HMO-aligned Wikibase class item."""

    local_id: str
    label: str
    description: str
    source_uri: str
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation of the class draft."""
        return {
            "local_id": self.local_id,
            "label": self.label,
            "description": self.description,
            "source_uri": self.source_uri,
            "aliases": self.aliases,
        }


@dataclass(frozen=True)
class WikibaseSchemaPropertyDraft:
    """Draft description of one HMO-aligned Wikibase property."""

    local_id: str
    label: str
    description: str
    datatype: str
    source_uri: str
    aliases: list[str] = field(default_factory=list)
    expected_value: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation of the property draft."""
        data: dict[str, object] = {
            "local_id": self.local_id,
            "label": self.label,
            "description": self.description,
            "datatype": self.datatype,
            "source_uri": self.source_uri,
            "aliases": self.aliases,
        }
        if self.expected_value is not None:
            data["expected_value"] = self.expected_value
        return data


@dataclass(frozen=True)
class WikibaseSchemaBootstrap:
    """Export-only HMO Wikibase schema bootstrap package."""

    classes: list[WikibaseSchemaClassDraft]
    properties: list[WikibaseSchemaPropertyDraft]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable bootstrap package."""
        return {
            "classes": [schema_class.to_dict() for schema_class in self.classes],
            "properties": [schema_property.to_dict() for schema_property in self.properties],
            "notes": self.notes,
        }


def build_default_hmo_schema_bootstrap() -> WikibaseSchemaBootstrap:
    """Build the first offline schema draft for an HMO project Wikibase."""
    return WikibaseSchemaBootstrap(
        classes=[
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_Manuscript",
                label="Manuscript",
                description="Physical manuscript as an HMO/LRMoo manifestation singleton.",
                source_uri=f"{_LRMOO}F4_Manifestation_Singleton",
                aliases=["Manifestation Singleton", "Bibliographic Unit"],
            ),
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_Work",
                label="Work",
                description="Intellectual or artistic work carried by manuscript witnesses.",
                source_uri=f"{_LRMOO}F1_Work",
            ),
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_Expression",
                label="Expression",
                description="Realization of a work, including textual form and language.",
                source_uri=f"{_LRMOO}F2_Expression",
            ),
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_CodicologicalUnit",
                label="Codicological Unit",
                description="Material production unit within a manuscript volume.",
                source_uri=f"{_HMO}Codicological_Unit",
                aliases=["Codicological unit", "CU"],
            ),
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_Person",
                label="Person",
                description="Individual associated with manuscript creation, transmission, ownership, or scholarship.",
                source_uri=f"{_CIDOC}E21_Person",
            ),
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_Place",
                label="Place",
                description="Geographic place associated with manuscript production, provenance, or custody.",
                source_uri=f"{_CIDOC}E53_Place",
            ),
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_Organization",
                label="Organization",
                description="Group, institution, collection, or corporate body associated with a manuscript.",
                source_uri=f"{_CIDOC}E74_Group",
                aliases=["Group", "Institution"],
            ),
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_Event",
                label="Event",
                description="Production, acquisition, provenance, or other manuscript-related event.",
                source_uri=f"{_CIDOC}E5_Event",
            ),
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_TransmissionWitness",
                label="Transmission Witness",
                description="HMO philological witness connecting a text tradition to a manuscript carrier.",
                source_uri=f"{_HMO}TransmissionWitness",
            ),
            WikibaseSchemaClassDraft(
                local_id="ClassDraft_TextTradition",
                label="Text Tradition",
                description="Philological text tradition represented by one or more transmission witnesses.",
                source_uri=f"{_HMO}TextTradition",
            ),
        ],
        properties=[
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_InstanceOf",
                label="instance of",
                description="Links an item to its HMO, CIDOC CRM, or LRMoo class.",
                datatype="wikibase-item",
                source_uri=f"{_RDF}type",
                expected_value="schema class item",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_SourceUri",
                label="source URI",
                description="Original RDF URI for the exported HMO resource.",
                datatype="url",
                source_uri=f"{_DCTERMS}identifier",
                aliases=["RDF URI"],
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_LocalHmoUri",
                label="local HMO URI",
                description="Stable local HMO URI minted by the MHM pipeline.",
                datatype="url",
                source_uri=f"{_HMO}local_hmo_uri",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_Label",
                label="label",
                description="Human-readable label preserved from the RDF graph.",
                datatype="monolingualtext",
                source_uri=f"{_RDFS}label",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_HasPart",
                label="has part",
                description="Connects a manuscript or unit to a component part.",
                datatype="wikibase-item",
                source_uri=f"{_HMO}has_part",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_PartOf",
                label="part of",
                description="Connects a component entity to its containing manuscript or unit.",
                datatype="wikibase-item",
                source_uri=f"{_HMO}part_of",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_CarriedBy",
                label="carried by",
                description="Links a textual witness or expression to its physical manuscript carrier.",
                datatype="wikibase-item",
                source_uri=f"{_HMO}carried_by",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_Embodies",
                label="embodies",
                description="Links a manuscript carrier to the expression or work it embodies.",
                datatype="wikibase-item",
                source_uri=f"{_LRMOO}R4_embodies",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_CreatedBy",
                label="created by",
                description="Links a manuscript, expression, or event to a creator or scribe.",
                datatype="wikibase-item",
                source_uri=f"{_CIDOC}P14_carried_out_by",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_AssociatedPerson",
                label="associated person",
                description="Person associated with a manuscript, event, provenance note, or scholarly assertion.",
                datatype="wikibase-item",
                source_uri=f"{_HMO}associated_person",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_AssociatedPlace",
                label="associated place",
                description="Place associated with production, provenance, custody, or description.",
                datatype="wikibase-item",
                source_uri=f"{_HMO}associated_place",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_AssociatedDate",
                label="associated date",
                description="Date or date text associated with a manuscript-related fact.",
                datatype="time",
                source_uri=f"{_HMO}associated_date",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_AuthorityId",
                label="authority ID",
                description="Identifier from an authority source such as Mazal/NLI, VIAF, or KIMA.",
                datatype="external-id",
                source_uri=f"{_HMO}authority_id",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_ExternalIdentifier",
                label="external identifier",
                description="External manuscript, catalog, collection, or authority identifier.",
                datatype="external-id",
                source_uri=f"{_DCTERMS}identifier",
            ),
            WikibaseSchemaPropertyDraft(
                local_id="PropertyDraft_EvidenceSourceNote",
                label="evidence/source note",
                description="Textual note recording the evidence or source for an exported assertion.",
                datatype="monolingualtext",
                source_uri=f"{_HMO}evidence_source_note",
                aliases=["source note", "evidence note"],
            ),
        ],
        notes=[
            "Offline schema bootstrap only: this module performs no network calls and no Wikibase writes.",
            "Local IDs are stable draft identifiers, not Wikibase Q/P identifiers.",
        ],
    )


def export_schema_bootstrap_to_file(path: Path) -> Path:
    """Write the default HMO schema bootstrap JSON to a file."""
    bootstrap = build_default_hmo_schema_bootstrap()
    path.write_text(
        json.dumps(bootstrap.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path
