"""Pre-approval validator for Wikidata items produced by the pipeline.

Every check here maps directly to a real community complaint from the
2026-04 incident (Pallor, Geagea, Kolja21, Epìdosis, Mcampany). The
validator is the last safety net before a user can tick "Approved" in
the Wikidata Studio — any ``ERROR``-severity issue blocks approval.

Why a separate module and not a unit test?  Unit tests catch regressions
in the *builder*; this validator catches bad items regardless of where
they came from (a stale `authority_enriched.json`, a manual edit in the
Q/P browser, a hand-added claim). It's the moat closest to the upload
action, so it's the one that must never miss.

Each issue records:

    severity      — ``"error"`` (blocks approval) or ``"warning"`` (informational)
    code          — short stable identifier (e.g. ``"P3959_ON_HUMAN"``)
    message       — human-readable explanation
    reference     — talk-page URL the user can cite back to the community
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationIssue:
    severity: str          # "error" | "warning"
    code: str
    message: str
    reference: str = ""    # community-link citing the rule


# ── Known-bad signals ────────────────────────────────────────────────────

_ANONYMOUS_KEYWORDS = {
    "unknown", "anonymous", "anon", "anon.",
    "לא ידוע", "אלמוני", "אלמונית", "פלוני",
    "משומד", "שאלוניקי",            # role / place descriptors
    "?", "-", "—", "[?]",
}

_INSTITUTIONAL_KEYWORDS = {
    # keep in sync with item_builder._INSTITUTIONAL_KEYWORDS
    "library", "libraries", "museum", "archive", "archives",
    "collection", "foundation", "institute", "institution",
    "society", "academy", "school", "college", "university",
    "seminary", "theological", "yeshiva", "beit", "bet",
    "ספרייה", "ספריה", "ספריית",       # construct form "library of X"
    "מוסד", "מכון", "חברה", "אקדמיה",
    "ישיבה", "בית", "אוסף", "מועדון",
    "מוזיאון", "מוזיאוני", "ארכיון", "ארכיוני",
    "bodleian", "palatina", "biblioteca",
}

_PLACEHOLDER_LABELS = {
    # Hebrew cataloging placeholders ("file" / "compilation" / etc.)
    "קובץ", "קובץ.", "קבץ", "קבץ.",
    "קובץ בקבלה", "קובץ בקבלה.",
    "קובץ מדרשים", "קובץ מדרשים.",
    "כתב יד", "כתב יד.",
    "מחזור", "סידור",            # when standing alone without a discriminator
    "אוסף",
}

_HEBREW_SCRIPT = re.compile(r"[\u0590-\u05ff]")
_LATIN_SCRIPT = re.compile(r"[A-Za-z]")

# Community-talk references (human-readable, not SPARQL)
_REF_NOTABILITY = "https://www.wikidata.org/wiki/Wikidata:Notability"
_REF_P3959 = "https://www.wikidata.org/wiki/Property:P3959"
_REF_P8189 = "https://www.wikidata.org/wiki/Property:P8189"
_REF_P214 = "https://www.wikidata.org/wiki/Property:P214"
_REF_P1559 = "https://www.wikidata.org/wiki/Property:P1559"
_REF_HELP_LABEL = "https://www.wikidata.org/wiki/Help:Label"
_REF_VIAF_NAMETYPE = (
    "https://www.wikidata.org/wiki/Wikidata_talk:WikiProject_Authority_control"
)


# ── The 11 checks ────────────────────────────────────────────────────────


def _stmt_values(item: Any, pid: str) -> list[str]:
    """Return every value of property *pid* on *item* as strings."""
    out: list[str] = []
    for s in getattr(item, "statements", []) or []:
        if getattr(s, "property_id", "") == pid:
            out.append(str(getattr(s, "value", "") or ""))
    return out


def _any_label(item: Any) -> str:
    labels = getattr(item, "labels", {}) or {}
    return str(labels.get("he") or labels.get("en") or
               next(iter(labels.values()), "") or "")


def _contains_institutional(text: str) -> str | None:
    lo = text.lower()
    for kw in _INSTITUTIONAL_KEYWORDS:
        if kw in lo:
            return kw
    return None


def validate_item(item: Any) -> list[ValidationIssue]:
    """Run every check against *item* and return the issues (may be empty)."""
    issues: list[ValidationIssue] = []
    etype = getattr(item, "entity_type", "") or ""
    label = _any_label(item).strip()
    labels = getattr(item, "labels", {}) or {}

    # 1. Empty label
    if not label:
        issues.append(ValidationIssue(
            "error", "EMPTY_LABEL",
            "Item has no label in any language — cannot be created.",
            _REF_HELP_LABEL,
        ))

    # 2. Anonymous / role-descriptor-only label (persons only)
    if etype == "person" and label and label.lower() in _ANONYMOUS_KEYWORDS:
        issues.append(ValidationIssue(
            "error", "ANONYMOUS_PERSON",
            f"Person with non-identifying label {label!r} — "
            f"fails notability (Wikidata:Notability).",
            _REF_NOTABILITY,
        ))

    # 3. Placeholder kovetz-style catalog label (manuscripts)
    if etype == "manuscript" and label in _PLACEHOLDER_LABELS:
        issues.append(ValidationIssue(
            "error", "KOVETZ_PLACEHOLDER",
            f"Label {label!r} is a generic catalog placeholder — "
            "replace with shelfmark-based label "
            '(e.g. "Hebrew manuscript, NLI, <shelfmark>"), '
            "move the placeholder to an alias.",
            "https://www.wikidata.org/wiki/User_talk:Alexander_Goldberg_IL"
            "#Checks_on_the_import",
        ))

    # 4. Trailing punctuation on the label — ERROR since 2026-04-24 per Epìdosis
    #    complaint on Q139231415: trailing commas made the Hebrew name look
    #    incomplete and required manual correction on 280 items.
    if label and label[-1] in ",;:.":
        issues.append(ValidationIssue(
            "error", "TRAILING_PUNCTUATION",
            f"Label ends with {label[-1]!r} — MARC ISBD artefact; "
            "strip before upload (Epìdosis report, Q139231415).",
            _REF_HELP_LABEL,
        ))

    # 5. MARC inverted form used as label ("Surname, Given") — ERROR since
    #    2026-04-24. Epìdosis explicitly corrected these on Q139230386 and
    #    asked the pipeline to emit natural order in labels, keeping the
    #    inverted form only in P1559 (native name).
    if label and "," in label and etype == "person":
        before, after = label.split(",", 1)
        if before.strip() and after.strip() and not after.strip()[0].isdigit():
            issues.append(ValidationIssue(
                "error", "INVERTED_NAME_LABEL",
                f"Label {label!r} is in MARC-inverted form — "
                "use natural 'Given Surname' in labels and keep the "
                "inverted form in P1559 only (Epìdosis report, "
                "Q139230386).",
                _REF_P1559,
            ))

    # 6. Institutional keyword on a person (P31=Q5)
    if etype == "person":
        kw = _contains_institutional(label)
        if kw:
            issues.append(ValidationIssue(
                "error", "INSTITUTION_AS_PERSON",
                f"Label contains institutional keyword {kw!r} but "
                f"entity_type=person (P31=Q5). Should be an "
                f"organisation (Q43229) or routed to P195 (collection).",
                "https://www.wikidata.org/wiki/User_talk:Alexander_Goldberg_IL"
                "#Multiple_erroneous_new_items_created",
            ))

    # 7. P3959 (NNL bibliographic) present on a person — should be P8189
    if etype == "person" and _stmt_values(item, "P3959"):
        issues.append(ValidationIssue(
            "error", "P3959_ON_HUMAN",
            "P3959 (NNL item ID) is a BIBLIOGRAPHIC identifier "
            "(prefix 99…). Human items should use P8189 "
            "(NLI J9U ID, prefix 9870…) instead.",
            _REF_P3959,
        ))

    # 8. P8189 value doesn't start with the authority prefix 9870
    for val in _stmt_values(item, "P8189"):
        if val and not val.startswith("9870"):
            issues.append(ValidationIssue(
                "error", "P8189_BAD_PREFIX",
                f"P8189 value {val!r} must start with '9870…' "
                "(authority record) — values starting with '99…' are "
                "bibliographic catalog IDs and belong on manuscripts, "
                "not people.",
                _REF_P8189,
            ))

    # 9. VIAF ID attached to an organisation
    if etype != "person" and _stmt_values(item, "P214"):
        # VIAF nameType validation should have stripped it; double-check here
        issues.append(ValidationIssue(
            "warning", "VIAF_ON_NON_PERSON",
            "P214 (VIAF) is attached to a non-person item. VIAF "
            "clusters come in four types — Personal / Corporate / "
            "Geographic / Uniform-title. Verify nameType matches the "
            "target's P31.",
            _REF_VIAF_NAMETYPE,
        ))

    # 10. P1559 language/script mismatch (e.g. Latin text tagged 'he') —
    #     ERROR since 2026-04-24 per Epìdosis request on Q139230386
    #     ("please make sure that only names in Hebrew script are entered
    #     as being in Hebrew").
    p1559_langs: list[str] = []
    for s in getattr(item, "statements", []) or []:
        if getattr(s, "property_id", "") != "P1559":
            continue
        val = getattr(s, "value", "")
        if isinstance(val, str) and ":" in val and val[:2].isalpha():
            lang, _, text = val.partition(":")
            text = text.strip('"')
        elif isinstance(val, dict):
            lang = str(val.get("language", ""))
            text = str(val.get("text", ""))
        else:
            continue
        p1559_langs.append(lang)
        if lang == "he" and _LATIN_SCRIPT.search(text) and not _HEBREW_SCRIPT.search(text):
            issues.append(ValidationIssue(
                "error", "P1559_LATIN_AS_HE",
                f"P1559 value {text!r} is tagged Hebrew (he) but "
                "contains only Latin characters — the Wikidata site "
                "flags this as a data-quality error (Q139230386).",
                _REF_P1559,
            ))

    # 10b. Same language tag used twice on P1559 — Q139230386 had two
    #      he-tagged values; Epìdosis asked us to emit one canonical
    #      value per language.
    seen: set[str] = set()
    for lang in p1559_langs:
        if lang and lang in seen:
            issues.append(ValidationIssue(
                "error", "MULTIPLE_P1559_SAME_LANG",
                f"Multiple P1559 statements tagged {lang!r} — emit "
                "one canonical native-name value per language "
                "(Epìdosis report, Q139230386).",
                _REF_P1559,
            ))
            break
        if lang:
            seen.add(lang)

    # 11. Notability — person with no external identifier and no existing QID
    if etype == "person" and not getattr(item, "existing_qid", ""):
        identifiers = any(
            _stmt_values(item, pid)
            for pid in ("P214", "P8189", "P244", "P227", "P213", "P268")
        )
        if not identifiers:
            issues.append(ValidationIssue(
                "error", "NO_IDENTIFIER",
                "Person item has no VIAF / NLI / LCCN / GND / ISNI / "
                "BnF identifier. Items without any external reference "
                "fail Wikidata:Notability and create duplicates.",
                _REF_NOTABILITY,
            ))

    # 12. Hebrew label slot contains only Latin script — common cataloger
    #     error on institutional items (Q139231608 "The Jewish Theological
    #     Seminary of Breslau" was stored in the `he` label slot). The
    #     Hebrew label should either be in Hebrew script or empty — never
    #     a pure Latin string mis-tagged as Hebrew.
    he_label = labels.get("he") if isinstance(labels, dict) else ""
    if he_label and isinstance(he_label, str):
        has_latin = bool(_LATIN_SCRIPT.search(he_label))
        has_hebrew = bool(_HEBREW_SCRIPT.search(he_label))
        if has_latin and not has_hebrew:
            issues.append(ValidationIssue(
                "error", "HE_LABEL_IS_LATIN",
                f"Hebrew label slot contains Latin-only text "
                f"{he_label!r} — store the Latin form in the `en` "
                "label, not `he` (Kolja21 report, Q139231608).",
                _REF_HELP_LABEL,
            ))

    # 13. Person label is a single short Latin surname with no identifier —
    #     Q139231258 style ("Winter"). Covered by NO_IDENTIFIER when no
    #     external IDs are attached, but we additionally surface this as
    #     a dedicated code so curators see the actionable signal.
    if (
        etype == "person"
        and label
        and _LATIN_SCRIPT.search(label)
        and not _HEBREW_SCRIPT.search(label)
        and " " not in label
        and "," not in label
        and len(label) < 15
        and not getattr(item, "existing_qid", "")
    ):
        identifiers_present = any(
            _stmt_values(item, pid)
            for pid in ("P214", "P8189", "P244", "P227", "P213", "P268")
        )
        if not identifiers_present:
            issues.append(ValidationIssue(
                "error", "AMBIGUOUS_SINGLE_NAME",
                f"Person label {label!r} is a single short name with no "
                "external identifier — ambiguous, fails Wikidata:Notability "
                "(Epìdosis report, Q139231258).",
                _REF_NOTABILITY,
            ))

    return issues


def worst_severity(issues: list[ValidationIssue]) -> str:
    """Return the worst severity among *issues* ('error' > 'warning' > 'ok')."""
    if any(i.severity == "error" for i in issues):
        return "error"
    if any(i.severity == "warning" for i in issues):
        return "warning"
    return "ok"
