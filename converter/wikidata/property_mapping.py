"""Central Wikidata property and QID mapping for Hebrew manuscripts.

All Wikidata property IDs (PIDs) and item IDs (QIDs) used by the upload
system are defined here. No API calls — pure constants and helper functions.

Sources:
- WikiProject Manuscripts Data Model
- Digital Scriptorium (McCandless & Coladangelo, 2025)
- Prebor (iConference 2020) — NLI Hebrew manuscripts
"""

from __future__ import annotations

import re
from datetime import date, timezone, datetime

# ── Wikidata PIDs ────────────────────────────────────────────────────

# Instance / classification
P_INSTANCE_OF = "P31"
P_COLLECTION = "P195"
P_INVENTORY_NUMBER = "P217"

# Content
P_TITLE = "P1476"
P_LANGUAGE = "P407"
P_WRITING_SYSTEM = "P282"
P_GENRE = "P136"
P_MAIN_SUBJECT = "P921"
P_EXEMPLAR_OF = "P1574"

# Creation / production
P_INCEPTION = "P571"
P_LOCATION_OF_CREATION = "P1071"
P_AUTHOR = "P50"
P_TRANSCRIBED_BY = "P11603"
P_COMMISSIONED_BY = "P88"

# Provenance
P_OWNED_BY = "P127"
P_START_TIME = "P580"
P_END_TIME = "P582"

# Physical description
P_MATERIAL = "P186"
P_HEIGHT = "P2048"
P_WIDTH = "P2049"
P_NUMBER_OF_PAGES = "P1104"

# Digital access
P_DESCRIBED_AT_URL = "P973"
P_IIIF_MANIFEST = "P6108"
P_IMAGE = "P18"

# Authority identifiers
P_NLI_J9U_ID = "P8189"
P_VIAF_ID = "P214"
P_GEONAMES_ID = "P1566"

# References
P_STATED_IN = "P248"
P_REFERENCE_URL = "P854"
P_RETRIEVED = "P813"

# Qualifiers
P_OBJECT_NAMED_AS = "P1932"
P_SOURCING_CIRCUMSTANCES = "P1480"

# Part relationships
P_PART_OF = "P361"
P_HAS_PARTS = "P527"

# WikiProject tagging
P_ON_FOCUS_LIST = "P5008"

# Conservation / condition
P_CONDITION = "P5816"

# Script / paleography
P_SCRIPT_STYLE = "P9302"

# Content description
P_SUMMARY = "P7535"

# Illustrator (for illuminated manuscripts)
P_ILLUSTRATOR = "P110"

# Provenance
P_SIGNIFICANT_EVENT = "P793"
P_DONATED_BY = "P1028"

# Binding
P_HAS_QUALITY = "P1552"

# Date qualifiers
P_EARLIEST_DATE = "P1319"
P_LATEST_DATE = "P1326"

# Occupation (for persons)
P_OCCUPATION = "P106"

# Date of birth / death (persons)
P_DATE_OF_BIRTH = "P569"
P_DATE_OF_DEATH = "P570"

# Country of citizenship
P_COUNTRY_OF_CITIZENSHIP = "P27"

# Catalog code
P_CATALOG_CODE = "P528"
P_CATALOG = "P972"

# Scope and content (summary / abstract)
P_SCOPE_AND_CONTENT = "P7535"

# Full work available at URL (digitized manuscript)
P_FULL_WORK_URL = "P953"

# Copyright status
P_COPYRIGHT_STATUS = "P6216"

# Folio/section qualifier (used on P1574 exemplar of)
# WikiProject Manuscripts recommends P958 (section) over P7416 (folio)
# for specifying where a work appears within a manuscript.
P_FOLIO = "P958"

# Provenance chain qualifiers
P_BEFOREHAND_OWNED_BY = "P11811"
P_AFTERWARD_OWNED_BY = "P11812"

# First/last line (manuscript incipits)
P_FIRST_LINE = "P1922"

# ── Wikidata QIDs ────────────────────────────────────────────────────

# Type classifications
Q_MANUSCRIPT = "Q87167"
Q_CODEX = "Q213924"
Q_ILLUMINATED_MANUSCRIPT = "Q48498"
Q_HUMAN = "Q5"
Q_WRITTEN_WORK = "Q47461344"
Q_ORGANIZATION = "Q43229"

# Collections / institutions
Q_NLI = "Q188915"
Q_KTIV = "Q118384267"

# Writing system
Q_HEBREW_ALPHABET = "Q33513"

# Sourcing
Q_CIRCA = "Q5727902"

# WikiProject
Q_WIKIPROJECT_MANUSCRIPTS = "Q123078816"

# Condition states
Q_GOOD_CONDITION = "Q56557591"   # preserved
Q_DAMAGED = "Q106379705"         # damaged

# Copyright status
Q_COPYRIGHTED = "Q50423863"     # copyrighted
Q_PUBLIC_DOMAIN = "Q19652"      # public domain

# Occupations for persons
Q_SCRIBE = "Q916292"
Q_AUTHOR_OCCUPATION = "Q482980"
Q_TRANSLATOR_OCCUPATION = "Q333634"
Q_COMMENTATOR_OCCUPATION = "Q106313281"

# Script styles (P9302 values) — mapped from HMO TypeScriptType
SCRIPT_TYPE_TO_QID: dict[str, str] = {
    "AshkenaziScript": "Q121094898",
    "SepharadicScript": "Q133177480",
    "ItalianScript": "Q133370075",
    "ByzantineScript": "Q133370466",
    "YemeniteScript": "Q121094936",
    "OrientalScript": "Q133327488",
}

# Genre mappings — from HMO genre → Wikidata QID
GENRE_TO_QID: dict[str, str] = {
    # HMO ontology genre types
    "BiblicalText": "Q55017318",     # biblical literature
    "TalmudicText": "Q43290",        # Talmud
    "MishnaicText": "Q191825",       # Mishnah
    "HalachicText": "Q107427",       # Halakha
    "KabbalisticText": "Q123006",    # Kabbalah
    "PhilosophicalText": "Q5891",    # philosophy
    "PoeticText": "Q482",            # poetry
    "LiturgicalText": "Q172331",     # liturgy
    "MedicalText": "Q11190",         # medicine
    "CommentaryText": "Q1749541",    # commentary
    "GrammaticalText": "Q8091",      # grammar
    # MARC genre/form strings (from NLI catalog data)
    "Poetry": "Q482",                          # poetry
    "Piyyutim": "Q1377011",                    # piyyut (Hebrew liturgical poetry)
    "Illustrated works (Manuscript)": "Q48498",  # illuminated manuscript
    "Approbations (Rabbinical literature)": "Q3089066",  # haskama
    "Personal correspondence": "Q133492",       # letter
    "Mezuzot": "Q177038",                       # mezuzah
    "Legislation (Jewish law)": "Q107427",      # Halakha
    "Gittin": "Q752001",                        # get (Jewish divorce document)
    "Drama": "Q25372",                          # drama
    "Calendars": "Q12132",                      # calendar
    "Pinkasim": "Q7197095",                     # pinkas (communal record book)
    "Censored manuscripts": "Q49100005",         # banned book (censored)
    "Family records": "Q485228",                 # family register
    "Registers of births, etc.": "Q18562479",    # vital record
    "Autograph manuscripts": "Q9026959",         # autograph (handwritten by author)
    "Bibliographies": "Q1631107",                # bibliography
    "Tales": "Q49084",                           # short story / tale
    "Negotiable instruments": "Q3359388",        # negotiable instrument
    "Riddles": "Q189539",                        # riddle
    "Death registers": "Q3348095",               # register of deaths
    "Account books": "Q192907",                  # ledger / account book
    "Business records (Manuscript)": "Q804154",  # business record
    "Licenses": "Q79719",                        # license
    "Records (Documents)": "Q49848",             # document
    "Community records (Manuscript)": "Q7197095", # pinkas (communal record)
    "Literature (Miscellaneous, in manuscript)": "Q8242",  # literature
    "Biographies (Manuscript)": "Q36279",        # biography
    "Parodies": "Q12378",                        # parody
    "Ketubbot": "Q207128",                       # ketubah (marriage contract)
    "Forms (Jewish law)": "Q11028",              # legal document / form
    "Prayer books": "Q3412432",                  # prayer book / siddur
    "Sermons": "Q861911",                        # sermon
    "Commentaries": "Q1749541",                  # commentary
    "Responsa (Jewish law)": "Q2112559",         # responsa
    "Deeds": "Q40621",                           # deed (legal document)
    "Manuscripts, Hebrew": "Q87167",             # Hebrew manuscript
    "Wills": "Q179157",                          # will / testament
    "Contracts": "Q386724",                      # contract
    "Letters": "Q133492",                        # letter
}

# Well-known works that already exist on Wikidata
KNOWN_WORK_QIDS: dict[str, str] = {
    "Torah": "Q34990",
    "תורה": "Q34990",
    "Talmud": "Q43290",
    "תלמוד": "Q43290",
    "Mishnah": "Q191825",
    "משנה": "Q191825",
    "Zohar": "Q205388",
    "זוהר": "Q205388",
    "Shulchan Aruch": "Q822206",
    "שלחן ערוך": "Q822206",
    "Mishneh Torah": "Q201029",
    "משנה תורה": "Q201029",
}

# Bible books → Wikidata QIDs (for P921 main subject from canonical_references)
BIBLE_BOOK_TO_QID: dict[str, str] = {
    "Genesis": "Q9184",
    "Exodus": "Q9190",
    "Leviticus": "Q23767",
    "Numbers": "Q23775",
    "Deuteronomy": "Q23790",
    "Joshua": "Q131168",
    "Samuel": "Q178547",
    "Kings": "Q182060",
    "Isaiah": "Q131135",
    "Jeremiah": "Q131144",
    "Psalms": "Q41064",
    "Proverbs": "Q29539",
    "Job": "Q43304",
}

# Talmud Bavli tractates → Wikidata QIDs (for P921 main subject)
TALMUD_TRACTATE_TO_QID: dict[str, str] = {
    "ברכות": "Q598626",
    "שבת": "Q2276714",
    "פסחים": "Q2364178",
    "יומא": "Q2605561",
    "סוטה": "Q1544949",
    "קידושין": "Q2360571",
    "בבא קמא": "Q806189",
    "בבא בתרא": "Q806186",
    "סנהדרין": "Q605375",
    "עבודה זרה": "Q1135584",
    "כתובות": "Q2360474",
    "נדרים": "Q2604843",
    "נזיר": "Q2605296",
    "שבועות": "Q2606013",
}

# LCSH subject terms → Wikidata QIDs (for P921 main subject)
SUBJECT_TO_QID: dict[str, str] = {
    "Eretz Israel": "Q1207",           # Land of Israel
    "Jews": "Q7325",                   # Jews
    "Karaites": "Q173579",             # Karaites
    "Jewish law": "Q107427",           # Halakha
    "Cabala": "Q123006",               # Kabbalah
    "Religious disputations": "Q841408",  # religious debate
    "Astronomy": "Q333",               # astronomy
    "Responsa": "Q2112559",            # responsa
    "Philosophy": "Q5891",             # philosophy
    "Jewish philosophy": "Q131748",     # Jewish philosophy
    "Shehitah": "Q328079",             # shechita (kosher slaughter)
    "Christianity": "Q5043",           # Christianity
    "Jewish sermons, Hebrew": "Q861911",  # sermon
    "Jewish calendar": "Q217535",       # Hebrew calendar
    "Hebrew language": "Q9288",         # Hebrew language
    "Dreams": "Q36348",                # dream
    "Earthquakes": "Q7944",            # earthquake
    "Medicine": "Q11190",              # medicine
    "Astrology": "Q34362",             # astrology
    "Phlebotomy": "Q575696",           # phlebotomy
    "Berit milah": "Q204819",          # circumcision
    "Bar mitzvah": "Q333783",          # Bar Mitzvah
    "Gematria": "Q168529",             # gematria
    "Purim": "Q132834",               # Purim
    "Apostasy": "Q179723",             # apostasy
    "Liturgy": "Q172331",              # liturgy
    "Prayer": "Q40953",                # prayer
    "Bible": "Q1845",                  # Bible
    "Talmud": "Q43290",                # Talmud
    "Torah scrolls": "Q37602",         # Torah
}

# ── Language code → QID mapping ──────────────────────────────────────

LANG_TO_QID: dict[str, str] = {
    "heb": "Q9288",
    "ara": "Q13955",
    "arc": "Q28602",
    "jrb": "Q37733",
    "lad": "Q36196",
    "yid": "Q8641",
    "jpr": "Q33367",
    "lat": "Q397",
    "grc": "Q35497",
    "per": "Q9168",
    "ger": "Q188",
    "spa": "Q1321",
    "ita": "Q652",
    "fre": "Q150",
    "eng": "Q1860",
    "por": "Q5146",
    "tur": "Q256",
    "dut": "Q7411",      # Dutch (MARC language code)
    "gre": "Q36510",     # Modern Greek (MARC language code)
    "tat": "Q25285",     # Tatar (MARC language code)
}

# ── Material → QID mapping ───────────────────────────────────────────

MATERIAL_TO_QID: dict[str, str] = {
    "Parchment": "Q226697",
    "parchment": "Q226697",
    "Vellum": "Q378274",
    "vellum": "Q378274",
    "Paper": "Q11472",
    "paper": "Q11472",
    "Papyrus": "Q125576",
    "papyrus": "Q125576",
    # Hebrew forms
    "קלף": "Q226697",
    "נייר": "Q11472",
    "פפירוס": "Q125576",
}

# ── NER/MARC role → Wikidata PID mapping ─────────────────────────────

ROLE_TO_PID: dict[str, str] = {
    # NER roles (uppercase)
    "AUTHOR": P_AUTHOR,
    "TRANSCRIBER": P_TRANSCRIBED_BY,
    "OWNER": P_OWNED_BY,
    "CENSOR": P_OWNED_BY,  # No specific censor property; model as association
    "TRANSLATOR": P_AUTHOR,  # Qualified with role
    "COMMENTATOR": P_AUTHOR,  # Qualified with role
    # MARC roles (lowercase)
    "author": P_AUTHOR,
    "scribe": P_TRANSCRIBED_BY,
    "copyist": P_TRANSCRIBED_BY,
    "former owner": P_OWNED_BY,
    "בעלים קודמים": P_OWNED_BY,
    "illuminator": P_ILLUSTRATOR,
    "translator": P_AUTHOR,
    "commentator": P_AUTHOR,
    "editor": P_AUTHOR,
    "compiler": P_AUTHOR,
    "contributor": P_AUTHOR,
    # Hebrew role variants
    "סופר": P_TRANSCRIBED_BY,
    "מעתיק": P_TRANSCRIBED_BY,
    "בעלים": P_OWNED_BY,
    "בעל": P_OWNED_BY,
    "(ממנו)": P_OWNED_BY,
    "owner": P_OWNED_BY,
    "former_owner": P_OWNED_BY,
    "transcriber": P_TRANSCRIBED_BY,
}

# ── Date precision constants (Wikidata time model) ───────────────────

PRECISION_GIGAYEAR = 0
PRECISION_CENTURY = 7
PRECISION_DECADE = 8
PRECISION_YEAR = 9
PRECISION_MONTH = 10
PRECISION_DAY = 11


# ── Helper functions ─────────────────────────────────────────────────


def nli_j9u_id(control_number: str) -> str:
    """Extract or format an NLI J9U identifier from a control number.

    The J9U format expected by Wikidata P8189 is the raw NLI system number,
    typically matching pattern 98[0-9]{12}5171.

    Args:
        control_number: NLI system number (e.g., "990001188700205171").

    Returns:
        The control number as-is (it is already in J9U format for NLI records).
    """
    return control_number.strip()


def nli_catalog_url(control_number: str) -> str:
    """Build a URL to the NLI catalog record for a manuscript.

    Args:
        control_number: NLI system number.

    Returns:
        URL string pointing to the NLI catalog viewer.
    """
    cn = control_number.strip()
    return f"https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH{cn}"


def nli_reference(control_number: str) -> list[dict[str, str]]:
    """Build a Wikidata reference snak set for NLI catalog sourcing.

    Every statement added to Wikidata should include this reference.

    Args:
        control_number: NLI system number.

    Returns:
        List of reference snak dicts with P248, P854, P813.
    """
    today = datetime.now(tz=timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
    return [
        {"property": P_STATED_IN, "value": Q_KTIV, "type": "item"},
        {"property": P_REFERENCE_URL, "value": nli_catalog_url(control_number), "type": "url"},
        {"property": P_RETRIEVED, "value": today, "type": "time", "precision": PRECISION_DAY},
    ]


PRECISION_CENTURY = 7

# Hebrew ordinal → century number mapping
_HEBREW_ORDINAL_TO_INT: dict[str, int] = {
    'א': 1, 'ב': 2, 'ג': 3, 'ד': 4, 'ה': 5, 'ו': 6, 'ז': 7, 'ח': 8, 'ט': 9,
    'י': 10, "י\"א": 11, "י\"ב": 12, "י\"ג": 13, "י\"ד": 14, "י\"ה": 15,
    "ט\"ו": 15, "י\"ו": 16, "ט\"ז": 16, "י\"ז": 17, "י\"ח": 18, "י\"ט": 19,
    'כ': 20, "כ\"א": 21,
}


def _parse_hebrew_century(text: str) -> int | None:
    """Parse Hebrew century string like 'מאה ט"ז' → 16 (= 1500s)."""
    # Clean CSV double-quote escaping
    text = text.replace('""', '"')
    match = re.search(r'מאה\s+([א-ת]["\u05F4\']?[א-ת]?)', text)
    if not match:
        return None
    ordinal = match.group(1).strip()
    # Try direct lookup
    century = _HEBREW_ORDINAL_TO_INT.get(ordinal)
    if century:
        return century
    # Try with quote variations
    for variant in [ordinal, ordinal.replace("'", '"'), ordinal.replace('"', "'")]:
        century = _HEBREW_ORDINAL_TO_INT.get(variant)
        if century:
            return century
    return None


def date_to_wikidata(dates_dict: dict[str, object]) -> tuple[str, int] | None:
    """Convert a pipeline dates dict to a Wikidata time value and precision.

    Handles: structured years, English century strings, Hebrew century strings
    (מאה ט"ז = 16th century), and approximate dates.

    Returns:
        Tuple of (ISO time string, precision int) or None if no date available.
    """
    if not dates_dict:
        return None

    year = dates_dict.get("year")
    date_format = dates_dict.get("date_format", "")

    if year is not None:
        year_int = int(year)
        if date_format == "FullDate":
            return f"+{year_int:04d}-01-01T00:00:00Z", PRECISION_YEAR
        return f"+{year_int:04d}-00-00T00:00:00Z", PRECISION_YEAR

    # No structured year — try to parse from original string
    original = str(dates_dict.get("original_string", "")).replace('""', '"')
    if not original:
        return None

    # English century: "16th century"
    century_match = re.search(r"(\d{1,2})(?:th|st|nd|rd)\s*cent", original, re.IGNORECASE)
    if century_match:
        century = int(century_match.group(1))
        mid_year = (century - 1) * 100 + 50
        return f"+{mid_year:04d}-00-00T00:00:00Z", PRECISION_CENTURY

    # Hebrew century: "מאה ט"ז" (16th century)
    heb_century = _parse_hebrew_century(original)
    if heb_century:
        mid_year = (heb_century - 1) * 100 + 50
        return f"+{mid_year:04d}-00-00T00:00:00Z", PRECISION_CENTURY

    # Hebrew century range: "מאה י"ד-ט"ו" → use midpoint
    range_match = re.search(r'מאה\s+([א-ת]["\u05F4\']?[א-ת]?)\s*[-–]\s*([א-ת]["\u05F4\']?[א-ת]?)', original.replace('""', '"'))
    if range_match:
        c1 = _HEBREW_ORDINAL_TO_INT.get(range_match.group(1).strip())
        c2 = _HEBREW_ORDINAL_TO_INT.get(range_match.group(2).strip())
        if c1 and c2:
            mid_year = ((c1 - 1) * 100 + (c2 - 1) * 100) // 2 + 50
            return f"+{mid_year:04d}-00-00T00:00:00Z", PRECISION_CENTURY

    # Gregorian year in string: extract 4-digit year
    year_match = re.search(r'\b(\d{4})\b', original)
    if year_match:
        return f"+{int(year_match.group(1)):04d}-00-00T00:00:00Z", PRECISION_YEAR

    return None


def extract_viaf_id(viaf_uri: str) -> str | None:
    """Extract the numeric VIAF ID from a VIAF URI.

    Args:
        viaf_uri: Full VIAF URI (e.g., "https://viaf.org/viaf/97223111").

    Returns:
        The numeric ID string, or None if parsing fails.
    """
    if not viaf_uri:
        return None
    match = re.search(r"viaf/(\d+)", viaf_uri)
    return match.group(1) if match else None


def extract_wikidata_qid(wikidata_uri: str) -> str | None:
    """Extract a QID from a Wikidata entity URI.

    Args:
        wikidata_uri: Full Wikidata URI (e.g., "https://www.wikidata.org/entity/Q1218").

    Returns:
        The QID string (e.g., "Q1218"), or None if parsing fails.
    """
    if not wikidata_uri:
        return None
    match = re.search(r"(Q\d+)", wikidata_uri)
    return match.group(1) if match else None
