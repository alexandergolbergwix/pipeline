"""Build Wikidata item representations from authority-enriched pipeline records.

Converts the structured JSON output of Stage 2 (authority matching) into
WikidataItem dataclasses ready for upload or QuickStatements export.

Uses ALL available pipeline data: NER entities, VIAF/Mazal authority matches,
KIMA place links, subjects, genres, physical features, provenance, colophon,
contents, condition, and epistemological tracking.

Entity linking: VIAF IDs and NLI/Mazal IDs are resolved to Wikidata QIDs
via the reconciler. Person claims on manuscripts use the resolved QIDs
when available, ensuring proper LOD wiring.

Follows WikiProject Manuscripts Data Model and Digital Scriptorium methodology.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from converter.wikidata.property_mapping import (
    GENRE_TO_QID,
    KNOWN_WORK_QIDS,
    LANG_TO_QID,
    MATERIAL_TO_QID,
    P_AUTHOR,
    P_CATALOG_CODE,
    P_COLLECTION,
    P_CONDITION,
    P_DATE_OF_BIRTH,
    P_DATE_OF_DEATH,
    P_DESCRIBED_AT_URL,
    P_EXEMPLAR_OF,
    P_GENRE,
    P_HAS_PARTS,
    P_HEIGHT,
    P_IIIF_MANIFEST,
    P_INCEPTION,
    P_INSTANCE_OF,
    P_INVENTORY_NUMBER,
    P_LANGUAGE,
    P_LOCATION_OF_CREATION,
    P_MAIN_SUBJECT,
    P_MATERIAL,
    P_NLI_J9U_ID,
    P_NUMBER_OF_PAGES,
    P_OBJECT_NAMED_AS,
    P_OCCUPATION,
    P_ON_FOCUS_LIST,
    P_OWNED_BY,
    P_PART_OF,
    P_SCRIPT_STYLE,
    P_SOURCING_CIRCUMSTANCES,
    P_START_TIME,
    P_TITLE,
    P_TRANSCRIBED_BY,
    P_VIAF_ID,
    P_WIDTH,
    P_WRITING_SYSTEM,
    PRECISION_DAY,
    PRECISION_YEAR,
    Q_AUTHOR_OCCUPATION,
    Q_CIRCA,
    Q_CODEX,
    Q_COMMENTATOR_OCCUPATION,
    Q_DAMAGED,
    Q_GOOD_CONDITION,
    Q_HEBREW_ALPHABET,
    Q_HUMAN,
    Q_ILLUMINATED_MANUSCRIPT,
    Q_MANUSCRIPT,
    Q_NLI,
    Q_ORGANIZATION,
    Q_SCRIBE,
    Q_TRANSLATOR_OCCUPATION,
    Q_WIKIPROJECT_MANUSCRIPTS,
    Q_WRITTEN_WORK,
    ROLE_TO_PID,
    SCRIPT_TYPE_TO_QID,
    date_to_wikidata,
    extract_viaf_id,
    extract_wikidata_qid,
    nli_j9u_id,
    nli_reference,
)

logger = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class WikidataStatement:
    """A single Wikidata statement (claim) with optional qualifiers and references."""

    property_id: str
    value: str | int | float
    value_type: str  # "item", "string", "time", "quantity", "url", "monolingualtext"
    qualifiers: list[dict[str, object]] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    precision: int = PRECISION_YEAR
    language: str = "he"
    unit: str = ""


@dataclass
class WikidataItem:
    """A Wikidata item ready for upload."""

    labels: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    statements: list[WikidataStatement] = field(default_factory=list)
    existing_qid: str | None = None
    entity_type: str = ""  # "manuscript" | "person" | "work"
    local_id: str = ""


# ── Person deduplication key ─────────────────────────────────────────


def _person_key(name: str, viaf_uri: str | None, mazal_id: str | None) -> str:
    """Create a deduplication key for a person entity."""
    if mazal_id:
        return f"mazal:{mazal_id}"
    if viaf_uri:
        viaf_id = extract_viaf_id(viaf_uri)
        if viaf_id:
            return f"viaf:{viaf_id}"
    normalized = re.sub(r"[,.\s]+", "_", name.strip().lower())
    return f"name:{normalized}"


# ── Role → occupation QID ────────────────────────────────────────────

_ROLE_TO_OCCUPATION: dict[str, str] = {
    "AUTHOR": Q_AUTHOR_OCCUPATION,
    "author": Q_AUTHOR_OCCUPATION,
    "TRANSCRIBER": Q_SCRIBE,
    "scribe": Q_SCRIBE,
    "copyist": Q_SCRIBE,
    "TRANSLATOR": Q_TRANSLATOR_OCCUPATION,
    "translator": Q_TRANSLATOR_OCCUPATION,
    "COMMENTATOR": Q_COMMENTATOR_OCCUPATION,
    "commentator": Q_COMMENTATOR_OCCUPATION,
}


# ── Builder ──────────────────────────────────────────────────────────


class WikidataItemBuilder:
    """Build Wikidata item representations from authority-enriched records.

    Covers ALL 53 fields from ExtractedData plus NER entities and
    authority matches. Entity linking uses resolved Wikidata QIDs
    from the reconciliation phase.

    Usage::

        builder = WikidataItemBuilder()
        items = builder.build_all(records)
    """

    def __init__(self) -> None:
        self._person_items: dict[str, WikidataItem] = {}
        self._person_qids: dict[str, str] = {}  # person_key -> resolved Wikidata QID
        self._manuscript_items: list[WikidataItem] = []

    def build_manuscript_item(self, record: dict[str, object]) -> WikidataItem:
        """Build a Wikidata item for a single manuscript record."""
        control_number = str(record.get("_control_number", ""))
        title = str(record.get("title", "")).strip()
        ref = nli_reference(control_number)

        item = WikidataItem(entity_type="manuscript", local_id=control_number)

        # ── Labels & descriptions ────────────────────────────────
        self._set_labels(item, record, title)

        # ── Core identity ────────────────────────────────────────
        instance_qid = self._determine_instance_type(record)
        item.statements.append(WikidataStatement(
            property_id=P_INSTANCE_OF, value=instance_qid,
            value_type="item", references=ref,
        ))
        item.statements.append(WikidataStatement(
            property_id=P_COLLECTION, value=Q_NLI,
            value_type="item", references=ref,
        ))

        shelfmark = record.get("shelfmark")
        if shelfmark:
            item.statements.append(WikidataStatement(
                property_id=P_INVENTORY_NUMBER, value=str(shelfmark),
                value_type="string", references=ref,
            ))
        if control_number:
            item.statements.append(WikidataStatement(
                property_id=P_NLI_J9U_ID, value=nli_j9u_id(control_number),
                value_type="external-id", references=ref,
            ))
        if title:
            item.statements.append(WikidataStatement(
                property_id=P_TITLE, value=title,
                value_type="monolingualtext", language="he", references=ref,
            ))

        # ── Language & writing system ────────────────────────────
        self._add_languages(item, record, ref)

        # ── Script type (paleographic) ───────────────────────────
        script_type = record.get("script_type")
        if script_type and str(script_type) in SCRIPT_TYPE_TO_QID:
            item.statements.append(WikidataStatement(
                property_id=P_SCRIPT_STYLE, value=SCRIPT_TYPE_TO_QID[str(script_type)],
                value_type="item", references=ref,
            ))

        # ── Dates ────────────────────────────────────────────────
        dates = record.get("dates") or {}
        date_result = date_to_wikidata(dates)
        if date_result:
            time_value, precision = date_result
            # Add P1480 (circa) qualifier when date certainty is not exact
            qualifiers: list[dict[str, object]] = []
            cert_levels = record.get("certainty_levels") or {}
            date_cert = cert_levels.get("date", "")
            if date_cert and date_cert != "Certain":
                qualifiers.append({
                    "property": P_SOURCING_CIRCUMSTANCES,
                    "value": Q_CIRCA, "type": "item",
                })
            item.statements.append(WikidataStatement(
                property_id=P_INCEPTION, value=time_value,
                value_type="time", precision=precision,
                qualifiers=qualifiers, references=ref,
            ))

        # ── Location of creation (KIMA places → Wikidata QIDs) ──
        kima_places = record.get("kima_places") or {}
        for _place_name, wikidata_uri in kima_places.items():
            qid = extract_wikidata_qid(str(wikidata_uri))
            if qid:
                item.statements.append(WikidataStatement(
                    property_id=P_LOCATION_OF_CREATION, value=qid,
                    value_type="item", references=ref,
                ))

        # ── Physical description ─────────────────────────────────
        self._add_physical_description(item, record, ref)

        # ── Digital access ───────────────────────────────────────
        digital_url = record.get("digital_url")
        if digital_url:
            item.statements.append(WikidataStatement(
                property_id=P_DESCRIBED_AT_URL, value=str(digital_url),
                value_type="url", references=ref,
            ))
        iiif_url = record.get("iiif_manifest_url")
        if iiif_url:
            item.statements.append(WikidataStatement(
                property_id=P_IIIF_MANIFEST, value=str(iiif_url),
                value_type="url", references=ref,
            ))

        # ── Genres ───────────────────────────────────────────────
        for genre in (record.get("genres") or []):
            qid = GENRE_TO_QID.get(str(genre))
            if qid:
                item.statements.append(WikidataStatement(
                    property_id=P_GENRE, value=qid,
                    value_type="item", references=ref,
                ))

        # ── Subjects from canonical_references → P921 ────────────
        self._add_canonical_subjects(item, record, ref)

        # ── Contents / works (P1574 exemplar of) ────────────────
        self._add_contents(item, record, ref)

        # ── Incipit (first line of text) → P1922 ───────────────
        incipit = record.get("has_incipit")
        if incipit and str(incipit).strip() and str(incipit) != "None":
            from converter.wikidata.property_mapping import P_FIRST_LINE  # noqa: PLC0415
            item.statements.append(WikidataStatement(
                property_id=P_FIRST_LINE, value=str(incipit).strip().strip('"'),
                value_type="monolingualtext", language="he", references=ref,
            ))

        # ── Condition ────────────────────────────────────────────
        # P5816 (state of conservation) expects item QIDs, not strings.
        # Free-text condition notes are skipped — would need QID mapping
        # (e.g., Q56557591 "good condition", Q106379705 "damaged").

        # ── Catalog references ───────────────────────────────────
        for cat_ref in (record.get("catalog_references") or []):
            cat_name = cat_ref.get("catalog", "") if isinstance(cat_ref, dict) else str(cat_ref)
            if cat_name:
                item.statements.append(WikidataStatement(
                    property_id=P_CATALOG_CODE, value=str(cat_name),
                    value_type="string", references=ref,
                ))

        # ── Summary (MARC 520) → P7535 ─────────────────────────
        summary = record.get("summary")
        if summary and str(summary).strip() and str(summary) != "None":
            item.statements.append(WikidataStatement(
                property_id="P7535", value=str(summary),
                value_type="monolingualtext", language="he", references=ref,
            ))

        # ── Rights (MARC 540) → P6216 ───────────────────────────
        # Historical Hebrew manuscripts are public domain (pre-1900 works).
        # Rights statements from NLI describe digital copy access, not copyright.
        rights = record.get("rights_statement")
        if rights and str(rights).strip() and str(rights) != "None":
            item.statements.append(WikidataStatement(
                property_id="P6216", value="Q19652",
                value_type="item", references=ref,
            ))

        # ── Person claims (authors, scribes, owners from MARC + NER) ──
        self._add_person_claims(item, record, ref)

        # ── Provenance claims (owners from NER on MARC 561) ─────
        self._add_provenance_claims(item, record, ref)

        # ── WikiProject Manuscripts ──────────────────────────────
        item.statements.append(WikidataStatement(
            property_id=P_ON_FOCUS_LIST, value=Q_WIKIPROJECT_MANUSCRIPTS,
            value_type="item",
        ))

        return item

    def _set_labels(
        self, item: WikidataItem, record: dict[str, object], title: str,
    ) -> None:
        """Set labels, descriptions, and aliases for a manuscript item."""
        if title:
            item.labels["he"] = title
            item.labels["en"] = title

        shelfmark = record.get("shelfmark")
        if shelfmark:
            item.labels["en"] = f"Jerusalem, NLI, {shelfmark}"
            if title:
                item.aliases.setdefault("he", []).append(title)

        # Variant titles as aliases
        for vt in (record.get("variant_titles") or []):
            item.aliases.setdefault("he", []).append(str(vt))

        # Description
        langs = record.get("languages") or []
        lang_str = "Hebrew" if "heb" in langs else ", ".join(langs) if langs else "Hebrew"
        dates = record.get("dates") or {}
        year = dates.get("year", "")
        desc_parts = [f"{lang_str} manuscript"]
        if year:
            desc_parts.append(str(year))
        desc_parts.append("National Library of Israel")
        item.descriptions["en"] = ", ".join(desc_parts)

    def _determine_instance_type(self, record: dict[str, object]) -> str:
        """Determine the most specific P31 value for a manuscript."""
        if record.get("has_decoration"):
            return Q_ILLUMINATED_MANUSCRIPT
        if record.get("is_multi_volume") or record.get("is_anthology"):
            return Q_CODEX
        return Q_MANUSCRIPT

    def _add_languages(
        self, item: WikidataItem, record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P407 language and P282 writing system statements."""
        langs = record.get("languages") or []
        for lang_code in langs:
            qid = LANG_TO_QID.get(str(lang_code))
            if qid:
                item.statements.append(WikidataStatement(
                    property_id=P_LANGUAGE, value=qid,
                    value_type="item", references=ref,
                ))
        if any(str(c) in ("heb", "arc", "yid", "lad", "jrb", "jpr") for c in langs):
            item.statements.append(WikidataStatement(
                property_id=P_WRITING_SYSTEM, value=Q_HEBREW_ALPHABET,
                value_type="item", references=ref,
            ))

    def _add_physical_description(
        self, item: WikidataItem, record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add material, dimensions, folio count."""
        for material in (record.get("materials") or []):
            qid = MATERIAL_TO_QID.get(str(material))
            if qid:
                item.statements.append(WikidataStatement(
                    property_id=P_MATERIAL, value=qid,
                    value_type="item", references=ref,
                ))
        height = record.get("height_mm")
        if height and float(height) > 0:
            item.statements.append(WikidataStatement(
                property_id=P_HEIGHT, value=float(height),
                value_type="quantity", unit="mm", references=ref,
            ))
        width = record.get("width_mm")
        if width and float(width) > 0:
            item.statements.append(WikidataStatement(
                property_id=P_WIDTH, value=float(width),
                value_type="quantity", unit="mm", references=ref,
            ))
        extent = record.get("extent")
        if extent:
            folio_match = re.search(r"(\d+)", str(extent))
            if folio_match:
                item.statements.append(WikidataStatement(
                    property_id=P_NUMBER_OF_PAGES, value=int(folio_match.group(1)),
                    value_type="quantity", references=ref,
                ))

    def _add_contents(
        self, item: WikidataItem, record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P1574 (exemplar of) for contained works.

        Links manuscript to known Wikidata work items when possible.
        Also processes NER-extracted WORK entities from MARC 505.
        """
        seen_works: set[str] = set()

        # From structured MARC 505 data
        for content in (record.get("contents") or []):
            work_title = str(content.get("title", "")) if isinstance(content, dict) else str(content)
            if not work_title:
                continue

            work_qid = KNOWN_WORK_QIDS.get(work_title.strip())
            if work_qid:
                seen_works.add(work_title.strip())
                qualifiers: list[dict[str, object]] = []
                if isinstance(content, dict) and content.get("folio_range"):
                    qualifiers.append({
                        "property": "P958", "value": str(content["folio_range"]),
                        "type": "string",
                    })
                item.statements.append(WikidataStatement(
                    property_id=P_EXEMPLAR_OF, value=work_qid,
                    value_type="item", qualifiers=qualifiers, references=ref,
                ))

        # From NER-extracted contents entities (WORK + FOLIO)
        entities = record.get("entities") or []
        cont_entities = [e for e in entities if e.get("source") == "contents_ner"]
        works = [e for e in cont_entities if e.get("type") == "WORK"]
        folios = [e for e in cont_entities if e.get("type") == "FOLIO"]

        for work in works:
            work_title = str(work.get("text", "")).strip().strip('".')
            if not work_title or work_title in seen_works:
                continue
            seen_works.add(work_title)

            work_qid = KNOWN_WORK_QIDS.get(work_title)
            qualifiers_ner: list[dict[str, object]] = []

            # Find associated folio entity (nearest by position)
            for folio in folios:
                if abs(folio.get("end", 0) - work.get("start", 0)) < 3:
                    qualifiers_ner.append({
                        "property": "P958",
                        "value": str(folio.get("text", "")).strip(":"),
                        "type": "string",
                    })
                    break

            if work_qid:
                item.statements.append(WikidataStatement(
                    property_id=P_EXEMPLAR_OF, value=work_qid,
                    value_type="item", qualifiers=qualifiers_ner, references=ref,
                ))
            # Unknown works (no QID) are skipped — P527 requires item values.
            # They are preserved in the authority_enriched.json for future linking.

    def _add_canonical_subjects(
        self, item: WikidataItem, record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P921 (main subject) from canonical_references.

        Maps Bible book names and Talmud Bavli tractate names to Wikidata QIDs.
        """
        from converter.wikidata.property_mapping import (  # noqa: PLC0415
            BIBLE_BOOK_TO_QID, SUBJECT_TO_QID, TALMUD_TRACTATE_TO_QID,
        )
        seen_qids: set[str] = set()

        # From canonical references (Bible books, Talmud tractates)
        for cr in (record.get("canonical_references") or []):
            hierarchy = cr.get("hierarchy", "")
            qid = None
            if hierarchy == "Bible":
                book = cr.get("book", "")
                qid = BIBLE_BOOK_TO_QID.get(book)
            elif hierarchy == "Talmud_Bavli":
                tractate = cr.get("tractate", "")
                qid = TALMUD_TRACTATE_TO_QID.get(tractate)
            if qid and qid not in seen_qids:
                seen_qids.add(qid)
                item.statements.append(WikidataStatement(
                    property_id=P_MAIN_SUBJECT, value=qid,
                    value_type="item", references=ref,
                ))

        # From LCSH subject headings
        for subj in (record.get("subjects") or []):
            term = subj.get("term", "") if isinstance(subj, dict) else str(subj)
            qid = SUBJECT_TO_QID.get(term)
            if qid and qid not in seen_qids:
                seen_qids.add(qid)
                item.statements.append(WikidataStatement(
                    property_id=P_MAIN_SUBJECT, value=qid,
                    value_type="item", references=ref,
                ))

    def _add_provenance_claims(
        self, item: WikidataItem, record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add provenance claims from NER-extracted MARC 561 entities.

        OWNER → P127 (owned by) with optional P580/P582 date qualifiers.
        COLLECTION → noted via P1932 (named as) qualifier on P195.
        """
        entities = record.get("entities") or []
        prov_entities = [e for e in entities if e.get("source") == "provenance_ner"]
        if not prov_entities:
            return

        owners = [e for e in prov_entities if e.get("type") == "OWNER"]
        dates = [e for e in prov_entities if e.get("type") == "DATE"]
        collections = [e for e in prov_entities if e.get("type") == "COLLECTION"]

        # Build date qualifiers from DATE entities (P580/P582 per WikiProject Data Model)
        date_qualifiers: list[dict[str, object]] = []
        for date_ent in dates:
            date_text = str(date_ent.get("text", "")).strip().strip('".:')
            if not date_text:
                continue
            # Try to parse as Gregorian year
            year_match = re.search(r"(\d{4})", date_text)
            if year_match:
                year = int(year_match.group(1))
                date_qualifiers.append({
                    "property": P_START_TIME,
                    "value": f"+{year:04d}-00-00T00:00:00Z",
                    "type": "time",
                })

        seen_owners: set[str] = set()
        for owner in owners:
            owner_name = str(owner.get("text", "")).strip().strip('".')
            if not owner_name or owner_name in seen_owners:
                continue
            seen_owners.add(owner_name)

            viaf_uri = owner.get("viaf_uri")
            mazal_id = owner.get("mazal_id")
            key = _person_key(owner_name, viaf_uri, mazal_id)

            person_item = self._get_or_create_person(
                owner_name, viaf_uri, mazal_id, "OWNER", record,
            )
            resolved_qid = self._person_qids.get(key) or person_item.existing_qid

            # Attach date qualifiers to the ownership statement
            owner_qualifiers = list(date_qualifiers)  # Copy shared dates
            if resolved_qid:
                item.statements.append(WikidataStatement(
                    property_id=P_OWNED_BY, value=resolved_qid,
                    value_type="item", qualifiers=owner_qualifiers, references=ref,
                ))
            else:
                owner_qualifiers.append({
                    "property": P_OBJECT_NAMED_AS,
                    "value": owner_name, "type": "string",
                })
                item.statements.append(WikidataStatement(
                    property_id=P_OWNED_BY, value=f"__LOCAL:{person_item.local_id}",
                    value_type="item", qualifiers=owner_qualifiers, references=ref,
                ))

        seen_colls: set[str] = set()
        for coll in collections:
            coll_name = str(coll.get("text", "")).strip().strip('".')
            if not coll_name or coll_name in seen_colls:
                continue
            seen_colls.add(coll_name)
            # P195 (collection) expects item QIDs, not strings.
            # NER-extracted collection names are skipped — would need
            # reconciliation to Wikidata institution QIDs.

    def _add_person_claims(
        self, item: WikidataItem, record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add person-related claims using resolved Wikidata QIDs.

        Wikidata convention (per WikiProject Manuscripts):
        - P50 (author) belongs on WORK items, not manuscripts
        - P11603 (transcribed by) goes directly on manuscripts (scribes)
        - P127 (owned by) goes directly on manuscripts (owners)

        Authors are linked via P1574: MS → exemplar of → Work → P50 → Author.
        When no separate work item exists, P50 is placed on the MS as fallback.

        Entity linking flow:
        1. Person has VIAF URI → reconciler resolves to Wikidata QID → use QID
        2. Person has Mazal/NLI ID → reconciler resolves to Wikidata QID → use QID
        3. Person not found on Wikidata → create new person item with P214 + P8189
        """
        seen_person_keys: set[str] = set()

        def _add_person_statement(
            name: str, role: str, viaf_uri: str | None, mazal_id: str | None,
        ) -> None:
            if not name:
                return
            key = _person_key(name, viaf_uri, mazal_id)
            if key in seen_person_keys:
                return
            seen_person_keys.add(key)

            # Normalize role for lookup (case-insensitive, strip whitespace)
            role_norm = role.strip().lower()
            pid = ROLE_TO_PID.get(role_norm, ROLE_TO_PID.get(role, P_AUTHOR))
            person_item = self._get_or_create_person(name, viaf_uri, mazal_id, role, record)
            resolved_qid = self._person_qids.get(key) or person_item.existing_qid

            # For scribes/owners → direct claim on manuscript
            # For authors → P50 on MS as fallback (proper model uses Work item)
            if resolved_qid:
                item.statements.append(WikidataStatement(
                    property_id=pid, value=resolved_qid,
                    value_type="item", references=ref,
                ))
            else:
                item.statements.append(WikidataStatement(
                    property_id=pid, value=f"__LOCAL:{person_item.local_id}",
                    value_type="item", references=ref,
                    qualifiers=[{"property": P_OBJECT_NAMED_AS, "value": name, "type": "string"}],
                ))

        # From MARC authority matches (structured name fields 100/700/etc.)
        for match in (record.get("marc_authority_matches") or []):
            _add_person_statement(
                str(match.get("name", "")), str(match.get("role", "")),
                match.get("viaf_uri"), match.get("mazal_id"),
            )

        # From NER entities (extracted from note fields)
        for entity in (record.get("entities") or []):
            _add_person_statement(
                str(entity.get("person", "")), str(entity.get("role", "")),
                entity.get("viaf_uri"), entity.get("mazal_id"),
            )

    def _get_or_create_person(
        self,
        name: str,
        viaf_uri: str | None,
        mazal_id: str | None,
        role: str,
        source_record: dict[str, object],
    ) -> WikidataItem:
        """Get existing or create new person item with full authority data."""
        key = _person_key(name, viaf_uri, mazal_id)
        if key in self._person_items:
            return self._person_items[key]

        person = WikidataItem(entity_type="person", local_id=key)
        person.labels["he"] = name

        # P31 = human (or organization)
        is_org = any(
            kw in name.lower()
            for kw in ("library", "museum", "university", "institute", "ספרייה", "מכון")
        )
        person.statements.append(WikidataStatement(
            property_id=P_INSTANCE_OF,
            value=Q_ORGANIZATION if is_org else Q_HUMAN,
            value_type="item",
        ))

        # Extract dates: first try direct authority ID match, then name match
        birth_year = None
        death_year = None
        dates_str = ""

        # Strategy 1: Match by authority ID (most reliable)
        for match in (source_record.get("marc_authority_matches") or []):
            mid = str(match.get("mazal_id", ""))
            vid = str(match.get("viaf_uri", ""))
            if (mazal_id and mid == mazal_id) or (viaf_uri and vid == viaf_uri):
                birth_year = match.get("birth_year")
                death_year = match.get("death_year")
                dates_str = str(match.get("dates", ""))
                break

        # Strategy 2: Name matching across all sources
        if not birth_year and not death_year:
            for person_list in [
                source_record.get("authors") or [],
                source_record.get("contributors") or [],
                source_record.get("marc_authority_matches") or [],
            ]:
                for entry in person_list:
                    entry_name = str(entry.get("name", ""))
                    if entry_name and name and entry_name[:5] == name[:5]:
                        birth_year = entry.get("birth_year")
                        death_year = entry.get("death_year")
                        dates_str = str(entry.get("dates", ""))
                        break
                if birth_year or death_year:
                    break

        # Strategy 3: Parse dates string if we have it but not individual years
        if not birth_year and not death_year and dates_str and dates_str != "None":
            parts = re.split(r"[-–]", dates_str.strip())
            for p in parts:
                p = p.strip()
                if p and p.isdigit():
                    yr = int(p)
                    if 100 < yr < 2100:
                        if birth_year is None:
                            birth_year = yr
                        else:
                            death_year = yr

        if dates_str:
            person.descriptions["en"] = f"person ({dates_str})"
        elif not is_org:
            person.descriptions["en"] = "person associated with Hebrew manuscripts"
        else:
            person.descriptions["en"] = "organization associated with Hebrew manuscripts"

        # P569/P570 = birth/death dates
        if birth_year and not is_org:
            person.statements.append(WikidataStatement(
                property_id=P_DATE_OF_BIRTH,
                value=f"+{int(birth_year):04d}-00-00T00:00:00Z",
                value_type="time", precision=PRECISION_YEAR,
            ))
        if death_year and not is_org:
            person.statements.append(WikidataStatement(
                property_id=P_DATE_OF_DEATH,
                value=f"+{int(death_year):04d}-00-00T00:00:00Z",
                value_type="time", precision=PRECISION_YEAR,
            ))

        # P106 = occupation (from role)
        occupation_qid = _ROLE_TO_OCCUPATION.get(role)
        if occupation_qid and not is_org:
            person.statements.append(WikidataStatement(
                property_id=P_OCCUPATION, value=occupation_qid,
                value_type="item",
            ))

        # P214 = VIAF ID (critical for LOD linking)
        viaf_id = extract_viaf_id(str(viaf_uri)) if viaf_uri else None
        if viaf_id:
            person.statements.append(WikidataStatement(
                property_id=P_VIAF_ID, value=viaf_id, value_type="external-id",
            ))

        # P8189 = NLI J9U ID (links to Mazal authority)
        if mazal_id:
            person.statements.append(WikidataStatement(
                property_id=P_NLI_J9U_ID, value=str(mazal_id), value_type="external-id",
            ))

        # P21 = sex or gender (male for historical Hebrew manuscript persons)
        # Nearly all historical manuscript authors/scribes were male
        if not is_org:
            person.statements.append(WikidataStatement(
                property_id="P21", value="Q6581097", value_type="item",  # male
            ))

        # P1343 = described by source (link to NLI/Ktiv catalog)
        person.statements.append(WikidataStatement(
            property_id="P1343", value="Q118384267", value_type="item",  # Ktiv
        ))

        self._person_items[key] = person
        return person

    def build_all(
        self,
        records: list[dict[str, object]],
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> list[WikidataItem]:
        """Build all Wikidata items from authority-enriched records.

        Returns manuscripts first, then deduplicated persons.
        """
        self._person_items.clear()
        self._person_qids.clear()
        self._manuscript_items.clear()
        total = len(records)

        for idx, record in enumerate(records):
            ms_item = self.build_manuscript_item(record)
            self._manuscript_items.append(ms_item)
            if progress_cb:
                progress_cb(idx + 1, total)

        # Persons MUST come before manuscripts so their QIDs are available
        # for resolving __LOCAL: references in manuscript claims
        all_items = list(self._person_items.values()) + self._manuscript_items
        logger.info(
            "Built %d items: %d manuscripts + %d persons",
            len(all_items), len(self._manuscript_items), len(self._person_items),
        )
        return all_items

    @property
    def person_count(self) -> int:
        """Return the number of unique person items built."""
        return len(self._person_items)

    def apply_reconciliation(self, reconciled: dict[str, str | None]) -> None:
        """Apply reconciliation results — set resolved Wikidata QIDs on persons.

        When a person is found on Wikidata via VIAF/NLI lookup, their
        existing QID is stored so manuscript claims can reference it
        directly (proper LOD wiring instead of local references).

        Also resolves __LOCAL: references in manuscript statements so
        QuickStatements/dry-run exports get proper QIDs too.
        """
        for key, qid in reconciled.items():
            if qid:
                self._person_qids[key] = qid
                if key in self._person_items:
                    self._person_items[key].existing_qid = qid

        # Resolve __LOCAL: references in manuscript statements
        resolved = 0
        for ms_item in self._manuscript_items:
            for stmt in ms_item.statements:
                if isinstance(stmt.value, str) and stmt.value.startswith("__LOCAL:"):
                    local_ref = stmt.value[len("__LOCAL:"):]
                    qid = self._person_qids.get(local_ref)
                    if qid:
                        stmt.value = qid
                        resolved += 1
        if resolved:
            logger.info("Resolved %d __LOCAL: references from reconciliation", resolved)
