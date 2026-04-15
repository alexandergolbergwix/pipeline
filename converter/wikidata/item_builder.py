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
    CONDITION_TO_QID,
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
    P_HEIGHT,
    P_IIIF_MANIFEST,
    P_INCEPTION,
    P_INSCRIPTION,
    P_INSTANCE_OF,
    P_INVENTORY_NUMBER,
    P_LANGUAGE,
    P_LAST_LINE,
    P_LOCATION_OF_CREATION,
    P_MAIN_SUBJECT,
    P_MATERIAL,
    P_NLI_J9U_ID,
    P_NUMBER_OF_FOLIOS,
    P_NUMBER_OF_PAGES,
    P_NUMBER_OF_PARTS,
    P_OBJECT_HAS_ROLE,
    P_OBJECT_NAMED_AS,
    P_OCCUPATION,
    P_ON_FOCUS_LIST,
    P_OWNED_BY,
    P_SCRIPT_STYLE,
    P_SIGNIFICANT_PLACE,
    P_SOURCING_CIRCUMSTANCES,
    P_START_TIME,
    P_TITLE,
    P_VIAF_ID,
    P_VOLUME,
    P_WIDTH,
    P_WRITING_SYSTEM,
    PRECISION_YEAR,
    Q_AUTHOR_OCCUPATION,
    Q_CIRCA,
    Q_CODEX,
    Q_COLOPHON,
    Q_COMMENTATOR_OCCUPATION,
    Q_CORRECTION,
    Q_GLOSS,
    Q_HEBREW_ALPHABET,
    Q_HUMAN,
    Q_ILLUMINATED_MANUSCRIPT,
    Q_MANUSCRIPT,
    Q_MARGINALIA,
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
    viaf_reference,
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


_INSTITUTIONAL_KEYWORDS: tuple[str, ...] = (
    "library",
    "museum",
    "university",
    "institute",
    "seminary",
    "school",
    "college",
    "society",
    "academy",
    "foundation",
    "association",
    "trust",
    "centre",
    "center",
    "archive",
    "ספרייה",
    "מכון",
    "אוניברסיטה",
    "מוזיאון",
    "קהילה",
    "מכללה",
    "ארכיון",
)


def _is_institutional_name(name: str) -> bool:
    """True if the name looks like an institution (library, museum, etc.).

    Used to re-route MARC 710 (added entry — corporate name) values away
    from P50 (author) to P195 (collection) — fix for the Q139085958 pattern
    Geagea reported (2026-04-15) where institutions were being assigned as
    authors of manuscripts.
    """
    if not name:
        return False
    lowered = name.lower()
    return any(kw in lowered for kw in _INSTITUTIONAL_KEYWORDS)


def _to_natural_name_order(name: str) -> str:
    """Convert MARC's inverted name form 'Surname, Given' to Wikidata's
    natural-order convention 'Given Surname'.

    Bug fix (2026-04-15, Geagea complaint on Q139230386, label "סופינו, עמנואל"):
    Wikidata expects person labels in natural order. The inverted form is a
    cataloging convention that belongs in P1559 (native name) for searchability,
    not in the human-facing label.

    Rules:
    - "Surname, Given" → "Given Surname"
    - "Surname, Given (qualifier)" → "Given Surname (qualifier)"
    - "Surname, Given, second-Given" → "second-Given Given Surname" (rare,
      conservatively NOT flipped — leave as-is to avoid worse mistakes)
    - Names without exactly one comma → returned unchanged
    - Trailing dates "Surname, Given, 1850-1900" → "Given Surname (1850-1900)"
    """
    if not name or "," not in name:
        return name
    # Split off any trailing date range like ", 1850-1900" or ", -1900"
    date_match = re.search(r",\s*(-?\d{2,4}(?:[-–]\d{0,4})?)\s*$", name)
    date_suffix = ""
    base = name
    if date_match:
        date_suffix = f" ({date_match.group(1)})"
        base = name[: date_match.start()]
    parts = [p.strip() for p in base.split(",")]
    # Drop empty parts (trailing comma case)
    parts = [p for p in parts if p]
    if len(parts) != 2:
        # Either zero commas (unchanged) or more than one comma (ambiguous);
        # return unchanged + any trailing date suffix.
        return name if not date_suffix else (base.strip() + date_suffix)
    surname, given = parts
    return f"{given} {surname}{date_suffix}"


# ── Work deduplication key ──────────────────────────────────────────


def _work_key(title: str) -> str:
    """Create a deduplication key for a work entity."""
    normalized = re.sub(r'[,.\s"׳״]+', "_", title.strip().lower())
    return f"work:{normalized}"


def _build_work_description(author_name: str | None, century: str | None) -> str:
    """Build a disambiguating English description for a work item.

    Wikidata requires descriptions to disambiguate same-label items.
    Bug fix 2026-04-15 (web audit): previously all work descriptions were
    identical ('Hebrew manuscript work'), making same-titled works
    indistinguishable. Now includes author and century when available.
    """
    parts = ["Hebrew manuscript work"]
    if author_name:
        cleaned = author_name.strip().rstrip(",;:")
        if cleaned:
            parts.append(f"by {cleaned}")
    if century:
        parts.append(f"({century})")
    return " ".join(parts)


_ROLE_TO_LABEL: dict[str, str] = {
    "AUTHOR": "author",
    "author": "author",
    "SCRIBE": "scribe",
    "scribe": "scribe",
    "OWNER": "manuscript owner",
    "owner": "manuscript owner",
    "TRANSLATOR": "translator",
    "translator": "translator",
    "EDITOR": "editor",
    "editor": "editor",
    "COMMENTATOR": "commentator",
    "commentator": "commentator",
    "PATRON": "patron",
    "patron": "patron",
}


def _is_placeholder_title(title: str | None) -> bool:
    """Return True if a MARC 245 title is a generic catalog placeholder.

    Bug fix 2026-04-15 (Geagea complaint, 2026-04-15): catalogers use
    "קובץ" / "קבץ" (= "compilation" / "file") and short topical variants
    ("קובץ בקבלה" = "Kabbalah compilation") as the title field of MARC
    records for multi-text anthologies that have no overarching real
    title. When emitted as a Wikidata Hebrew label, these strings are
    useless for disambiguation and were flagged as nonsense by the
    Hebrew-Wikidata community.

    We treat as placeholder:
    - exact "קובץ" / "קבץ" (with optional trailing punctuation)
    - "קובץ X" / "קבץ X" where the whole string is short (≤ 25 chars)

    The original string is preserved as a Hebrew alias by the caller so
    it remains searchable; the Wikidata LABEL falls back to a synthetic
    shelfmark-based label.
    """
    if not title:
        return False
    cleaned = title.strip().rstrip(".,;:")
    if cleaned in {"קובץ", "קבץ"}:
        return True
    # Short topical placeholder like "קובץ בקבלה" or "קבץ מדרשים"
    if cleaned.startswith(("קובץ ", "קבץ ")) and len(cleaned) <= 25:
        return True
    return False


def _build_person_description(role: str, dates_str: str, is_org: bool) -> str:
    """Build a disambiguating English description for a person item.

    Wikidata expects descriptions to disambiguate same-label items.
    Bug fix 2026-04-16 (deeper audit Fix #13): previously emitted a bare
    "person (1200-1280)" or generic "person associated with Hebrew
    manuscripts". Now incorporates the role so e.g. two different scribes
    with the same name can be told apart.
    """
    if is_org:
        if dates_str:
            return f"organization ({dates_str})"
        return "organization associated with Hebrew manuscripts"
    role_label = _ROLE_TO_LABEL.get((role or "").strip(), "")
    if role_label and dates_str:
        return f"{role_label} ({dates_str})"
    if role_label:
        return f"Hebrew manuscript {role_label}"
    if dates_str:
        return f"person ({dates_str})"
    return "person associated with Hebrew manuscripts"


def _extract_inception_year(record: dict[str, object]) -> int | None:
    """Return the manuscript's earliest known year (CE) if available.

    Used by the public-domain (P6216) gate so we only assert public-domain
    status on demonstrably pre-1900 works. Returns ``None`` when no year
    can be determined — caller should err on the side of NOT asserting
    public domain.

    Looks at: record["dates"]["year"], MARC 008 date1, and a fallback
    parse of the original Hebrew/English date string.
    """
    dates = record.get("dates")
    if isinstance(dates, dict):
        year = dates.get("year") or dates.get("date1") or dates.get("year_start")
        if year is not None:
            try:
                return int(year)
            except (TypeError, ValueError):
                pass
        original = str(dates.get("original_string") or "")
        m = re.search(r"\b(\d{3,4})\b", original)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def _extract_century_for_work(source_record: dict[str, object]) -> str | None:
    """Extract a human-readable century string for the work description.

    Pulls from the manuscript's date data when present (e.g. '16th century',
    'מאה ט"ז'). Returns None when no century info is available so the
    description omits the parenthetical.
    """
    dates = source_record.get("dates")
    if not isinstance(dates, dict):
        return None
    original = str(dates.get("original_string") or "").replace('""', '"').strip()
    if not original:
        return None
    eng_match = re.search(r"\d{1,2}(?:th|st|nd|rd)\s*century", original, re.IGNORECASE)
    if eng_match:
        return eng_match.group(0).lower()
    if "מאה" in original:
        # Take just up to the closing date ordinal
        snippet = re.search(r"מאה\s+[א-ת][\u05F4\"\']?[א-ת]?", original)
        if snippet:
            return snippet.group(0)
    return None


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

    def __init__(self, reconciler: object | None = None) -> None:
        """Initialize the builder.

        Args:
            reconciler: Optional WikidataReconciler instance. When provided,
                _get_or_create_work() will SPARQL-query Wikidata for an
                existing work item before creating a new one. This catches
                duplicates of classical Hebrew works (Talmud tractates,
                Rashi commentaries, Maimonides, etc.) that already exist
                on Wikidata. Bug fix 2026-04-15 (web audit Fix #2).
                Pass None to disable SPARQL reconciliation (faster offline
                builds; falls back to KNOWN_WORK_QIDS hardcoded mapping).
        """
        self._person_items: dict[str, WikidataItem] = {}
        self._person_qids: dict[str, str] = {}  # person_key -> resolved Wikidata QID
        self._work_items: dict[str, WikidataItem] = {}
        self._manuscript_items: list[WikidataItem] = []
        self._reconciler = reconciler

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
        item.statements.append(
            WikidataStatement(
                property_id=P_INSTANCE_OF,
                value=instance_qid,
                value_type="item",
                references=ref,
            )
        )
        item.statements.append(
            WikidataStatement(
                property_id=P_COLLECTION,
                value=Q_NLI,
                value_type="item",
                references=ref,
            )
        )
        # P17 = country (Israel — all NLI manuscripts are held in Israel)
        item.statements.append(
            WikidataStatement(
                property_id="P17",
                value="Q801",
                value_type="item",
                references=ref,
            )
        )
        # P131 = located in administrative entity (Jerusalem)
        item.statements.append(
            WikidataStatement(
                property_id="P131",
                value="Q1218",
                value_type="item",
                references=ref,
            )
        )

        shelfmark = record.get("shelfmark")
        if shelfmark:
            item.statements.append(
                WikidataStatement(
                    property_id=P_INVENTORY_NUMBER,
                    value=str(shelfmark),
                    value_type="string",
                    references=ref,
                )
            )
        if control_number:
            item.statements.append(
                WikidataStatement(
                    property_id=P_NLI_J9U_ID,
                    value=nli_j9u_id(control_number),
                    value_type="external-id",
                    references=ref,
                )
            )
        if title:
            item.statements.append(
                WikidataStatement(
                    property_id=P_TITLE,
                    value=title,
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Language & writing system ────────────────────────────
        self._add_languages(item, record, ref)

        # ── Script type (paleographic) ───────────────────────────
        script_type = record.get("script_type")
        if script_type and str(script_type) in SCRIPT_TYPE_TO_QID:
            item.statements.append(
                WikidataStatement(
                    property_id=P_SCRIPT_STYLE,
                    value=SCRIPT_TYPE_TO_QID[str(script_type)],
                    value_type="item",
                    references=ref,
                )
            )

        # ── Dates ────────────────────────────────────────────────
        colophon_fields = set(record.get("data_from_colophon") or [])
        dates = record.get("dates") or {}
        date_result = date_to_wikidata(dates)
        if date_result:
            time_value, precision = date_result
            # Add P1480 (circa) qualifier when date certainty is not exact
            qualifiers: list[dict[str, object]] = []
            cert_levels = record.get("certainty_levels") or {}
            date_cert = cert_levels.get("date", "")
            if date_cert and date_cert != "Certain":
                qualifiers.append(
                    {
                        "property": P_SOURCING_CIRCUMSTANCES,
                        "value": Q_CIRCA,
                        "type": "item",
                    }
                )
            # P887 (based on heuristic) = colophon when date is from colophon
            if "dates" in colophon_fields or "colophon_text" in colophon_fields:
                qualifiers.append(
                    {
                        "property": "P887",
                        "value": Q_COLOPHON,
                        "type": "item",
                    }
                )
            item.statements.append(
                WikidataStatement(
                    property_id=P_INCEPTION,
                    value=time_value,
                    value_type="time",
                    precision=precision,
                    qualifiers=qualifiers,
                    references=ref,
                )
            )

        # ── Location of creation (KIMA places → Wikidata QIDs) ──
        kima_places = record.get("kima_places") or {}
        for _place_name, wikidata_uri in kima_places.items():
            qid = extract_wikidata_qid(str(wikidata_uri))
            if qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_LOCATION_OF_CREATION,
                        value=qid,
                        value_type="item",
                        references=ref,
                    )
                )

        # ── Physical description ─────────────────────────────────
        self._add_physical_description(item, record, ref)

        # ── Digital access ───────────────────────────────────────
        digital_url = record.get("digital_url")
        if digital_url:
            item.statements.append(
                WikidataStatement(
                    property_id=P_DESCRIBED_AT_URL,
                    value=str(digital_url),
                    value_type="url",
                    references=ref,
                )
            )
        iiif_url = record.get("iiif_manifest_url")
        if iiif_url:
            item.statements.append(
                WikidataStatement(
                    property_id=P_IIIF_MANIFEST,
                    value=str(iiif_url),
                    value_type="url",
                    references=ref,
                )
            )

        # ── Genres ───────────────────────────────────────────────
        for genre in record.get("genres") or []:
            qid = GENRE_TO_QID.get(str(genre))
            if qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_GENRE,
                        value=qid,
                        value_type="item",
                        references=ref,
                    )
                )

        # ── Subjects from canonical_references → P921 ────────────
        self._add_canonical_subjects(item, record, ref)

        # ── Contents / works (P1574 exemplar of) ────────────────
        self._add_contents(item, record, ref)

        # ── Incipit (first line of text) → P1922 ───────────────
        incipit = record.get("has_incipit")
        if incipit and str(incipit).strip() and str(incipit) != "None":
            from converter.wikidata.property_mapping import P_FIRST_LINE  # noqa: PLC0415

            item.statements.append(
                WikidataStatement(
                    property_id=P_FIRST_LINE,
                    value=str(incipit).strip().strip('"'),
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Explicit (last line of text) → P3132 ───────────────
        explicit = record.get("has_explicit")
        if explicit and str(explicit).strip() and str(explicit) != "None":
            item.statements.append(
                WikidataStatement(
                    property_id=P_LAST_LINE,
                    value=str(explicit).strip().strip('"'),
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Condition → P5816 (keyword → QID mapping + date parsing)
        for cond_note in record.get("condition_notes") or []:
            cond_text = str(cond_note).strip()
            # Try keyword → QID
            matched = False
            for keyword, qid in CONDITION_TO_QID.items():
                if keyword in cond_text.lower():
                    item.statements.append(
                        WikidataStatement(
                            property_id=P_CONDITION,
                            value=qid,
                            value_type="item",
                            references=ref,
                        )
                    )
                    matched = True
                    break
            if matched:
                continue
            # Try YYYYMMDD date → restoration date
            date_match = re.match(r"(\d{4})(\d{2})(\d{2})", cond_text)
            if date_match:
                y, m, d = date_match.groups()
                from converter.wikidata.property_mapping import Q_RESTORED  # noqa: PLC0415

                item.statements.append(
                    WikidataStatement(
                        property_id=P_CONDITION,
                        value=Q_RESTORED,
                        value_type="item",
                        references=ref,
                        qualifiers=[
                            {
                                "property": "P585",
                                "value": f"+{y}-{m}-{d}T00:00:00Z",
                                "type": "time",
                            }
                        ],
                    )
                )

        # ── Catalog references ───────────────────────────────────
        for cat_ref in record.get("catalog_references") or []:
            cat_name = cat_ref.get("catalog", "") if isinstance(cat_ref, dict) else str(cat_ref)
            if cat_name:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_CATALOG_CODE,
                        value=str(cat_name),
                        value_type="string",
                        references=ref,
                    )
                )

        # ── Summary (MARC 520) → P7535 ─────────────────────────
        summary = record.get("summary")
        if summary and str(summary).strip() and str(summary) != "None":
            item.statements.append(
                WikidataStatement(
                    property_id="P7535",
                    value=str(summary),
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Rights (MARC 540) → P6216 ───────────────────────────
        # Historical Hebrew manuscripts are public domain (pre-1900 works).
        # Rights statements from NLI describe digital copy access, not copyright.
        # Bug fix 2026-04-16 (deeper audit Fix #15): only assert public-domain
        # status when the inception date is known AND before 1900. A 20th-c.
        # manuscript could otherwise receive an incorrect public-domain claim.
        rights = record.get("rights_statement")
        if rights and str(rights).strip() and str(rights) != "None":
            inception_year = _extract_inception_year(record)
            if inception_year is not None and inception_year < 1900:
                item.statements.append(
                    WikidataStatement(
                        property_id="P6216",
                        value="Q19652",
                        value_type="item",
                        references=ref,
                    )
                )

        # ── Person claims (authors, scribes, owners from MARC + NER) ──
        self._add_person_claims(item, record, ref)

        # ── Provenance claims (owners from NER on MARC 561) ─────
        self._add_provenance_claims(item, record, ref)

        # ── Number of codicological units → P2635 ───────────────
        codic_units = record.get("codicological_units") or []
        if len(codic_units) > 1:
            item.statements.append(
                WikidataStatement(
                    property_id=P_NUMBER_OF_PARTS,
                    value=len(codic_units),
                    value_type="quantity",
                    references=ref,
                )
            )

        # ── Colophon text → P1684 (inscription) ─────────────────
        colophon = record.get("colophon_text")
        if colophon and str(colophon).strip() and str(colophon) != "None":
            item.statements.append(
                WikidataStatement(
                    property_id=P_INSCRIPTION,
                    value=str(colophon).strip()[:1500],
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                    qualifiers=[
                        {
                            "property": P_OBJECT_HAS_ROLE,
                            "value": Q_COLOPHON,
                            "type": "item",
                        }
                    ],
                )
            )

        # ── Scribal interventions → P1684 (inscription) ─────────
        for intervention in record.get("scribal_interventions") or []:
            text = (
                str(intervention.get("text", "")).strip()
                if isinstance(intervention, dict)
                else str(intervention).strip()
            )
            if not text or text == "None":
                continue
            int_type = (
                str(intervention.get("type", "")).lower() if isinstance(intervention, dict) else ""
            )
            role_qid = (
                Q_GLOSS
                if "gloss" in int_type
                else Q_CORRECTION
                if "correct" in int_type
                else Q_MARGINALIA
            )
            item.statements.append(
                WikidataStatement(
                    property_id=P_INSCRIPTION,
                    value=text[:1500],
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                    qualifiers=[
                        {
                            "property": P_OBJECT_HAS_ROLE,
                            "value": role_qid,
                            "type": "item",
                        }
                    ],
                )
            )

        # ── Volume info → P478 ──────────────────────────────────
        vol_info = record.get("volume_info")
        if vol_info and str(vol_info).strip() and str(vol_info) != "None":
            item.statements.append(
                WikidataStatement(
                    property_id=P_VOLUME,
                    value=str(vol_info).strip(),
                    value_type="string",
                    references=ref,
                )
            )

        # ── Related places → P7153 (significant place) ──────────
        for place_name in record.get("related_places") or []:
            for _name, uri in (record.get("kima_places") or {}).items():
                if place_name.strip() in _name or _name in place_name.strip():
                    qid = extract_wikidata_qid(str(uri))
                    if qid:
                        item.statements.append(
                            WikidataStatement(
                                property_id=P_SIGNIFICANT_PLACE,
                                value=qid,
                                value_type="item",
                                references=ref,
                            )
                        )
                        break

        # ── General notes (MARC 500) → P7535 ────────────────────
        for note in record.get("notes") or []:
            note_text = str(note).strip()
            if note_text and note_text != "None" and len(note_text) > 5:
                item.statements.append(
                    WikidataStatement(
                        property_id="P7535",
                        value=note_text[:1500],
                        value_type="monolingualtext",
                        language="he",
                        references=ref,
                    )
                )

        # ── Provenance raw text (MARC 561) → P7535 + provenance qualifier
        prov_text = record.get("provenance")
        if prov_text and str(prov_text).strip() and str(prov_text) != "None":
            item.statements.append(
                WikidataStatement(
                    property_id="P7535",
                    value=str(prov_text).strip()[:1500],
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                    qualifiers=[
                        {
                            "property": P_OBJECT_HAS_ROLE,
                            "value": "Q1145267",
                            "type": "item",  # provenance
                        }
                    ],
                )
            )

        # ── Multiple scribal hands → P7535 note ─────────────────
        if record.get("has_multiple_hands"):
            item.statements.append(
                WikidataStatement(
                    property_id="P7535",
                    value="Written in multiple scribal hands",
                    value_type="monolingualtext",
                    language="en",
                    references=ref,
                )
            )

        # ── Related works → P1574 via work items ────────────────
        for rw in record.get("related_works") or []:
            rw_title = rw.get("title", "") if isinstance(rw, dict) else str(rw)
            rw_title = rw_title.strip().strip('".')
            if not rw_title:
                continue
            rw_qid = KNOWN_WORK_QIDS.get(rw_title)
            if rw_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=rw_qid,
                        value_type="item",
                        references=ref,
                    )
                )
            else:
                rw_item = self._get_or_create_work(rw_title, None, record)
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=f"__LOCAL:{rw_item.local_id}",
                        value_type="item",
                        references=ref,
                    )
                )

        # ── WikiProject Manuscripts ──────────────────────────────
        item.statements.append(
            WikidataStatement(
                property_id=P_ON_FOCUS_LIST,
                value=Q_WIKIPROJECT_MANUSCRIPTS,
                value_type="item",
            )
        )

        return item

    def _set_labels(
        self,
        item: WikidataItem,
        record: dict[str, object],
        title: str,
    ) -> None:
        """Set labels, descriptions, and aliases for a manuscript item.

        Bug fix 2026-04-15 (Geagea complaint): MARC 245 sometimes contains
        a generic placeholder like "קובץ." (= "compilation") rather than a
        real title, used by catalogers when an anthology has no overarching
        name. Emitting that as the Hebrew label produced 94 useless labels
        on Wikidata. We now detect placeholder titles and route them to
        an alias slot, falling back to a shelfmark-based label.
        """
        is_placeholder = _is_placeholder_title(title)
        shelfmark = record.get("shelfmark")
        if title and not is_placeholder:
            item.labels["he"] = title
            item.labels["en"] = title
        elif title:
            # Placeholder: keep the original cataloger string as a Hebrew
            # alias for searchability, but do NOT use it as the label.
            item.aliases.setdefault("he", []).append(title)

        if shelfmark:
            item.labels["en"] = f"Jerusalem, NLI, {shelfmark}"
            if title and not is_placeholder:
                item.aliases.setdefault("he", []).append(title)
            # When the title was a placeholder AND we have a shelfmark,
            # synthesise a useful Hebrew label from the shelfmark.
            if is_placeholder and "he" not in item.labels:
                item.labels["he"] = f"כתב יד עברי, ספרייה לאומית, {shelfmark}"

        # Variant titles as aliases
        for vt in record.get("variant_titles") or []:
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
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P407 language and P282 writing system statements."""
        langs = record.get("languages") or []
        for lang_code in langs:
            qid = LANG_TO_QID.get(str(lang_code))
            if qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_LANGUAGE,
                        value=qid,
                        value_type="item",
                        references=ref,
                    )
                )
        if any(str(c) in ("heb", "arc", "yid", "lad", "jrb", "jpr") for c in langs):
            item.statements.append(
                WikidataStatement(
                    property_id=P_WRITING_SYSTEM,
                    value=Q_HEBREW_ALPHABET,
                    value_type="item",
                    references=ref,
                )
            )

    def _add_physical_description(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add material, dimensions, folio count."""
        for material in record.get("materials") or []:
            qid = MATERIAL_TO_QID.get(str(material))
            if qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_MATERIAL,
                        value=qid,
                        value_type="item",
                        references=ref,
                    )
                )
        height = record.get("height_mm")
        if height and float(height) > 0:
            item.statements.append(
                WikidataStatement(
                    property_id=P_HEIGHT,
                    value=float(height),
                    value_type="quantity",
                    unit="mm",
                    references=ref,
                )
            )
        width = record.get("width_mm")
        if width and float(width) > 0:
            item.statements.append(
                WikidataStatement(
                    property_id=P_WIDTH,
                    value=float(width),
                    value_type="quantity",
                    unit="mm",
                    references=ref,
                )
            )
        extent = record.get("extent")
        if extent:
            extent_str = str(extent)
            folio_match = re.search(r"(\d+)", extent_str)
            if folio_match:
                # Bug fix 2026-04-16 (deeper audit Fix #11): manuscripts are
                # counted in folios (leaves), not pages. P1104 (number of
                # pages) is wrong; the correct property is P7416 (number of
                # folios). Heuristic: if the extent string explicitly says
                # "page(s)" use P1104; otherwise default to P7416 since
                # virtually all manuscript catalogues count in folios/leaves.
                low = extent_str.lower()
                says_pages = "page" in low or "עמוד" in low
                prop = P_NUMBER_OF_PAGES if says_pages else P_NUMBER_OF_FOLIOS
                item.statements.append(
                    WikidataStatement(
                        property_id=prop,
                        value=int(folio_match.group(1)),
                        value_type="quantity",
                        references=ref,
                    )
                )

    def _add_contents(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P1574 (exemplar of) for contained works.

        Links manuscript to known Wikidata work items when possible.
        Also processes NER-extracted WORK entities from MARC 505.
        """
        seen_works: set[str] = set()

        # From structured MARC 505 data
        for content in record.get("contents") or []:
            work_title = (
                str(content.get("title", "")) if isinstance(content, dict) else str(content)
            )
            if not work_title or not work_title.strip():
                continue
            work_title = work_title.strip()
            if work_title in seen_works:
                continue
            seen_works.add(work_title)

            qualifiers: list[dict[str, object]] = []
            if isinstance(content, dict) and content.get("folio_range"):
                qualifiers.append(
                    {
                        "property": "P958",
                        "value": str(content["folio_range"]),
                        "type": "string",
                    }
                )

            work_qid = KNOWN_WORK_QIDS.get(work_title)
            if work_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=work_qid,
                        value_type="item",
                        qualifiers=qualifiers,
                        references=ref,
                    )
                )
            else:
                work_item = self._get_or_create_work(work_title, None, record)
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=f"__LOCAL:{work_item.local_id}",
                        value_type="item",
                        qualifiers=qualifiers,
                        references=ref,
                    )
                )

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
                    qualifiers_ner.append(
                        {
                            "property": "P958",
                            "value": str(folio.get("text", "")).strip(":"),
                            "type": "string",
                        }
                    )
                    break

            if work_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=work_qid,
                        value_type="item",
                        qualifiers=qualifiers_ner,
                        references=ref,
                    )
                )
            else:
                # Find associated WORK_AUTHOR entity by position proximity
                work_authors = [e for e in cont_entities if e.get("type") == "WORK_AUTHOR"]
                author_name = None
                for wa in work_authors:
                    if abs(wa.get("start", 0) - work.get("end", 0)) < 20:
                        author_name = str(wa.get("text", "")).strip()
                        break
                work_item = self._get_or_create_work(work_title, author_name, record)
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=f"__LOCAL:{work_item.local_id}",
                        value_type="item",
                        qualifiers=qualifiers_ner,
                        references=ref,
                    )
                )

    def _add_canonical_subjects(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P921 (main subject) from canonical_references.

        Maps Bible book names and Talmud Bavli tractate names to Wikidata QIDs.
        """
        from converter.wikidata.property_mapping import (  # noqa: PLC0415
            BIBLE_BOOK_TO_QID,
            SUBJECT_TO_QID,
            TALMUD_TRACTATE_TO_QID,
        )

        seen_qids: set[str] = set()

        # From canonical references (Bible books, Talmud tractates)
        for cr in record.get("canonical_references") or []:
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
                item.statements.append(
                    WikidataStatement(
                        property_id=P_MAIN_SUBJECT,
                        value=qid,
                        value_type="item",
                        references=ref,
                    )
                )

        # From LCSH subject headings
        for subj in record.get("subjects") or []:
            term = subj.get("term", "") if isinstance(subj, dict) else str(subj)
            if not term:
                continue
            qid = SUBJECT_TO_QID.get(term)
            if qid and qid not in seen_qids:
                seen_qids.add(qid)
                item.statements.append(
                    WikidataStatement(
                        property_id=P_MAIN_SUBJECT,
                        value=qid,
                        value_type="item",
                        references=ref,
                    )
                )
            elif not qid and term.strip():
                # Person names as subjects → create person item, link via P921
                person = self._get_or_create_person(
                    term.strip(),
                    None,
                    None,
                    "subject",
                    record,
                )
                p_key = _person_key(term.strip(), None, None)
                resolved = self._person_qids.get(p_key) or person.existing_qid
                if resolved and resolved not in seen_qids:
                    seen_qids.add(resolved)
                    item.statements.append(
                        WikidataStatement(
                            property_id=P_MAIN_SUBJECT,
                            value=resolved,
                            value_type="item",
                            references=ref,
                        )
                    )
                elif person.local_id not in seen_qids:
                    seen_qids.add(person.local_id)
                    item.statements.append(
                        WikidataStatement(
                            property_id=P_MAIN_SUBJECT,
                            value=f"__LOCAL:{person.local_id}",
                            value_type="item",
                            references=ref,
                        )
                    )

    def _add_provenance_claims(
        self,
        item: WikidataItem,
        record: dict[str, object],
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
                date_qualifiers.append(
                    {
                        "property": P_START_TIME,
                        "value": f"+{year:04d}-00-00T00:00:00Z",
                        "type": "time",
                    }
                )

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
                owner_name,
                viaf_uri,
                mazal_id,
                "OWNER",
                record,
            )
            resolved_qid = self._person_qids.get(key) or person_item.existing_qid

            # Attach date qualifiers to the ownership statement
            owner_qualifiers = list(date_qualifiers)  # Copy shared dates
            if resolved_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_OWNED_BY,
                        value=resolved_qid,
                        value_type="item",
                        qualifiers=owner_qualifiers,
                        references=ref,
                    )
                )
            else:
                owner_qualifiers.append(
                    {
                        "property": P_OBJECT_NAMED_AS,
                        "value": owner_name,
                        "type": "string",
                    }
                )
                item.statements.append(
                    WikidataStatement(
                        property_id=P_OWNED_BY,
                        value=f"__LOCAL:{person_item.local_id}",
                        value_type="item",
                        qualifiers=owner_qualifiers,
                        references=ref,
                    )
                )

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
        self,
        item: WikidataItem,
        record: dict[str, object],
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
            name: str,
            role: str,
            viaf_uri: str | None,
            mazal_id: str | None,
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

            # Bug fix (2026-04-15, Geagea complaint on Q139085958): an
            # institutional contributor (MARC 710 "current owner" = National
            # Library of Israel, etc.) was being attached as P50 (author).
            # Institutions cannot be authors of manuscripts. Re-route them:
            #   - If pid would be P50 (author) AND the name is institutional,
            #     change pid to P195 (collection) instead.
            #   - "owner" / "current_owner" roles already map to P127 (owned
            #     by) which is correct.
            if pid == P_AUTHOR and _is_institutional_name(name):
                pid = "P195"  # collection

            person_item = self._get_or_create_person(name, viaf_uri, mazal_id, role, record)
            resolved_qid = self._person_qids.get(key) or person_item.existing_qid

            # For scribes/owners → direct claim on manuscript
            # For authors → P50 on MS as fallback (proper model uses Work item)
            if resolved_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=pid,
                        value=resolved_qid,
                        value_type="item",
                        references=ref,
                    )
                )
            else:
                item.statements.append(
                    WikidataStatement(
                        property_id=pid,
                        value=f"__LOCAL:{person_item.local_id}",
                        value_type="item",
                        references=ref,
                        qualifiers=[
                            {"property": P_OBJECT_NAMED_AS, "value": name, "type": "string"}
                        ],
                    )
                )

        # From MARC authority matches (structured name fields 100/700/etc.)
        for match in record.get("marc_authority_matches") or []:
            _add_person_statement(
                str(match.get("name", "")),
                str(match.get("role", "")),
                match.get("viaf_uri"),
                match.get("mazal_id"),
            )

        # From NER entities (extracted from note fields)
        for entity in record.get("entities") or []:
            _add_person_statement(
                str(entity.get("person", "")),
                str(entity.get("role", "")),
                entity.get("viaf_uri"),
                entity.get("mazal_id"),
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

        # Clean name: strip trailing punctuation that comes from MARC formatting
        clean_name = name.strip().rstrip(",;:")
        if not clean_name or len(clean_name) < 2:
            # Skip creating items with incomplete/empty names
            self._person_items[key] = person
            return person

        # Bug fix 2026-04-16 (deeper audit Fix #1): every person statement
        # must carry a P248 reference. Use VIAF cluster URL when the person
        # has a VIAF ID; otherwise fall back to the parent manuscript's
        # NLI catalog URL where the name was first encountered. Without
        # references, WikiProject Authority Control flags the bot at WD:AN.
        viaf_id_for_ref = extract_viaf_id(str(viaf_uri)) if viaf_uri else None
        if viaf_id_for_ref:
            person_ref = viaf_reference(viaf_id_for_ref)
        else:
            ms_ctrl = str(source_record.get("_control_number") or "")
            person_ref = nli_reference(ms_ctrl) if ms_ctrl else []

        # Detect script: Hebrew vs Latin
        has_hebrew = any("\u0590" <= c <= "\u05ff" for c in clean_name)
        label_lang = "he" if has_hebrew else "en"
        # Wikidata convention is "Given Surname" (natural order), NOT MARC's
        # inverted "Surname, Given" form. Bug fix (2026-04-15, Geagea complaint
        # on Q139230386 where label was "סופינו, עמנואל"): flip inverted forms
        # to natural order for the LABEL. The original inverted form is
        # preserved in P1559 (native name) below for searchability.
        person.labels[label_lang] = _to_natural_name_order(clean_name)

        # P31 = human (or organization) — uses the shared institutional
        # keyword list (see _is_institutional_name above).
        is_org = _is_institutional_name(name)
        person.statements.append(
            WikidataStatement(
                property_id=P_INSTANCE_OF,
                value=Q_ORGANIZATION if is_org else Q_HUMAN,
                value_type="item",
            )
        )

        # Extract dates: first try direct authority ID match, then name match
        birth_year = None
        death_year = None
        dates_str = ""

        # Strategy 1: Match by authority ID (most reliable)
        for match in source_record.get("marc_authority_matches") or []:
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

        # Bug fix 2026-04-16 (deeper audit Fix #13): person descriptions
        # should disambiguate, not just restate dates. Build as
        # "<role> (<dates>)" when role is known. Falls back gracefully.
        person.descriptions["en"] = _build_person_description(
            role=role,
            dates_str=dates_str,
            is_org=is_org,
        )

        # P569/P570 = birth/death dates
        if birth_year and not is_org:
            person.statements.append(
                WikidataStatement(
                    property_id=P_DATE_OF_BIRTH,
                    value=f"+{int(birth_year):04d}-00-00T00:00:00Z",
                    value_type="time",
                    precision=PRECISION_YEAR,
                )
            )
        if death_year and not is_org:
            person.statements.append(
                WikidataStatement(
                    property_id=P_DATE_OF_DEATH,
                    value=f"+{int(death_year):04d}-00-00T00:00:00Z",
                    value_type="time",
                    precision=PRECISION_YEAR,
                )
            )

        # P106 = occupation (from role)
        occupation_qid = _ROLE_TO_OCCUPATION.get(role)
        if occupation_qid and not is_org:
            person.statements.append(
                WikidataStatement(
                    property_id=P_OCCUPATION,
                    value=occupation_qid,
                    value_type="item",
                )
            )

        # P214 = VIAF ID (critical for LOD linking)
        viaf_id = extract_viaf_id(str(viaf_uri)) if viaf_uri else None
        if viaf_id:
            person.statements.append(
                WikidataStatement(
                    property_id=P_VIAF_ID,
                    value=viaf_id,
                    value_type="external-id",
                )
            )

        # P8189 = NLI J9U ID — STRICT: only attach when ALL three are true:
        #   1. The ID exists (mazal_id is non-empty)
        #   2. The ID has the AUTHORITY-record prefix '9870…' (NOT bibliographic '990…')
        #   3. The target item is a person, not an organisation
        # Bug fix (2026-04-15, Geagea complaint): bibliographic record IDs were
        # being attached to person items, causing the Property talk:P8189
        # /Duplicates/humans page to flood with false-positive duplicates.
        # P8189's format URL is nli.org.il/en/authorities/$1 — authority-only.
        mazal_str = str(mazal_id) if mazal_id else ""
        if mazal_str and mazal_str.startswith("9870") and not is_org:
            person.statements.append(
                WikidataStatement(
                    property_id=P_NLI_J9U_ID,
                    value=mazal_str,
                    value_type="external-id",
                )
            )
        elif mazal_str and not mazal_str.startswith("9870"):
            # Bibliographic ID (990…) or unknown prefix — do NOT attach P8189.
            # Could be logged for offline review.
            pass

        # P21 (sex or gender): intentionally NOT set.
        # Bug fix 2026-04-15 (web audit): the MARC source records carry no
        # reliable gender information, and unsourced bot-added gender claims
        # are flagged by the community (UW iSchool 2023 "P21 Problem" study).
        # For medieval scribes, gender is often genuinely unknown. Future
        # enrichment may derive P21 from MARC 375 if/when it is populated by
        # the cataloger. Until then, omit P21 entirely rather than asserting
        # a default that is unsourced and may be wrong.

        # P1343 = described by source (link to NLI/Ktiv catalog)
        person.statements.append(
            WikidataStatement(
                property_id="P1343",
                value="Q118384267",
                value_type="item",  # Ktiv
            )
        )

        # P1412 = languages spoken, written or signed.
        # Bug fix 2026-04-15 (web audit): previously hardcoded to Hebrew.
        # Bug fix 2026-04-16 (deeper audit Fix #12): the manuscript's MARC
        # 008/041 languages are MANUSCRIPT-level data, NOT person-level.
        # A scribe who only copied a Hebrew manuscript may not have written
        # Hebrew themselves; an owner mentioned in provenance may speak
        # something else entirely. Only emit P1412 when the role is
        # "author" — for that role the manuscript's language is a defensible
        # proxy for the author's writing language. For all other roles
        # (scribe, owner, mentioned-person), omit P1412 to avoid asserting
        # something we cannot defend.
        role_norm = role.strip().lower() if role else ""
        if not is_org and role_norm == "author":
            seen_lang_qids: set[str] = set()
            for lang_code in source_record.get("languages") or []:
                lang_qid = LANG_TO_QID.get(str(lang_code))
                if lang_qid and lang_qid not in seen_lang_qids:
                    seen_lang_qids.add(lang_qid)
                    person.statements.append(
                        WikidataStatement(
                            property_id="P1412",
                            value=lang_qid,
                            value_type="item",
                        )
                    )

        # P1559 = name in native language — use language matching the script.
        # Skip names with trailing commas/incomplete entries.
        # Bug fix 2026-04-16 (deeper audit Fix #14): Latin-script names were
        # being emitted with language "la" (Latin), which is wrong for modern
        # European names like "Emanuel Sofino" (Italian). The label already
        # carries the same value with a more accurate language tag (en),
        # so omit P1559 entirely for Latin-script names.
        cleaned_name = name.strip().rstrip(",;:")
        if cleaned_name and not is_org and len(cleaned_name) >= 2:
            # Detect script: Hebrew, Cyrillic, Arabic. Latin is intentionally
            # excluded because we cannot reliably infer the true language.
            if any("\u0590" <= c <= "\u05ff" for c in cleaned_name):
                native_lang = "he"
            elif any("\u0400" <= c <= "\u04ff" for c in cleaned_name):
                native_lang = "ru"
            elif any("\u0600" <= c <= "\u06ff" for c in cleaned_name):
                native_lang = "ar"
            else:
                native_lang = None  # Latin and unknown scripts → omit P1559

            if native_lang:
                person.statements.append(
                    WikidataStatement(
                        property_id="P1559",
                        value=cleaned_name,
                        value_type="monolingualtext",
                        language=native_lang,
                    )
                )

        # Additional authority IDs from VIAF cluster harvesting
        for match in source_record.get("marc_authority_matches") or []:
            mid = str(match.get("mazal_id", ""))
            vid = str(match.get("viaf_uri", ""))
            if (mazal_id and mid == mazal_id) or (viaf_uri and vid == viaf_uri):
                if match.get("gnd_id"):
                    person.statements.append(
                        WikidataStatement(
                            property_id="P227",
                            value=str(match["gnd_id"]),
                            value_type="external-id",
                        )
                    )
                if match.get("lc_id"):
                    person.statements.append(
                        WikidataStatement(
                            property_id="P244",
                            value=str(match["lc_id"]),
                            value_type="external-id",
                        )
                    )
                if match.get("isni"):
                    person.statements.append(
                        WikidataStatement(
                            property_id="P213",
                            value=str(match["isni"]),
                            value_type="external-id",
                        )
                    )
                if match.get("bnf_id"):
                    person.statements.append(
                        WikidataStatement(
                            property_id="P268",
                            value=str(match["bnf_id"]),
                            value_type="external-id",
                        )
                    )
                break

        # Bug fix 2026-04-16 (deeper audit Fix #1): attach person_ref to
        # every statement that does not already carry references. Done as
        # a post-build pass so each `WikidataStatement(...)` callsite above
        # does not need an explicit references= kwarg.
        if person_ref:
            for stmt in person.statements:
                if not stmt.references:
                    stmt.references = list(person_ref)

        self._person_items[key] = person
        return person

    def _get_or_create_work(
        self,
        title: str,
        author_name: str | None,
        source_record: dict[str, object],
    ) -> WikidataItem:
        """Get existing or create new work item for a Hebrew manuscript work."""
        key = _work_key(title)
        if key in self._work_items:
            return self._work_items[key]

        # Bug fix 2026-04-15 (web audit Fix #2): consult the reconciler to
        # find an existing Wikidata work matching this Hebrew title before
        # creating a duplicate. The KNOWN_WORK_QIDS hardcoded mapping is a
        # fast first pass (handled by callers); this is the SPARQL fallback
        # for works not in that list. The reconciler's
        # reconcile_work_by_label_and_author() also rejects matches whose
        # P50 author conflicts with our proposed author (cross-verification
        # pattern reused from person reconciliation).
        existing_qid: str | None = None
        if self._reconciler is not None:
            try:
                # Resolve author QID from cache when available so the
                # reconciler can perform author-conflict rejection.
                author_qid: str | None = None
                if author_name and author_name.strip():
                    author_key = _person_key(author_name, None, None)
                    author_qid = self._person_qids.get(author_key)
                existing_qid = self._reconciler.reconcile_work_by_label_and_author(
                    title=title,
                    lang="he",
                    author_qid=author_qid,
                )
            except Exception as exc:  # noqa: BLE001 - reconciler failures must not block builds
                logger.warning(
                    "reconcile_work_by_label_and_author failed for %r: %s; "
                    "proceeding with new-item creation",
                    title,
                    exc,
                )

        work = WikidataItem(entity_type="work", local_id=key)
        if existing_qid:
            work.existing_qid = existing_qid
        work.labels["he"] = title
        # Bug fix 2026-04-15 (web audit): all 3,970 work items previously
        # received the identical description "Hebrew manuscript work", which
        # made same-label items indistinguishable on Wikidata. Build a
        # disambiguating description that includes the author when known
        # (Wikidata requires descriptions to disambiguate same-label items).
        work.descriptions["en"] = _build_work_description(
            author_name=author_name,
            century=_extract_century_for_work(source_record),
        )

        work.statements.append(
            WikidataStatement(
                property_id=P_INSTANCE_OF,
                value=Q_WRITTEN_WORK,
                value_type="item",
            )
        )
        work.statements.append(
            WikidataStatement(
                property_id=P_TITLE,
                value=title,
                value_type="monolingualtext",
                language="he",
            )
        )
        work.statements.append(
            WikidataStatement(
                property_id=P_LANGUAGE,
                value="Q9288",
                value_type="item",
            )
        )

        # Link to author if available
        if author_name and author_name.strip():
            author_key = _person_key(author_name, None, None)
            person = self._person_items.get(author_key)
            if person:
                resolved_qid = self._person_qids.get(author_key) or person.existing_qid
                if resolved_qid:
                    work.statements.append(
                        WikidataStatement(
                            property_id=P_AUTHOR,
                            value=resolved_qid,
                            value_type="item",
                        )
                    )
                else:
                    work.statements.append(
                        WikidataStatement(
                            property_id=P_AUTHOR,
                            value=f"__LOCAL:{person.local_id}",
                            value_type="item",
                        )
                    )

        # Bug fix 2026-04-16 (deeper audit Fix #2): attach a P248 reference
        # to every work statement. Use the parent manuscript's NLI catalog
        # URL — the work was extracted from that record.
        ms_ctrl = str(source_record.get("_control_number") or "")
        if ms_ctrl:
            work_ref = nli_reference(ms_ctrl)
            for stmt in work.statements:
                if not stmt.references:
                    stmt.references = list(work_ref)

        self._work_items[key] = work
        return work

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
        self._work_items.clear()
        self._manuscript_items.clear()
        total = len(records)

        for idx, record in enumerate(records):
            ms_item = self.build_manuscript_item(record)
            self._manuscript_items.append(ms_item)
            if progress_cb:
                progress_cb(idx + 1, total)

        # Order: works → persons → manuscripts (for __LOCAL: resolution)
        all_items = (
            list(self._work_items.values())
            + list(self._person_items.values())
            + self._manuscript_items
        )
        logger.info(
            "Built %d items: %d manuscripts + %d persons + %d works",
            len(all_items),
            len(self._manuscript_items),
            len(self._person_items),
            len(self._work_items),
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
                    local_ref = stmt.value[len("__LOCAL:") :]
                    qid = self._person_qids.get(local_ref)
                    if qid:
                        stmt.value = qid
                        resolved += 1
        if resolved:
            logger.info("Resolved %d __LOCAL: references from reconciliation", resolved)
