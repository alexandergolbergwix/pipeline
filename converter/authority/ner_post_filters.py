"""Deterministic post-filters for Stage 2 NER outputs.

Each filter prevents a specific class of NER mistake from flowing to
Stage 3 / Stage 4 and producing a wrong Wikidata claim:

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


def filter_work_author_folio(
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Re-type WORK_AUTHOR entities whose text is actually a folio ref.

    Returns the same list (mutated in place) for ergonomic chaining.
    A WORK_AUTHOR span whose text matches :data:`_FOLIO_PREFIX_RE`
    (digits followed by a Hebrew side letter) is re-tagged as
    ``FOLIO`` and stamped with ``retyped_from`` so callers can tell
    a real WORK_AUTHOR from a recovered one.
    """
    for ent in entities:
        if ent.get("type") != "WORK_AUTHOR":
            continue
        text = str(ent.get("text") or "")
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
})

# English / Latin topic words and acronyms commonly mis-tagged as
# persons. Extend when a new false-positive class surfaces.
_LATIN_TOPIC_DENYLIST: frozenset[str] = frozenset({
    "kabbalah", "messiah", "yihudim", "torah", "talmud", "halakhah",
    "midrash", "zohar", "siddur", "pesach", "yom kippur",
    "TPP", "NASH PAPYRUS", "PAPYRUS",
    "Idra Raba",
})

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
) -> list[dict[str, Any]]:
    """Drop person_ner entities that are almost certainly not persons.

    Conservative — only drops entities matching one of:

    * A Hebrew topic keyword (קבלה, ספרד, אוטוגרף, …).
    * A Latin topic keyword or ALL-CAPS ASCII fragment (NASH PAPYRUS,
      TPP) — never plausible as a personal name.
    * An MARC uncertainty marker (``?`` / ``[`` / ``]``) — the
      cataloguer wasn't sure, so authority-matching the span is
      worse than dropping it.
    * Fewer than :data:`_MIN_HEBREW_LETTERS` Hebrew letters AND no
      Latin word characters — single Hebrew tokens are unreliable
      as authority keys.
    """
    kept: list[dict[str, Any]] = []
    for ent in entities:
        if ent.get("source") != "person_ner":
            kept.append(ent)
            continue
        name = str(ent.get("person") or "").strip()
        reason = _hallucination_reason(name)
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
    # 1. Hebrew topic denylist (case-insensitive on the Latin half)
    if name in _HEBREW_TOPIC_DENYLIST:
        return "hebrew_topic_denylist"
    # 2. Latin topic denylist (case-insensitive comparison)
    name_lower = name.lower()
    for topic in _LATIN_TOPIC_DENYLIST:
        if topic.lower() == name_lower:
            return "latin_topic_denylist"
    # 3. Uncertainty markers
    if _UNCERTAINTY_MARKER_RE.search(name):
        return "uncertainty_marker"
    # 4. ALL-CAPS ASCII fragments (no Hebrew, no lowercase, no spaces
    #    of any plausible name shape: ``"NASH PAPYRUS"``, ``"TPP"``).
    is_all_ascii = name.isascii()
    has_hebrew = bool(_HEBREW_LETTER_RE.search(name))
    if is_all_ascii and not has_hebrew:
        # All-uppercase ASCII (allow underscores/digits but no lowercase)
        if name == name.upper() and any(c.isalpha() for c in name):
            return "all_caps_ascii"
    # 5. Insufficient Hebrew letter count AND no Latin name pattern
    hebrew_letter_count = len(_HEBREW_LETTER_RE.findall(name))
    has_latin_word = any(c.isalpha() and c.isascii() for c in name)
    if hebrew_letter_count < _MIN_HEBREW_LETTERS and not has_latin_word:
        return "too_short_hebrew"
    return None


__all__ = [
    "filter_collection_citations",
    "filter_owner_length",
    "filter_person_hallucinations",
    "filter_work_author_folio",
    "OWNER_MAX_LENGTH",
]
