"""Deterministic post-filters for NER outputs.

Each filter prevents a specific class of NER mistake from flowing to
authority resolution / RDF construction and producing a wrong Wikidata claim:

* :func:`filter_work_author_folio` — re-types folio-shaped strings
  ("133ב :") that the contents NER mis-tags as ``WORK_AUTHOR``,
  preventing P50 author claims with folio values.

* :func:`filter_collection_citations` — routes catalog citations
  ("מ' גסטר.", "הלברשטם 89.") out of the COLLECTION list and into a
  per-record ``catalog_references`` field, preventing P195 claims
  that point at non-existent institutions.

* :func:`filter_owner_length` — moves OWNER spans longer than
  :data:`OWNER_MAX_LENGTH` into a per-record ``provenance_inscriptions``
  list (destined for P7535 description notes), preventing P127 /
  P2093 from carrying paragraph-length bill-of-sale text instead of
  a name.

* :func:`filter_person_hallucinations` — drops person spans whose
  text is a known topic keyword (Hebrew or Latin), an ALL-CAPS ASCII
  fragment, an MARC uncertainty marker, or too short to disambiguate;
  prevents Stage 4 from creating person items for non-persons.

All four are pure functions over the entity list (plus a shared
``surrounding_text`` for B2). ``NerWorker`` chains them after every
NER model has emitted its spans and the entity offsets have been
rebased onto ``record["text"]``.
"""

from __future__ import annotations

import re
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# B1 — WORK_AUTHOR folio-prefix re-route
# ─────────────────────────────────────────────────────────────────────

# Folio references in Hebrew manuscript catalogues take forms like
# "133ב :", "5א", "342ב, 45א". They are digit-led with a Hebrew side
# letter (א=front, ב=back) immediately following.
_FOLIO_PREFIX_RE = re.compile(r"^\s*\d+\s*[א-ת]")

# Hebrew tokens that signal a real WORK title rather than a folio ref
# or a person name. When a WORK_AUTHOR span starts with one of these,
# it is re-typed to WORK (preserving the entity) instead of being
# routed to FOLIO. Covers explicit work-headers (ספר, מסכת, הלכות,
# שו"ת, מאמר, אגרת), genre prefixes (פירוש, ביאור, מהדורת), and known
# stand-alone work titles (תשב"ץ, יוסיפון, כתובים, נביאים, תורה).
_WORK_TITLE_PREFIXES: frozenset[str] = frozenset({
    "ספר", "מסכת", "הלכות", "שו\"ת", "שו״ת",
    "פירוש", "ביאור", "מהדורת", "מפר'", "מאמר", "אגרת",
    "תשב\"ץ", "תשב״ץ", "יוסיפון", "כתובים", "נביאים", "תורה",
})


def _has_work_title_prefix(text: str) -> bool:
    """True iff *text* begins with a WORK morphology marker."""
    stripped = text.strip()
    if not stripped:
        return False
    for prefix in _WORK_TITLE_PREFIXES:
        if stripped.startswith(prefix):
            return True
    first_token = stripped.split()[0] if stripped.split() else ""
    return first_token in _WORK_TITLE_PREFIXES


def filter_work_author_folio(
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Re-type WORK_AUTHOR entities whose text is actually a folio ref.

    Returns the same list (mutated in place) for ergonomic chaining.
    A WORK_AUTHOR span whose text matches :data:`_FOLIO_PREFIX_RE`
    (digits followed by a Hebrew side letter) is re-tagged as
    ``FOLIO`` and stamped with ``retyped_from`` so callers can tell
    a real WORK_AUTHOR from a recovered one.

    A WORK_AUTHOR span whose text starts with a WORK morphology
    marker (:data:`_WORK_TITLE_PREFIXES`) is re-typed to ``WORK``
    instead — these are full work titles (with or without an embedded
    author) the contents NER mis-classified, and they belong on P1574
    not on P50.
    """
    for ent in entities:
        if ent.get("type") != "WORK_AUTHOR":
            continue
        text = str(ent.get("text") or "")
        if _has_work_title_prefix(text):
            ent["type"] = "WORK"
            ent["retyped_from"] = "WORK_AUTHOR"
            continue
        if _FOLIO_PREFIX_RE.match(text):
            ent["type"] = "FOLIO"
            ent["retyped_from"] = "WORK_AUTHOR"
    return entities


# ─────────────────────────────────────────────────────────────────────
# B2 — COLLECTION catalog-citation filter (two-layer)
# ─────────────────────────────────────────────────────────────────────

# Surnames that almost always appear as catalog *citations* in MARC —
# their bibliographies are the primary references for Hebrew manuscript
# descriptions. A string matching ``<surname> <digits>`` with one of
# these surnames is a citation, NOT a collection.
_KNOWN_CATALOGUER_SURNAMES: frozenset[str] = frozenset({
    "גסטר", "Gaster",
    "הלברשטם", "Halberstam",
    "מרצבכר", "Merzbacher",
    "שטיינשניידר", "Steinschneider",
    "נויבאואר", "Neubauer",
    "מרגליות", "Margaliouth", "Margoliouth",
    "קסוטו", "Cassuto",
    "שטראק", "Strack",
    "אלוני", "Allony",
    "בנעט", "Bennet",
    "ריכלר", "Richler",
    "ז'נון", "Zinberg",
})

# Surnames that ALSO label real collections (Sassoon Collection,
# Schocken Library, Mocatta Collection, Adler manuscripts, Kaufmann
# Collection at the Hungarian Academy, etc.). For these we keep the
# string as a COLLECTION only when the surrounding context contains
# institution markers; otherwise we route to the catalog-citation
# fallback (safer to under-emit P195 than over-emit per Rule 25).
_KNOWN_INSTITUTION_SURNAMES: frozenset[str] = frozenset({
    "ששון", "Sassoon",
    "שוקן", "Schocken",
    "מוקטה", "Mocatta",
    "אדלר", "Adler",
    "קאופמן", "Kaufmann",
    "פירקוביץ", "Firkovich",
    "אוקספורד", "Oxford",
    "בודלי", "Bodleian",
})

# Markers that confirm a string with an institution-eligible surname
# is being used as a collection name in the surrounding text.
_INSTITUTION_MARKERS: frozenset[str] = frozenset({
    "אוסף", "ספריית", "ספריה", "אוניברסיטת",
    "Library", "Collection", "Universität", "Bibliothek",
    " ms ", " MS ", " mss ", " MSS ",
})

# A catalog citation looks like:
#   "מ' גסטר." (initial + surname + period)
#   "הלברשטם 89." (surname + ms number)
#   "Gaster 12,"
# The regex captures: optional given-name initials/words, the surname,
# optional digits and punctuation. We match liberally and disambiguate
# via the surname allowlists above.
_CATALOG_CITATION_RE = re.compile(
    r"^\s*([\u0590-\u05ff'A-Za-z. ]+?)\s*(\d*)\s*[.,;:]?\s*$"
)


def _surname_in(text: str, surnames: frozenset[str]) -> str | None:
    """Return the matched surname iff *text* mentions one."""
    for s in surnames:
        if s in text:
            return s
    return None


def filter_collection_citations(
    entities: list[dict[str, Any]],
    *,
    surrounding_text: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    """Separate real COLLECTION names from catalog-citation lookalikes.

    Returns ``(kept_entities, catalog_refs)``. A COLLECTION span with
    ``<surname> <digits>`` shape is disambiguated against two curated
    surname allowlists:

    * Surname in :data:`_KNOWN_CATALOGUER_SURNAMES` → catalog citation,
      route to ``catalog_refs``.
    * Surname in :data:`_KNOWN_INSTITUTION_SURNAMES` → keep as COLLECTION
      iff *surrounding_text* mentions an institution marker (אוסף,
      Library, ms, …); otherwise route to ``catalog_refs``. The
      no-marker fallback is the safer default — better to under-emit
      P195 than emit one pointing at a non-existent institution.
    * Citation-shape with unknown surname → route to ``catalog_refs``.
    * Any other COLLECTION → keep unchanged.
    """
    kept: list[dict[str, Any]] = []
    catalog_refs: list[str] = []
    haystack_lower = surrounding_text  # markers are language-mixed; case-sensitive Hebrew is fine
    for ent in entities:
        if ent.get("type") != "COLLECTION":
            kept.append(ent)
            continue
        text = str(ent.get("text") or "").strip()
        if not text:
            kept.append(ent)
            continue
        match = _CATALOG_CITATION_RE.match(text)
        if not match:
            kept.append(ent)
            continue

        cataloguer = _surname_in(text, _KNOWN_CATALOGUER_SURNAMES)
        if cataloguer is not None:
            catalog_refs.append(text)
            continue

        institution = _surname_in(text, _KNOWN_INSTITUTION_SURNAMES)
        if institution is not None:
            has_marker = any(m in haystack_lower for m in _INSTITUTION_MARKERS)
            if has_marker:
                kept.append(ent)
            else:
                catalog_refs.append(text)
            continue

        # Unknown surname matching the citation pattern. Safer to route
        # to catalog notes than to emit a wrong P195 claim.
        catalog_refs.append(text)

    return kept, catalog_refs


# ─────────────────────────────────────────────────────────────────────
# B3 — OWNER length cap with provenance_inscriptions fallback
# ─────────────────────────────────────────────────────────────────────

OWNER_MAX_LENGTH: int = 80


def filter_owner_length(
    entities: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop OWNER entities longer than :data:`OWNER_MAX_LENGTH` chars.

    A name belongs in P127; a full bill-of-sale paragraph belongs in
    P7535. Hebrew provenance NER frequently produces the latter when
    the inscription has no clean head/tail boundary. The full text is
    preserved in the returned ``inscriptions`` list so the caller can
    append it to a record-level ``provenance_inscriptions`` field.
    """
    kept: list[dict[str, Any]] = []
    inscriptions: list[str] = []
    for ent in entities:
        if ent.get("type") == "OWNER":
            text = str(ent.get("text") or "")
            if len(text) > OWNER_MAX_LENGTH:
                inscriptions.append(text)
                continue
        kept.append(ent)
    return kept, inscriptions


# ─────────────────────────────────────────────────────────────────────
# B4 — Person NER hallucination filter
# ─────────────────────────────────────────────────────────────────────

# Hebrew topic / meta keywords the person NER frequently emits as
# spurious person spans. Extend when a new false-positive class
# surfaces.
_HEBREW_TOPIC_DENYLIST: frozenset[str] = frozenset({
    "ספרד", "פולין", "אשכנז", "צרפת", "איטליה", "תוגרמה",
    "קבלה", "גמרא", "תלמוד", "תורה", "משנה", "הלכה",
    "אוטוגרף", "קולופון", "כריכה", "קלף", "כתב יד",
    "משיח", "גאולה",
    "אונקלוס", "עונקלוס",
})

# Hebrew place names and rite designators that surface in MARC subject
# headings and bibliographic notes. The person NER mis-tags them as
# author / owner spans because they share the morphology of Hebrew
# proper nouns. They should never become person items on Wikidata —
# real names like "משה ממנטובה" remain unaffected because the filter
# only matches the place token in isolation.
_HEBREW_PLACE_DENYLIST: frozenset[str] = frozenset({
    "מנטובה", "קנדיאה", "ויניציאה", "קרפנטרץ", "קארפינטראץ",
    "אוגיניון", "תרודנט", "מקדם", "פראג", "אמסטרדם", "ליוורנו",
    "פיזרו", "ורמייזא", "פרנקפורט", "פרנקפורט אם מיין", "פדובה",
    "סלוניקי", "ירושלים", "חברון", "צפת", "מצרים", "קושטא",
    "קושטנדינא", "איסטנבול",
})

# Bible book names (Hebrew + English) the person NER occasionally
# mis-tags as a person — typically with a TRANSLATOR or COMMENTATOR
# role on a span like "ישעיהו" picked up from a subject heading.
_BIBLE_BOOK_DENYLIST: frozenset[str] = frozenset({
    # Hebrew — 24 books of the Tanakh
    "בראשית", "שמות", "ויקרא", "במדבר", "דברים",
    "יהושע", "שופטים", "שמואל", "מלכים",
    "ישעיהו", "ירמיהו", "יחזקאל",
    "הושע", "יואל", "עמוס", "עובדיה", "יונה", "מיכה",
    "נחום", "חבקוק", "צפניה", "חגי", "זכריה", "מלאכי",
    "תהלים", "משלי", "איוב", "שיר השירים", "רות",
    "איכה", "קהלת", "אסתר", "דניאל", "עזרא", "נחמיה",
    "דברי הימים",
    # English equivalents
    "genesis", "exodus", "leviticus", "numbers", "deuteronomy",
    "joshua", "judges", "samuel", "kings",
    "isaiah", "jeremiah", "ezekiel",
    "hosea", "joel", "amos", "obadiah", "jonah", "micah",
    "nahum", "habakkuk", "zephaniah", "haggai", "zechariah", "malachi",
    "psalms", "proverbs", "job", "ecclesiastes", "esther", "daniel",
})

# English / Latin topic words and acronyms commonly mis-tagged as
# persons. Extend when a new false-positive class surfaces.
_LATIN_TOPIC_DENYLIST: frozenset[str] = frozenset({
    "kabbalah", "messiah", "yihudim", "torah", "talmud", "halakhah",
    "midrash", "zohar", "siddur", "pesach", "yom kippur",
    "TPP", "NASH PAPYRUS", "PAPYRUS",
    "Idra Raba",
})

# Hebrew subject-heading marker. MARC subject headings prefixed by
# "נושא נוסף:" feed the person NER topic words like "פורים", "סוס",
# "פולמוס" that the tagger then emits as AUTHOR spans. Detected via
# the preceding ≤30-char window of the surrounding text.
_SUBJECT_HEADING_MARKER: str = "נושא נוסף"
_SUBJECT_HEADING_WINDOW: int = 30

# Uncertainty markers that almost always indicate a non-person span
# (cataloguer's note about an unclear reading).
_UNCERTAINTY_MARKER_RE = re.compile(r"[\?\[\]]")

# Minimum number of Hebrew letter characters in a Hebrew name. Single
# tokens like "נח" are too short to disambiguate against authority
# files — let them through only if a Latin name is also present.
_MIN_HEBREW_LETTERS: int = 3
_HEBREW_LETTER_RE = re.compile(r"[\u05d0-\u05ea]")


def filter_person_hallucinations(
    entities: list[dict[str, Any]],
    *,
    surrounding_text: str = "",
) -> list[dict[str, Any]]:
    """Drop person_ner entities that are almost certainly not persons.

    Conservative — only drops entities matching one of:

    * Preceded in *surrounding_text* (within 30 chars) by the MARC
      subject-heading marker ``נושא נוסף`` — the span is a topic
      keyword harvested from a subject line, never a person.
    * A Hebrew topic keyword (קבלה, ספרד, אוטוגרף, …).
    * A Hebrew place / rite token (מנטובה, ויניציאה, ירושלים, …).
    * A Bible book name in Hebrew or English (בראשית, Genesis, …).
    * A Latin topic keyword or ALL-CAPS ASCII fragment (NASH PAPYRUS,
      TPP) — never plausible as a personal name.
    * An MARC uncertainty marker (``?`` / ``[`` / ``]``) — the
      cataloguer wasn't sure, so authority-matching the span is
      worse than dropping it.
    * Fewer than :data:`_MIN_HEBREW_LETTERS` Hebrew letters AND no
      Latin word characters — single Hebrew tokens are unreliable
      as authority keys.

    *surrounding_text* (when supplied) is the full record-level text
    used for the subject-heading window check; an empty string
    disables that one check defensively without affecting the others.
    """
    kept: list[dict[str, Any]] = []
    for ent in entities:
        if ent.get("source") != "person_ner":
            kept.append(ent)
            continue
        name = str(ent.get("person") or "").strip()
        reason = _hallucination_reason(name)
        if reason is None and surrounding_text:
            start = ent.get("start")
            if isinstance(start, int) and start >= 0:
                window = surrounding_text[max(0, start - _SUBJECT_HEADING_WINDOW):start]
                if _SUBJECT_HEADING_MARKER in window:
                    reason = "subject_heading_marker"
        if reason is None:
            kept.append(ent)
        else:
            ent["rejected_reason"] = reason
            # Drop — do not emit. (We don't keep rejected entities in
            # the live list because the Stage 3 reconciler doesn't
            # check ``rejected_reason``; if we kept them, they'd flow
            # through and create wrong items.)
    return kept


def _hallucination_reason(name: str) -> str | None:
    """Return a short reason string if *name* is a hallucination, else None."""
    if not name:
        return "empty"
    # 1. Hebrew topic denylist
    if name in _HEBREW_TOPIC_DENYLIST:
        return "hebrew_topic_denylist"
    # 2. Hebrew place / rite denylist
    if name in _HEBREW_PLACE_DENYLIST:
        return "hebrew_place_denylist"
    # 3. Bible book denylist (case-insensitive for the English half)
    name_lower = name.lower()
    if name in _BIBLE_BOOK_DENYLIST or name_lower in _BIBLE_BOOK_DENYLIST:
        return "bible_book_denylist"
    # 4. Latin topic denylist (case-insensitive comparison)
    for topic in _LATIN_TOPIC_DENYLIST:
        if topic.lower() == name_lower:
            return "latin_topic_denylist"
    # 5. Uncertainty markers
    if _UNCERTAINTY_MARKER_RE.search(name):
        return "uncertainty_marker"
    # 6. ALL-CAPS ASCII fragments (no Hebrew, no lowercase, no spaces
    #    of any plausible name shape: ``"NASH PAPYRUS"``, ``"TPP"``).
    is_all_ascii = name.isascii()
    has_hebrew = bool(_HEBREW_LETTER_RE.search(name))
    if is_all_ascii and not has_hebrew:
        # All-uppercase ASCII (allow underscores/digits but no lowercase)
        if name == name.upper() and any(c.isalpha() for c in name):
            return "all_caps_ascii"
    # 7. Insufficient Hebrew letter count AND no Latin name pattern
    hebrew_letter_count = len(_HEBREW_LETTER_RE.findall(name))
    has_latin_word = any(c.isalpha() and c.isascii() for c in name)
    if hebrew_letter_count < _MIN_HEBREW_LETTERS and not has_latin_word:
        return "too_short_hebrew"
    return None


# ─────────────────────────────────────────────────────────────────────
# F6 — Per-record same-name role dedup
# ─────────────────────────────────────────────────────────────────────

# Role priority — when the same person is tagged with multiple roles
# in one record, keep the highest-priority role.
_ROLE_PRIORITY: dict[str, int] = {
    "AUTHOR": 5,
    "TRANSCRIBER": 4,
    "COMMENTATOR": 3,
    "TRANSLATOR": 2,
    "OWNER": 1,
}


def filter_person_role_dedup(
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse same-name multi-role person entries to a single canonical row.

    The keyword classifier in ``ner/inference_pipeline.py`` runs once
    per text segment, so a person mentioned in multiple segments of the
    same record gets a fresh role classification each time. The
    surrounding context can drift (one segment says "the scribe Eleazar
    wrote", another says "Eleazar's commentary on") and the same person
    ends up with three different roles. Stage 3 would then create three
    separate authority candidates.

    Group ``person_ner`` entities by their normalised ``person`` text
    and keep only one row per group: the row whose role has the
    highest :data:`_ROLE_PRIORITY`. Ties are broken by the first row
    encountered (stable input order). Other-source entities pass
    through untouched.

    Operates per call — the caller MUST pass entities for one record
    at a time. ``NerWorker`` already has the per-record entity list in
    scope before it joins them into ``all_entities``.
    """
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}  # normalised name → index in `out`
    for ent in entities:
        if ent.get("source") != "person_ner":
            out.append(ent)
            continue
        name = str(ent.get("person") or "").strip()
        if not name:
            out.append(ent)
            continue
        key = name.lower()
        if key not in seen:
            seen[key] = len(out)
            out.append(ent)
            continue
        # Collide — compare role priority.
        existing = out[seen[key]]
        existing_pri = _ROLE_PRIORITY.get(str(existing.get("role") or ""), 0)
        new_pri = _ROLE_PRIORITY.get(str(ent.get("role") or ""), 0)
        if new_pri > existing_pri:
            out[seen[key]] = ent
        # else: drop the colliding entity
    return out


# ─────────────────────────────────────────────────────────────────────
# F7 — DATE shape filter
# ─────────────────────────────────────────────────────────────────────

# Shapes a real Hebrew-manuscript date can take. The provenance NER
# occasionally tags shelfmark suffixes / catalog item numbers / narrative
# verbs as DATE; this regex screens for the four shapes that are
# actually parseable downstream.
_DATE_SHAPE_RE = re.compile(
    r"""
    \b\d{3,4}\b          # 3-4 digit Gregorian year (e.g. 1654, 1826, 982)
    | [\u05d0-\u05ea]['\u05F3]?[\u05d0-\u05ea]{1,4}["\u05F4][\u05d0-\u05ea]
                         # Hebrew gershayim form (e.g. תפ"ט, רמ"ב, ב'קל"ז)
    | \[\s*=\s*\d{3,4}\s*\]
                         # MARC Gregorian-equivalent bracket (e.g. [=1826])
    | \bca\.\s*\d{3,4}\b
                         # circa-form (e.g. ca. 1500)
    """,
    re.VERBOSE,
)


def filter_date_shape(
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop ``provenance_ner`` DATE entities whose text doesn't match
    a parseable date shape.

    Keeps four canonical Hebrew-manuscript date forms — Gregorian
    years, Hebrew gershayim chronograms, MARC ``[=YYYY]`` equivalence
    brackets, and ``ca.`` circa-forms. Anything else (shelfmark
    suffixes, catalog item numbers, narrative verbs) is dropped.

    Other-source / other-type entities pass through untouched.
    """
    out: list[dict[str, Any]] = []
    for ent in entities:
        if ent.get("source") != "provenance_ner" or ent.get("type") != "DATE":
            out.append(ent)
            continue
        text = str(ent.get("text") or "")
        if _DATE_SHAPE_RE.search(text):
            out.append(ent)
        # else: drop silently — not a real date
    return out


# ─────────────────────────────────────────────────────────────────────
# F8 — MARC-grounded auto-approval filter (additive, non-destructive)
# ─────────────────────────────────────────────────────────────────────
#
# WHY: eval-agent's 2026-05-22 run on the canonical test_subset.tsv
# showed that per (evaluator, sub_type) strict precision at the global
# 0.85 confidence threshold ranges from 100 % (FOLIO, OWNER, DATE) to
# 25 % (person_ner.TRANSCRIBER / TRANSLATOR) to 6 %
# (marc500_colophon.COLOPHON). The classifier-level confidence score
# is therefore mis-calibrated as an auto-approval signal — most of the
# "high-confidence wrong" cases are predictions that contradict the
# MARC source the entity should have been read from.
#
# WHAT: deterministic check that asks "is this entity's text actually
# present in the MARC field its predicted role / type implies?". When
# the answer is "no", we don't drop the entity — we stamp it with
# ``grounded = False`` so the GUI's auto-approve gate can refuse to
# auto-approve unconditionally.
#
# This is the pipeline-side answer to the eval-agent's
# Gemini-judge precision data. The GUI can then offer:
#
#   auto_approve = (confidence >= threshold[evaluator][sub_type]
#                   AND grounded)
#
# where ``threshold[…]`` is sourced from
# ``state/runs/<id>/per_sub_type_thresholds.yaml`` (the eval-agent's
# ``calibrate`` subcommand emits it).

# Map of predicted person ROLE → MARC field(s) where evidence must live
# for the entity to count as grounded. Order matters — first hit wins
# and lands in ``grounded_field``.
_PERSON_ROLE_TO_MARC_FIELDS: dict[str, tuple[str, ...]] = {
    "AUTHOR":       ("authors",),
    "TRANSCRIBER":  ("colophon_text", "data_from_colophon.scribe",
                     "contributors"),
    "TRANSLATOR":   ("contributors", "notes"),
    "COMMENTATOR":  ("contributors", "notes"),
    "EDITOR":       ("contributors",),
    "CENSOR":       ("notes", "contributors"),
    "OWNER":        ("provenance", "notes"),
}

# Fields searched when role is unknown / unmapped — any hit grounds.
_PERSON_FALLBACK_FIELDS: tuple[str, ...] = (
    "authors", "contributors", "colophon_text",
    "data_from_colophon.scribe", "provenance", "notes",
)

# Map of provenance / contents entity TYPE → MARC field(s).
_PROVENANCE_TYPE_TO_MARC_FIELDS: dict[str, tuple[str, ...]] = {
    "OWNER":      ("provenance", "notes"),
    "DATE":       ("colophon_text", "provenance", "notes", "dates"),
    "COLLECTION": ("provenance", "notes"),
}

_CONTENTS_TYPE_TO_MARC_FIELDS: dict[str, tuple[str, ...]] = {
    "WORK":        ("contents", "notes", "canonical_references",
                    "colophon_text"),
    "FOLIO":       ("contents", "notes"),
    "WORK_AUTHOR": ("contents", "notes", "canonical_references"),
}


def _norm_for_match(text: str) -> str:
    """Normalize Hebrew/Latin text for substring matching.

    Collapses internal whitespace, swaps ASCII for Hebrew quote marks
    (``"`` ≡ ``״``, ``'`` ≡ ``׳``), lower-cases ASCII letters. Hebrew
    is unchanged because nikud is rare in MARC and we don't have a
    full unicode-normaliser dependency in this module.
    """
    if not text:
        return ""
    t = text.strip().lower()
    t = t.replace("״", '"').replace("׳", "'")
    t = re.sub(r"\s+", " ", t)
    return t


def _name_appears(name: str, haystack: str) -> bool:
    """True iff *name* appears in *haystack* allowing word-order swap.

    ``"ריאיטי, חזקיה"`` ≡ ``"חזקיה ריאיטי"`` — both forms count as a
    match. Empty inputs return False.
    """
    n = _norm_for_match(name)
    h = _norm_for_match(haystack)
    if not n or not h:
        return False
    if n in h:
        return True
    # MARC inverts personal names as "Surname, Given" — try the swap.
    if "," in n:
        parts = [p.strip() for p in n.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            swapped = f"{parts[1]} {parts[0]}"
            if swapped in h:
                return True
    # Token-set fallback: every name token appears in haystack. Helps
    # when MARC drops honorifics or word order differs by ≥ 1 token.
    tokens = [t for t in n.split() if t]
    if len(tokens) >= 2 and all(t in h for t in tokens):
        return True
    return False


def _resolve_field(marc: dict[str, Any], dotted: str) -> str:
    """Return the MARC field at *dotted* (e.g. ``data_from_colophon.scribe``)
    as a flat haystack string suitable for substring matching.

    Resolves list-of-dicts shapes (``authors[].name``, ``contributors[].name``)
    AND list-of-strings (``notes``) AND nested dicts.
    """
    cur: Any = marc
    for key in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return ""
        if cur is None:
            return ""

    # Flatten by shape
    if isinstance(cur, str):
        return cur
    if isinstance(cur, list):
        chunks: list[str] = []
        for item in cur:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                # Common name-bearing keys across MARC dicts
                for k in ("name", "term", "title", "value", "original_string"):
                    v = item.get(k)
                    if isinstance(v, str) and v:
                        chunks.append(v)
        return " || ".join(chunks)
    if isinstance(cur, dict):
        return " || ".join(str(v) for v in cur.values() if isinstance(v, str))
    return str(cur)


def _ground_in_fields(
    needle: str, marc: dict[str, Any], fields: tuple[str, ...],
    *, person_mode: bool = False,
) -> str | None:
    """Return the first dotted field name where *needle* appears, else None."""
    for field in fields:
        haystack = _resolve_field(marc, field)
        if not haystack:
            continue
        if person_mode:
            if _name_appears(needle, haystack):
                return field
        else:
            if _norm_for_match(needle) in _norm_for_match(haystack):
                return field
    return None


# ── Broad evidence search (every MARC field, full vs partial match) ──
#
# The strict ``_ground_in_fields`` only checks the field implied by the
# entity's role/type — that's the right signal for the auto-approve
# gate. For the GUI's "Exists in" column we want a richer picture:
# WHICH MARC fields mention this name at all, and how strongly.
#
# Match types:
#   ``full``    — needle is a substring of haystack (after norm), OR
#                 token-set equality (handles "Yossi Stiwi" vs
#                 "Stiwi Yossi"), OR exact word-swap from the "Last,
#                 First" MARC inversion.
#   ``partial`` — at least one needle token appears in haystack but
#                 it is NOT a full match. Handles "Stiwi" finding
#                 "Yossi Stiwi" — useful evidence even though the
#                 prediction is incomplete.

# Fields searched for the broad ``exists_in`` audit. The set is a
# superset of the strict role-mapped fields — every text-bearing MARC
# field worth surfacing in the UI.
_BROAD_AUDIT_FIELDS: tuple[str, ...] = (
    "title",
    "variant_titles",
    "authors",
    "contributors",
    "provenance",
    "notes",
    "contents",
    "colophon_text",
    "data_from_colophon.scribe",
    "data_from_colophon.year",
    "data_from_colophon.place",
    "subjects",
    "canonical_references",
    "related_works",
    "place",
    "related_places",
    "dates.original_string",
    "shelfmark",
    "genres",
)


# Punctuation we strip when tokenising for partial-match detection.
_TOKEN_PUNCT_RE = re.compile(r"[\s,;:.\"׳״'\[\]()<>]+")


def _tokens_for_match(text: str) -> list[str]:
    """Split ``text`` into normalised tokens for set-equality comparison.

    Hebrew + ASCII safe — splits on whitespace and common punctuation,
    drops empty tokens, lowercases ASCII.
    """
    norm = _norm_for_match(text)
    if not norm:
        return []
    return [t for t in _TOKEN_PUNCT_RE.split(norm) if t]


def _classify_match(needle: str, haystack: str) -> str | None:
    """Classify a single needle-vs-haystack comparison.

    Returns ``"full"``, ``"partial"``, or ``None``. ``full`` means the
    whole prediction is accounted for in the haystack (substring,
    word-order swap, or token-set equality on shortish strings).
    ``partial`` means ≥1 needle token appears but not all of them.
    """
    n = _norm_for_match(needle)
    h = _norm_for_match(haystack)
    if not n or not h:
        return None

    # Path 1: direct substring → full match
    if n in h:
        return "full"

    n_tokens = _tokens_for_match(needle)
    h_tokens = _tokens_for_match(haystack)
    if not n_tokens:
        return None
    h_set = set(h_tokens)
    n_set = set(n_tokens)

    # Path 2: token-set equality (handles "Yossi Stiwi" vs "Stiwi Yossi"
    # plus any other permutation; also catches "Last, First" inversion
    # because the comma drops out via _TOKEN_PUNCT_RE).
    if n_set == h_set and n_set:
        return "full"

    # Path 3: needle is a token-subset of haystack — meaning EVERY
    # needle token appears in haystack but haystack has more. This is
    # still a "full" match for short names where the haystack is the
    # canonical form (e.g., MARC has "Surname, Given Middle" and we
    # predicted "Given Surname").
    if n_set.issubset(h_set):
        return "full"

    # Path 4: partial — at least one shared token but not all.
    if n_set & h_set:
        return "partial"

    return None


def _iter_audit_fields(
    marc: dict[str, Any],
) -> list[tuple[str, str]]:
    """Yield ``(field_path, value_str)`` for every audited MARC field.

    Multi-valued fields (``authors[]``, ``contributors[]``,
    ``notes[]``, ``subjects[]``) are exploded so each entry is checked
    independently — this lets the GUI cite the specific list index in
    the evidence popup (``contributors[2]`` rather than the whole list).
    """
    out: list[tuple[str, str]] = []
    for path in _BROAD_AUDIT_FIELDS:
        # Walk dotted path
        cur: Any = marc
        for key in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(key)
            else:
                cur = None
                break
            if cur is None:
                break
        if cur is None:
            continue

        if isinstance(cur, str):
            if cur.strip():
                out.append((path, cur))
            continue
        if isinstance(cur, (int, float)):
            out.append((path, str(cur)))
            continue
        if isinstance(cur, list):
            for idx, item in enumerate(cur):
                if isinstance(item, str):
                    if item.strip():
                        out.append((f"{path}[{idx}]", item))
                elif isinstance(item, dict):
                    # Surface every name-bearing field separately so
                    # the GUI snippet stays grounded to the source row.
                    for k in ("name", "term", "title", "value",
                              "original_string", "role", "hierarchy",
                              "book", "relationship"):
                        v = item.get(k)
                        if isinstance(v, str) and v.strip():
                            out.append((f"{path}[{idx}].{k}", v))
                else:
                    out.append((f"{path}[{idx}]", str(item)))
            continue
        if isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(v, str) and v.strip():
                    out.append((f"{path}.{k}", v))
    return out


def find_marc_evidence(
    needle: str, marc_record: dict[str, Any],
) -> list[dict[str, Any]]:
    """Search every audited MARC field for ``needle``.

    Returns a list of evidence rows shaped:

        {"field": str, "match_type": "full"|"partial", "value": str}

    Ordered: every full match first (in MARC declaration order), then
    every partial match. ``value`` is the original (un-normalised) MARC
    string so the GUI can render it verbatim and highlight the matched
    span.
    """
    if not needle.strip() or not marc_record:
        return []
    full: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for field_path, value in _iter_audit_fields(marc_record):
        mtype = _classify_match(needle, value)
        if mtype == "full":
            full.append({"field": field_path, "match_type": "full",
                          "value": value})
        elif mtype == "partial":
            partial.append({"field": field_path, "match_type": "partial",
                             "value": value})
    return full + partial


def _entity_needle(ent: dict[str, Any]) -> str:
    """Return the searchable text for an entity (``person`` for
    person_ner, ``text`` for the others)."""
    if ent.get("source") == "person_ner":
        return str(ent.get("person") or "")
    return str(ent.get("text") or "")


def filter_with_marc_grounding(
    entities: list[dict[str, Any]],
    *,
    marc_record: dict[str, Any],
) -> list[dict[str, Any]]:
    """Stamp every entity with ``grounded`` + ``grounded_field`` (strict
    role-mapped gate) and ``exists_in`` (broad evidence list for UI).

    Non-destructive: no entities are dropped. The GUI's auto-approve
    gate is expected to read ``grounded`` AND ``confidence`` together
    rather than relying on confidence alone, while ``exists_in`` powers
    the "Exists in" column + evidence popup so a reviewer can see
    every MARC field where the predicted text appears (full or partial
    match).

    Strict grounding rules (drive ``grounded`` / ``grounded_field``):

    - ``person_ner`` entities: look for the name in the MARC field
      mapped from the predicted role (e.g., role=TRANSCRIBER →
      ``colophon_text`` or ``data_from_colophon.scribe``).
    - ``provenance_ner`` entities: look for the text in the MARC field
      mapped from the predicted type (OWNER → ``provenance`` / ``notes``).
    - ``contents_ner`` entities: look for the text in ``contents`` /
      ``notes`` / ``canonical_references`` / ``colophon_text``.
    - Unknown source / unmapped role/type: fall back to a broad
      person-fields search (for person_ner) or to the full set of
      content fields. Sets ``grounded_field`` to the matching field
      name on success, ``None`` on failure.

    Broad evidence (drives ``exists_in``):

    Every entity also gets ``exists_in`` populated by
    :func:`find_marc_evidence` — a list of every MARC field where the
    predicted text appears, with a match_type of ``"full"`` or
    ``"partial"``. Independent of the strict role check; useful for
    the human reviewer who wants to know "where else does this name
    appear in MARC?".

    The MARC record dict is the Stage-1 ``marc_extracted.json`` entry
    for the same control number.
    """
    if not marc_record:
        # No MARC context to verify against — stamp everything as
        # ungrounded so the GUI must treat every prediction as
        # needing review. exists_in stays empty for the same reason.
        for ent in entities:
            ent["grounded"] = False
            ent["grounded_field"] = None
            ent["exists_in"] = []
        return entities

    for ent in entities:
        source = ent.get("source")
        if source == "person_ner":
            name = str(ent.get("person") or "")
            role = str(ent.get("role") or "").upper()
            fields = _PERSON_ROLE_TO_MARC_FIELDS.get(role, _PERSON_FALLBACK_FIELDS)
            grounded_field = _ground_in_fields(
                name, marc_record, fields, person_mode=True,
            )
            ent["grounded"] = grounded_field is not None
            ent["grounded_field"] = grounded_field
            ent["exists_in"] = find_marc_evidence(name, marc_record)
            continue

        if source == "provenance_ner":
            text = str(ent.get("text") or "")
            etype = str(ent.get("type") or "").upper()
            fields = _PROVENANCE_TYPE_TO_MARC_FIELDS.get(
                etype, ("provenance", "notes"),
            )
            grounded_field = _ground_in_fields(text, marc_record, fields)
            ent["grounded"] = grounded_field is not None
            ent["grounded_field"] = grounded_field
            ent["exists_in"] = find_marc_evidence(text, marc_record)
            continue

        if source == "contents_ner":
            text = str(ent.get("text") or "")
            etype = str(ent.get("type") or "").upper()
            fields = _CONTENTS_TYPE_TO_MARC_FIELDS.get(
                etype, ("contents", "notes"),
            )
            grounded_field = _ground_in_fields(text, marc_record, fields)
            ent["grounded"] = grounded_field is not None
            ent["grounded_field"] = grounded_field
            ent["exists_in"] = find_marc_evidence(text, marc_record)
            continue

        # Unknown source — leave the flags absent rather than guess.
        ent.setdefault("grounded", None)
        ent.setdefault("grounded_field", None)
        ent.setdefault("exists_in", [])

    return entities


__all__ = [
    "filter_collection_citations",
    "filter_date_shape",
    "filter_owner_length",
    "filter_person_hallucinations",
    "filter_person_role_dedup",
    "filter_with_marc_grounding",
    "filter_work_author_folio",
    "find_marc_evidence",
    "OWNER_MAX_LENGTH",
]
