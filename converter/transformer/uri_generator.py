"""URI generator for consistent RDF entity identifiers."""

import logging
import re
import unicodedata
from typing import TYPE_CHECKING

from rdflib import URIRef

from ..config.namespaces import HM

if TYPE_CHECKING:
    from ..authority.mazal_matcher import MazalMatcher

logger = logging.getLogger(__name__)

# NLI Authority base URL
NLI_AUTHORITY_BASE = "https://www.nli.org.il/en/authorities/"


class UriGenerator:
    """Generates consistent URIs for RDF entities.

    Supports integration with Mazal (NLI) authority files for
    generating official NLI URIs when matches are found.
    """

    def __init__(self, namespace: str = str(HM), mazal_matcher: "MazalMatcher" = None):
        """Initialize the URI generator.

        Args:
            namespace: Base namespace for generated URIs
            mazal_matcher: Optional MazalMatcher for NLI authority lookups
        """
        self.namespace = namespace
        self.mazal_matcher = mazal_matcher
        self._uri_cache = {}

    def normalize_string(self, text: str) -> str:
        """Normalize a string for use in URIs.

        - Removes diacritics
        - Replaces spaces with underscores
        - Removes special characters
        - Converts to lowercase for consistency

        Args:
            text: Input text to normalize

        Returns:
            Normalized string safe for URI use
        """
        if not text:
            return ""

        text = text.strip()

        normalized = unicodedata.normalize("NFD", text)
        ascii_text = "".join(
            char for char in normalized if unicodedata.category(char) != "Mn" or ord(char) < 128
        )

        result = re.sub(r"[^\w\s-]", "", ascii_text)
        result = re.sub(r"[-\s]+", "_", result)
        result = result.strip("_")

        return result

    def normalize_hebrew(self, text: str) -> str:
        """Normalize Hebrew text for URI generation.

        Keeps Hebrew characters but removes niqqud and special marks.

        Args:
            text: Hebrew text

        Returns:
            Normalized Hebrew string
        """
        if not text:
            return ""

        text = text.strip()

        result = "".join(
            char
            for char in text
            if not (0x0591 <= ord(char) <= 0x05C7 and ord(char) not in range(0x05D0, 0x05EB))
        )

        result = re.sub(r"[^\w\s\u0590-\u05FF-]", "", result)
        result = re.sub(r"[-\s]+", "_", result)
        result = result.strip("_")

        return result

    def manuscript_uri(self, identifier: str) -> URIRef:
        """Generate URI for a manuscript.

        Args:
            identifier: Manuscript identifier (e.g., NLI control number)

        Returns:
            URIRef for the manuscript
        """
        normalized = self.normalize_string(identifier)
        return URIRef(f"{self.namespace}MS_{normalized}")

    def work_uri(self, title: str, author: str | None = None) -> URIRef:
        """Generate URI for a Work entity.

        First attempts to match against Mazal authority files.
        Falls back to local URI generation if no match.

        Args:
            title: Work title
            author: Optional author name for disambiguation

        Returns:
            URIRef for the work
        """
        # Try Mazal authority lookup first
        if self.mazal_matcher:
            nli_id = self.mazal_matcher.match_work(title, author)
            if nli_id:
                return URIRef(f"{NLI_AUTHORITY_BASE}{nli_id}")

        # Fall back to local URI
        normalized_title = self.normalize_hebrew(title) or self.normalize_string(title)
        if author:
            normalized_author = self.normalize_hebrew(author) or self.normalize_string(author)
            return URIRef(f"{self.namespace}Work_{normalized_title}_by_{normalized_author}")
        return URIRef(f"{self.namespace}Work_{normalized_title}")

    def expression_uri(self, work_title: str, manuscript_id: str) -> URIRef:
        """Generate URI for an Expression entity.

        Args:
            work_title: Title of the work
            manuscript_id: Manuscript identifier

        Returns:
            URIRef for the expression
        """
        normalized_title = self.normalize_hebrew(work_title) or self.normalize_string(work_title)
        normalized_ms = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}Expression_{normalized_title}_in_{normalized_ms}")

    def person_uri(self, name: str, dates: str = None) -> URIRef:
        """Generate URI for a Person entity.

        First attempts to match against Mazal authority files.
        Falls back to local URI generation if no match.

        Args:
            name: Person's name
            dates: Optional date string for disambiguation (e.g., "1138-1204")

        Returns:
            URIRef for the person
        """
        # Try Mazal authority lookup first
        if self.mazal_matcher:
            nli_id = self.mazal_matcher.match_person(name, dates)
            if nli_id:
                return URIRef(f"{NLI_AUTHORITY_BASE}{nli_id}")

        # Fall back to local URI
        normalized = self.normalize_hebrew(name) or self.normalize_string(name)
        return URIRef(f"{self.namespace}Person_{normalized}")

    def place_uri(self, name: str) -> URIRef:
        """Generate URI for a Place entity.

        First attempts to match against Mazal authority files.
        Falls back to local URI generation if no match.

        Args:
            name: Place name

        Returns:
            URIRef for the place
        """
        # Try Mazal authority lookup first
        if self.mazal_matcher:
            nli_id = self.mazal_matcher.match_place(name)
            if nli_id:
                return URIRef(f"{NLI_AUTHORITY_BASE}{nli_id}")

        # Fall back to local URI
        normalized = self.normalize_hebrew(name) or self.normalize_string(name)
        return URIRef(f"{self.namespace}Place_{normalized}")

    def group_uri(self, name: str) -> URIRef:
        """Generate URI for a Group/Organization entity.

        First attempts to match against Mazal authority files.
        Falls back to local URI generation if no match.

        Args:
            name: Group/organization name

        Returns:
            URIRef for the group
        """
        # Try Mazal authority lookup first (corporate bodies)
        if self.mazal_matcher:
            nli_id = self.mazal_matcher.match_corporate(name)
            if nli_id:
                return URIRef(f"{NLI_AUTHORITY_BASE}{nli_id}")

        # Fall back to local URI
        normalized = self.normalize_hebrew(name) or self.normalize_string(name)
        return URIRef(f"{self.namespace}Group_{normalized}")

    def time_span_uri(self, description: str) -> URIRef:
        """Generate URI for a Time-Span entity.

        Args:
            description: Time span description (e.g., "1407", "15th_century")

        Returns:
            URIRef for the time span
        """
        normalized = self.normalize_string(description)
        return URIRef(f"{self.namespace}TimeSpan_{normalized}")

    def production_event_uri(self, manuscript_id: str) -> URIRef:
        """Generate URI for a Production event.

        Args:
            manuscript_id: Manuscript identifier

        Returns:
            URIRef for the production event
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}Production_{normalized}")

    def work_creation_event_uri(self, work_title: str, author: str | None = None) -> URIRef:
        """Generate URI for a Work Creation event.

        Args:
            work_title: Title of the work
            author: Optional author name for disambiguation

        Returns:
            URIRef for the work creation event
        """
        normalized_title = self.normalize_hebrew(work_title) or self.normalize_string(work_title)
        if author:
            normalized_author = self.normalize_hebrew(author) or self.normalize_string(author)
            return URIRef(f"{self.namespace}WorkCreation_{normalized_title}_by_{normalized_author}")
        return URIRef(f"{self.namespace}WorkCreation_{normalized_title}")

    def ownership_event_uri(self, manuscript_id: str, sequence: int) -> URIRef:
        """Generate URI for an Ownership/Transfer event.

        Args:
            manuscript_id: Manuscript identifier
            sequence: Sequence number for multiple ownership events

        Returns:
            URIRef for the ownership event
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}Ownership_{normalized}_{sequence}")

    def catalog_uri(self, name: str) -> URIRef:
        """Generate URI for a Catalog reference.

        Args:
            name: Catalog name

        Returns:
            URIRef for the catalog
        """
        normalized = self.normalize_string(name)
        return URIRef(f"{self.namespace}Catalog_{normalized}")

    def colophon_uri(self, manuscript_id: str) -> URIRef:
        """Generate URI for a Colophon.

        Args:
            manuscript_id: Manuscript identifier

        Returns:
            URIRef for the colophon
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}Colophon_{normalized}")

    def subject_uri(self, term: str) -> URIRef:
        """Generate URI for a Subject type.

        Args:
            term: Subject term

        Returns:
            URIRef for the subject
        """
        normalized = self.normalize_hebrew(term) or self.normalize_string(term)
        return URIRef(f"{self.namespace}Subject_{normalized}")

    def binding_uri(self, manuscript_id: str) -> URIRef:
        """Generate URI for a Binding.

        Args:
            manuscript_id: Manuscript identifier

        Returns:
            URIRef for the binding
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}Binding_{normalized}")

    def language_uri(self, code_or_name: str) -> URIRef:
        """Generate URI for a Language.

        Args:
            code_or_name: Language code (e.g., 'heb') or name

        Returns:
            URIRef for the language
        """
        normalized = self.normalize_string(code_or_name)
        return URIRef(f"{self.namespace}{normalized}")

    def material_uri(self, material: str) -> URIRef:
        """Generate URI for a Material.

        Args:
            material: Material name

        Returns:
            URIRef for the material
        """
        normalized = self.normalize_string(material)
        return URIRef(f"{self.namespace}{normalized}")

    def script_type_uri(self, script_type: str) -> URIRef:
        """Generate URI for a Script Type.

        Args:
            script_type: Script type name

        Returns:
            URIRef for the script type
        """
        return URIRef(f"{self.namespace}{script_type}")

    def bibliographical_unit_uri(self, manuscript_id: str, sequence: int) -> URIRef:
        """Generate URI for a Bibliographical Unit.

        Args:
            manuscript_id: Manuscript identifier
            sequence: Unit sequence number

        Returns:
            URIRef for the bibliographical unit
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}BibUnit_{normalized}_{sequence}")

    # =========================================================================
    # v1.4 ONTOLOGY FEATURES: New URI Generators
    # =========================================================================

    def codicological_unit_uri(self, manuscript_id: str, sequence: int = 1) -> URIRef:
        """Generate URI for a Codicological Unit.

        Args:
            manuscript_id: Manuscript identifier
            sequence: Unit sequence number (default 1 for main CU)

        Returns:
            URIRef for the codicological unit
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}CU_{normalized}_{sequence}")

    def paleographical_unit_uri(self, manuscript_id: str, sequence: int) -> URIRef:
        """Generate URI for a Paleographical Unit.

        Args:
            manuscript_id: Manuscript identifier
            sequence: Unit sequence number

        Returns:
            URIRef for the paleographical unit
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}PU_{normalized}_{sequence}")

    def cataloging_view_uri(self, manuscript_id: str) -> URIRef:
        """Generate URI for a Cataloging View (bibliographic paradigm).

        Args:
            manuscript_id: Manuscript identifier

        Returns:
            URIRef for the cataloging view
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}CatalogingView_{normalized}")

    def philological_view_uri(self, manuscript_id: str) -> URIRef:
        """Generate URI for a Philological View.

        Args:
            manuscript_id: Manuscript identifier

        Returns:
            URIRef for the philological view
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}PhilologicalView_{normalized}")

    def codicological_hierarchy_uri(self, manuscript_id: str) -> URIRef:
        """Generate URI for a Codicological Hierarchy.

        Args:
            manuscript_id: Manuscript identifier

        Returns:
            URIRef for the hierarchy structure
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}Hierarchy_{normalized}")

    def text_tradition_uri(self, tradition_name: str) -> URIRef:
        """Generate URI for a Text Tradition.

        Args:
            tradition_name: Name of the text tradition

        Returns:
            URIRef for the text tradition
        """
        normalized = self.normalize_hebrew(tradition_name) or self.normalize_string(tradition_name)
        return URIRef(f"{self.namespace}TextTradition_{normalized}")

    def transmission_witness_uri(self, manuscript_id: str, work_title: str) -> URIRef:
        """Generate URI for a Transmission Witness.

        Args:
            manuscript_id: Manuscript identifier
            work_title: Title of the work witnessed

        Returns:
            URIRef for the transmission witness
        """
        normalized_ms = self.normalize_string(manuscript_id)
        normalized_title = self.normalize_hebrew(work_title) or self.normalize_string(work_title)
        return URIRef(f"{self.namespace}Witness_{normalized_title}_in_{normalized_ms}")

    def textual_variant_uri(self, manuscript_id: str, location: str) -> URIRef:
        """Generate URI for a Textual Variant.

        Args:
            manuscript_id: Manuscript identifier
            location: Location identifier (e.g., folio_line)

        Returns:
            URIRef for the textual variant
        """
        normalized_ms = self.normalize_string(manuscript_id)
        normalized_loc = self.normalize_string(location)
        return URIRef(f"{self.namespace}Variant_{normalized_ms}_{normalized_loc}")

    def scribal_intervention_uri(self, manuscript_id: str, sequence: int) -> URIRef:
        """Generate URI for a Scribal Intervention.

        Args:
            manuscript_id: Manuscript identifier
            sequence: Intervention sequence number

        Returns:
            URIRef for the scribal intervention
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}Intervention_{normalized}_{sequence}")

    def evidence_chain_uri(self, manuscript_id: str, data_field: str | None = None) -> URIRef:
        """Generate URI for an Evidence Chain.

        Args:
            manuscript_id: Manuscript identifier
            data_field: Optional name of the data field being evidenced

        Returns:
            URIRef for the evidence chain
        """
        normalized_ms = self.normalize_string(manuscript_id)
        if data_field:
            normalized_field = self.normalize_string(data_field)
            return URIRef(f"{self.namespace}EvidenceChain_{normalized_ms}_{normalized_field}")
        return URIRef(f"{self.namespace}EvidenceChain_{normalized_ms}")

    def canonical_reference_uri(self, hierarchy_type: str, reference: str) -> URIRef:
        """Generate URI for a Canonical Reference.

        Args:
            hierarchy_type: Type of canonical hierarchy (e.g., Bible, Mishnah)
            reference: Reference string (e.g., "Genesis_1_1")

        Returns:
            URIRef for the canonical reference
        """
        normalized_type = self.normalize_string(hierarchy_type)
        normalized_ref = self.normalize_string(reference)
        return URIRef(f"{self.namespace}CanonRef_{normalized_type}_{normalized_ref}")

    def text_location_uri(self, manuscript_id: str, folio: str, line: int | None = None) -> URIRef:
        """Generate URI for a Text Location.

        Args:
            manuscript_id: Manuscript identifier
            folio: Folio number (e.g., "15r")
            line: Optional line number

        Returns:
            URIRef for the text location
        """
        normalized_ms = self.normalize_string(manuscript_id)
        normalized_folio = self.normalize_string(folio)
        if line is not None:
            return URIRef(f"{self.namespace}Loc_{normalized_ms}_{normalized_folio}_L{line}")
        return URIRef(f"{self.namespace}Loc_{normalized_ms}_{normalized_folio}")

    def paradigm_bridge_uri(self, work_title: str, tradition_name: str) -> URIRef:
        """Generate URI for a Paradigm Bridge linking Work to TextTradition.

        Args:
            work_title: Title of the work
            tradition_name: Name of the text tradition

        Returns:
            URIRef for the paradigm bridge
        """
        normalized_work = self.normalize_hebrew(work_title) or self.normalize_string(work_title)
        normalized_trad = self.normalize_hebrew(tradition_name) or self.normalize_string(
            tradition_name
        )
        return URIRef(f"{self.namespace}Bridge_{normalized_work}_to_{normalized_trad}")

    def multi_volume_set_uri(self, set_name: str) -> URIRef:
        """Generate URI for a Multi-Volume Set.

        Args:
            set_name: Name of the multi-volume set

        Returns:
            URIRef for the multi-volume set
        """
        normalized = self.normalize_hebrew(set_name) or self.normalize_string(set_name)
        return URIRef(f"{self.namespace}MultiVolumeSet_{normalized}")

    def anthology_structure_uri(self, manuscript_id: str) -> URIRef:
        """Generate URI for an Anthology Structure.

        Args:
            manuscript_id: Manuscript identifier

        Returns:
            URIRef for the anthology structure
        """
        normalized = self.normalize_string(manuscript_id)
        return URIRef(f"{self.namespace}AnthologyStructure_{normalized}")
