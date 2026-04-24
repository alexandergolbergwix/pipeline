"""Human-readable labels for the Wikidata properties the pipeline emits.

The Wikidata Studio's entity view mirrors the wikidata.org page, which
shows each property's *label* (e.g. "author" for P50) next to the PID
chip. Fetching labels live would add latency and require network access,
so we ship a static map covering every property the pipeline uses.

Keep this in sync with :mod:`converter.wikidata.property_mapping`.
"""

from __future__ import annotations

PROPERTY_LABELS: dict[str, str] = {
    # Instance / classification
    "P31":  "instance of",
    "P195": "collection",
    "P217": "inventory number",
    "P279": "subclass of",
    # Terms / titles
    "P1476": "title",
    "P1448": "official name",
    "P1559": "name in native language",
    "P2093": "author name string",
    # Content
    "P407":  "language of work or name",
    "P282":  "writing system",
    "P136":  "genre",
    "P921":  "main subject",
    "P1574": "exemplar of",
    "P527":  "has parts",
    "P361":  "part of",
    # Creation / production
    "P571":  "inception",
    "P1071": "location of creation",
    "P50":   "author",
    "P11603": "transcribed by",
    "P88":   "commissioned by",
    "P110":  "illustrator",
    "P655":  "translator",
    "P9046": "commentary by",
    # Provenance
    "P127":  "owned by",
    "P580":  "start time",
    "P582":  "end time",
    "P1028": "donated by",
    "P7153": "significant place",
    "P793":  "significant event",
    # Physical description
    "P186":  "material used",
    "P2048": "height",
    "P2049": "width",
    "P1104": "number of pages",
    "P7416": "number of folios",
    "P5816": "state of conservation",
    "P1552": "has characteristic",
    "P9302": "script style",
    "P2635": "number of parts of this work",
    # Inscription / content body
    "P1684": "inscription",
    "P7535": "scope and content",
    # Digital access
    "P973":  "described at URL",
    "P6108": "manifest URL",
    "P953":  "full work available at URL",
    "P18":   "image",
    # Authority identifiers
    "P214":  "VIAF ID",
    "P8189": "National Library of Israel J9U ID",
    "P244":  "Library of Congress authority ID",
    "P227":  "GND ID",
    "P213":  "ISNI",
    "P268":  "BnF ID",
    "P1566": "GeoNames ID",
    # References
    "P248":  "stated in",
    "P854":  "reference URL",
    "P813":  "retrieved",
    "P887":  "based on heuristic",
    # Generic qualifiers
    "P1932": "object named as",
    "P1480": "sourcing circumstances",
    "P3831": "object has role",
    "P1319": "earliest date",
    "P1326": "latest date",
    # Persons
    "P106":  "occupation",
    "P569":  "date of birth",
    "P570":  "date of death",
    "P19":   "place of birth",
    "P20":   "place of death",
    "P27":   "country of citizenship",
    "P21":   "sex or gender",
    "P1412": "languages spoken, written or signed",
    "P1343": "described by source",
    # Location
    "P17":   "country",
    "P131":  "located in the administrative territorial entity",
    # Catalog
    "P528":  "catalog code",
    "P972":  "catalog",
    # Copyright
    "P6216": "copyright status",
    "P1001": "applies to jurisdiction",
    # WikiProject
    "P5008": "on focus list of Wikimedia project",
}


# ── Known-QID labels — surface the human label next to any item-value ─────
#
# Keep narrow: only the QIDs the pipeline routinely emits (genre/subject
# mappings, hardcoded country/city, calendar models, etc.).

QID_LABELS: dict[str, str] = {
    # Top-level classes
    "Q5":       "human",
    "Q43229":   "organization",
    "Q87167":   "manuscript",
    "Q47461344": "written work",
    "Q871232":  "editorial collective",     # placeholder used for some colls
    # Countries / places (pipeline hardcodes)
    "Q801":     "Israel",
    "Q1218":    "Jerusalem",
    # Calendar models
    "Q1985727": "proleptic Gregorian calendar",
    "Q1985786": "Julian calendar",
    # Hebrew / languages
    "Q9288":    "Hebrew",
    # Gender
    "Q6581097": "male",
    "Q6581072": "female",
    # Catalog
    "Q118384267": "Ktiv (NLI manuscript catalog)",
    # Common roles
    "Q916292":  "scribe",
    "Q333634":  "translator",
    "Q106313281": "commentator",
    "Q1773840": "provenance",
    # Source heuristics
    "Q2539":    "machine learning",
    # Sourcing circumstances
    "Q18122778": "presumably",
    "Q21857942": "possibly",
    # Copyright
    "Q19652":   "public domain",
}


def property_label(pid: str) -> str:
    """Return the best known label for *pid* (falls back to the PID itself)."""
    return PROPERTY_LABELS.get(pid, pid)


def qid_label(qid: str) -> str:
    """Return the best known label for *qid* (falls back to the QID itself)."""
    return QID_LABELS.get(qid, qid)
