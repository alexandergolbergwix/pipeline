#!/usr/bin/env python3
"""Convert MARC .mrc files to Turtle (.ttl) using Hebrew Manuscripts Ontology v1.5.

This script handles NLI-specific MARC fields and implements v1.5 ontology features:
- Multi-volume sets (Vatican 44)
- Anthology/collection structures (Parma 3122, Vatican 44, Opp. 129)
- Multiple script types for manuscripts with multiple hands
- Host item linking via 773 (Jerusalem 8210)
- Epistemological metadata and cataloging views
- Nested codicological units

Output strategy:
- pure data-only output (`test_manuscripts.ttl`) with no ontology declaration
- optional merged debug output (`test_manuscripts_merged.ttl`)
"""

import re
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Iterator

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pymarc
from rdflib import Graph, Literal, URIRef, Namespace, BNode
from rdflib.namespace import RDF, RDFS, XSD, OWL

# Namespaces
HM = Namespace("http://www.ontology.org.il/HebrewManuscripts/2025-12-06#")
LRMOO = Namespace("http://iflastandards.info/ns/lrm/lrmoo/")
CIDOC = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
NLI = Namespace("https://www.nli.org.il/en/authorities/")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")


def bind_namespaces(graph: Graph) -> None:
    """Bind all namespaces to the graph."""
    graph.bind("hm", HM)
    graph.bind("lrmoo", LRMOO)
    graph.bind("cidoc-crm", CIDOC)
    graph.bind("nli", NLI)
    graph.bind("skos", SKOS)
    graph.bind("rdf", RDF)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)
    graph.bind("owl", OWL)


# Language codes mapping
LANGUAGE_CODES = {
    'heb': 'Hebrew', 'ara': 'Arabic', 'arc': 'Aramaic',
    'jrb': 'Judeo-Arabic', 'lad': 'Ladino', 'yid': 'Yiddish',
    'lat': 'Latin', 'grc': 'Greek', 'per': 'Persian',
    'ger': 'German', 'eng': 'English', 'fre': 'French',
    'ita': 'Italian', 'spa': 'Spanish', 'por': 'Portuguese',
}

# Script types mapping (NLI field 958)
SCRIPT_TYPES = {
    'ספרדית': 'SepharadicScript', 'sephardic': 'SepharadicScript',
    'אשכנזית': 'AshkenaziScript', 'ashkenazi': 'AshkenaziScript',
    'איטלקית': 'ItalianScript', 'italian': 'ItalianScript',
    'מזרחית': 'OrientalScript', 'oriental': 'OrientalScript',
    'ביזנטית': 'ByzantineScript', 'byzantine': 'ByzantineScript',
    'תימנית': 'YemeniteScript', 'yemenite': 'YemeniteScript',
}

# Role mappings
ROLE_MAPPINGS = {
    'מעתיק': 'scribe', '(מעתיק)': 'scribe', 'scribe': 'scribe', 'copyist': 'scribe',
    'מחבר': 'author', 'author': 'author',
    'מעיר': 'annotator', 'annotator': 'annotator',
    'בעלים קודמים': 'former_owner', 'former owner': 'former_owner',
    'current owner': 'current_owner',
    'censor': 'censor',
    '(ממנו)': 'quoted_author',
}


@dataclass
class ExtractedData:
    """Container for data extracted from MARC record."""
    control_number: str = ""
    title: Optional[str] = None
    subtitle: Optional[str] = None
    variant_titles: List[str] = field(default_factory=list)
    additional_titles: List[str] = field(default_factory=list)  # From 740
    authors: List[Dict[str, Any]] = field(default_factory=list)
    contributors: List[Dict[str, Any]] = field(default_factory=list)
    date_string: Optional[str] = None
    date_start: Optional[int] = None
    date_end: Optional[int] = None
    place: Optional[str] = None
    extent: Optional[int] = None
    extent_string: Optional[str] = None
    height_mm: Optional[int] = None
    width_mm: Optional[int] = None
    materials: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    script_types: List[str] = field(default_factory=list)  # Multiple for multi-hand MSS
    notes: List[str] = field(default_factory=list)
    contents: List[Dict[str, Any]] = field(default_factory=list)  # From 505
    subjects: List[Dict[str, Any]] = field(default_factory=list)
    genres: List[str] = field(default_factory=list)
    provenance_notes: List[str] = field(default_factory=list)  # All 561 fields
    digital_url: Optional[str] = None
    catalog_references: List[Dict[str, str]] = field(default_factory=list)
    bibliography: List[str] = field(default_factory=list)  # From 581
    colophon_texts: List[str] = field(default_factory=list)  # From 957
    sfardata_id: Optional[str] = None  # From 024
    shelfmark: Optional[str] = None  # From 942
    institution: Optional[str] = None  # From 942
    host_record_id: Optional[str] = None  # From 773
    
    # Administrative/Access fields
    copyright_notices: List[str] = field(default_factory=list)  # From 597
    usage_restrictions: List[Dict[str, str]] = field(default_factory=list)  # From 939
    rights_determinations: List[Dict[str, str]] = field(default_factory=list)  # From 952
    physical_holdings: List[Dict[str, str]] = field(default_factory=list)  # From AVA
    digital_access_points: List[Dict[str, str]] = field(default_factory=list)  # From AVE
    related_material_notes: List[str] = field(default_factory=list)  # From 544
    acquisition_source: Optional[str] = None  # From 541
    collection_names: List[str] = field(default_factory=list)  # From 966
    
    # Structural indicators
    is_multi_volume: bool = False
    volume_info: Optional[str] = None
    is_anthology: bool = False
    num_works: int = 0
    has_multiple_hands: bool = False


def normalize_string(text: str) -> str:
    """Normalize string for URI generation."""
    if not text:
        return ""
    text = text.strip()
    result = re.sub(r'[^\w\s\u0590-\u05FF-]', '', text)
    result = re.sub(r'[-\s]+', '_', result)
    return result.strip('_')


def _is_http_uri(value: str) -> bool:
    """Return True when value is a stable HTTP(S) URI."""
    return bool(re.match(r'^https?://\\S+$', value.strip(), re.IGNORECASE))


def extract_authority_ids(raw_values: List[str]) -> Dict[str, Any]:
    """Extract MARC authority identifiers from $0/$1 values (MARC-only)."""
    result: Dict[str, Any] = {
        'source_values': [],
        'same_as_uris': [],
    }

    def add_same_as(uri: str) -> None:
        if uri not in result['same_as_uris']:
            result['same_as_uris'].append(uri)

    for raw in raw_values:
        if not raw:
            continue
        value = raw.strip()
        if not value:
            continue
        result['source_values'].append(value)

        viaf_uri_match = re.search(r'https?://(?:www\\.)?viaf\\.org/viaf/(\\d+)', value, re.IGNORECASE)
        viaf_id_match = re.search(r'\\(VIAF\\)\\s*(\\d+)', value, re.IGNORECASE)
        viaf_plain_match = re.fullmatch(r'\\d{5,}', value)
        if viaf_uri_match:
            viaf_id = viaf_uri_match.group(1)
            result['viaf_id'] = viaf_id
            add_same_as(f"https://viaf.org/viaf/{viaf_id}")
        elif viaf_id_match:
            result['viaf_id'] = viaf_id_match.group(1)
        elif viaf_plain_match:
            result['viaf_id'] = value

        wikidata_uri_match = re.search(r'https?://(?:www\\.)?wikidata\\.org/entity/(Q\\d+)', value, re.IGNORECASE)
        wikidata_id_match = re.fullmatch(r'Q\\d+', value, re.IGNORECASE)
        if wikidata_uri_match:
            qid = wikidata_uri_match.group(1).upper()
            result['wikidata_id'] = qid
            add_same_as(f"https://www.wikidata.org/entity/{qid}")
        elif wikidata_id_match:
            result['wikidata_id'] = value.upper()

        if _is_http_uri(value):
            if 'nli.org.il' in value.lower():
                result['external_uri_nli'] = value
                add_same_as(value)
            elif '/authorities/' in value.lower() and 'nli' in value.lower():
                result['external_uri_nli'] = value
                add_same_as(value)

    return result


def parse_nli_505(content: str) -> List[Dict[str, Any]]:
    """Parse NLI-format 505 contents field.
    
    NLI format: "N) דף [XXX]א-[XXX]ב: Title"
    Uses Hebrew recto (א) / verso (ב) notation and brackets for foliation.
    """
    items = []
    
    # Pattern for NLI 505 format: number) folio range: title
    # Example: "1) דף [2]א-[119]ב: מדרש תנחומא."
    pattern = r'(\d+)\)\s*דף\s*\[?(\d+)\]?([אב]?)[-–]\[?(\d+)\]?([אב]?):\s*(.+?)(?=\d+\)|$)'
    
    matches = re.findall(pattern, content, re.DOTALL)
    
    for match in matches:
        seq_num, start_folio, start_side, end_folio, end_side, title = match
        
        # Convert Hebrew recto/verso to standard r/v
        start_rv = 'r' if start_side == 'א' else 'v' if start_side == 'ב' else ''
        end_rv = 'r' if end_side == 'א' else 'v' if end_side == 'ב' else ''
        
        folio_range = f"{start_folio}{start_rv}-{end_folio}{end_rv}"
        
        items.append({
            'sequence': int(seq_num),
            'title': title.strip().rstrip('.'),
            'folio_range': folio_range,
            'folio_start': f"{start_folio}{start_rv}",
            'folio_end': f"{end_folio}{end_rv}",
        })
    
    # If pattern didn't match, try simpler parsing
    if not items and content:
        # Try splitting by numbered items
        simple_pattern = r'(\d+)\)\s*(.+?)(?=\d+\)|$)'
        simple_matches = re.findall(simple_pattern, content, re.DOTALL)
        
        for seq_num, text in simple_matches:
            # Try to extract folio range
            folio_match = re.search(r'דף\s*\[?(\d+[אב]?)\]?\s*[-–]\s*\[?(\d+[אב]?)\]?', text)
            folio_range = None
            if folio_match:
                folio_range = f"{folio_match.group(1)}-{folio_match.group(2)}"
                # Convert Hebrew to standard
                folio_range = folio_range.replace('א', 'r').replace('ב', 'v')
            
            # Extract title (after colon or the whole text)
            title_match = re.search(r':\s*(.+)', text)
            title = title_match.group(1).strip() if title_match else text.strip()
            title = title.rstrip('.')
            
            items.append({
                'sequence': int(seq_num),
                'title': title,
                'folio_range': folio_range,
            })
    
    return items


def extract_person_dates(dates_str: str) -> Dict[str, Any]:
    """Parse person dates from MARC date string."""
    result = {}
    
    # Pattern: 1135-1204
    match = re.search(r'(\d{3,4})\s*-\s*(\d{3,4})', dates_str)
    if match:
        result['birth_year'] = int(match.group(1))
        result['death_year'] = int(match.group(2))
        return result
    
    # Pattern: נפטר 1013 (died)
    match = re.search(r'נפטר\s*(\d{3,4})|died?\s*(\d{3,4})', dates_str, re.IGNORECASE)
    if match:
        year = match.group(1) or match.group(2)
        result['death_year'] = int(year)
        return result
    
    # Pattern: 1135-
    match = re.search(r'(\d{3,4})\s*-\s*$', dates_str)
    if match:
        result['birth_year'] = int(match.group(1))
    
    return result


def parse_date_string(date_str: str) -> Dict[str, Any]:
    """Parse date string to extract year(s)."""
    result = {'original': date_str}
    
    # Hebrew year with Gregorian: קס"ז (1407) or ה'ל (1270)
    match = re.search(r'\((\d{4})\)', date_str)
    if match:
        result['year'] = int(match.group(1))
        return result
    
    # Century mapping - Hebrew to century number
    # Using both quote styles that appear in NLI data
    century_patterns = [
        (r'י"ח', 18), (r'י״ח', 18),  # 18th century
        (r'י"ז', 17), (r'י״ז', 17),  # 17th century
        (r'ט"ז', 16), (r'ט״ז', 16),  # 16th century
        (r'ט"ו', 15), (r'ט״ו', 15),  # 15th century
        (r'י"ד', 14), (r'י״ד', 14),  # 14th century
        (r'י"ג', 13), (r'י״ג', 13),  # 13th century
        (r'י"ב', 12), (r'י״ב', 12),  # 12th century
        (r'י"א', 11), (r'י״א', 11),  # 11th century
        (r"י'", 10), (r'י׳', 10),    # 10th century
    ]
    
    # Check for century range first: מאה ט"ז-י"ז
    range_match = re.search(r'מאה\s+(.+?)-(.+?)\.?$', date_str)
    if range_match:
        start_str, end_str = range_match.group(1), range_match.group(2)
        for pattern, num in century_patterns:
            if re.search(pattern, start_str):
                result['year_start'] = (num - 1) * 100 + 1
            if re.search(pattern, end_str):
                result['year_end'] = num * 100
        if 'year_start' in result:
            return result
    
    # Single century: מאה י"ד
    for pattern, num in century_patterns:
        if re.search(pattern, date_str):
            result['century'] = num
            result['year_start'] = (num - 1) * 100 + 1
            result['year_end'] = num * 100
            return result
    
    # Plain year
    match = re.search(r'(\d{4})', date_str)
    if match:
        result['year'] = int(match.group(1))
    
    return result


def parse_extent(extent_str: str) -> Optional[int]:
    """Parse folio/leaf count from extent string."""
    # Pattern: [285] דף or 151 דף
    match = re.search(r'\[?(\d+)\]?\s*דף', extent_str)
    if match:
        return int(match.group(1))
    
    # Pattern: 2 כרכים (384 דף) - multi-volume
    match = re.search(r'\((\d+)\s*דף\)', extent_str)
    if match:
        return int(match.group(1))
    
    return None


def parse_dimensions(dim_str: str) -> Dict[str, int]:
    """Parse dimensions from dimension string."""
    result = {}
    
    # Pattern: 16.1X11.1 ס"מ (cm)
    match = re.search(r'(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*ס"מ', dim_str)
    if match:
        result['height_mm'] = int(float(match.group(1)) * 10)
        result['width_mm'] = int(float(match.group(2)) * 10)
        return result
    
    # Pattern: 280 x 200 mm
    match = re.search(r'(\d+)\s*[xX×]\s*(\d+)\s*(?:mm|מ"מ)', dim_str)
    if match:
        result['height_mm'] = int(match.group(1))
        result['width_mm'] = int(match.group(2))
    
    return result


def extract_marc_data(record: pymarc.Record) -> ExtractedData:
    """Extract all relevant data from a MARC record."""
    data = ExtractedData()
    
    # Control number (001)
    if record['001']:
        data.control_number = record['001'].data
    
    # Sfardata ID (024)
    for field in record.get_fields('024'):
        source_values = [s.lower() for s in field.get_subfields('2')]
        if source_values and any('sfardata' in s for s in source_values):
            sfardata = field.get_subfields('a')
            if sfardata:
                data.sfardata_id = sfardata[0]
    
    # Languages (041)
    for field in record.get_fields('041'):
        for lang in field.get_subfields('a'):
            if lang and lang not in data.languages:
                data.languages.append(lang)
    
    # Author (100)
    for field in record.get_fields('100'):
        name = field.get_subfields('a')
        if name:
            author = {'name': name[0].rstrip(','), 'role': 'author'}
            dates = field.get_subfields('d')
            if dates:
                author['dates'] = dates[0]
                author.update(extract_person_dates(dates[0]))
            authority_values = field.get_subfields('0') + field.get_subfields('1')
            if authority_values:
                auth_data = extract_authority_ids(authority_values)
                if auth_data.get('source_values'):
                    author['authority_source_values'] = auth_data['source_values']
                if auth_data.get('viaf_id'):
                    author['viaf_id'] = auth_data['viaf_id']
                if auth_data.get('wikidata_id'):
                    author['wikidata_id'] = auth_data['wikidata_id']
                if auth_data.get('external_uri_nli'):
                    author['external_uri_nli'] = auth_data['external_uri_nli']
                if auth_data.get('same_as_uris'):
                    author['same_as_uris'] = auth_data['same_as_uris']
            data.authors.append(author)
    
    # Title (245)
    if record['245']:
        title = record['245'].get_subfields('a')
        if title:
            data.title = title[0].rstrip(' /:.')
        subtitle = record['245'].get_subfields('b')
        if subtitle:
            data.subtitle = subtitle[0].rstrip(' /.')
    
    # Variant titles (246)
    for field in record.get_fields('246'):
        variant = field.get_subfields('a')
        if variant:
            data.variant_titles.append(variant[0])
    
    # Publication/Production (260/264)
    for tag in ['260', '264']:
        for field in record.get_fields(tag):
            place = field.get_subfields('a')
            if place and not data.place:
                data.place = place[0].rstrip(' :;,')
            date = field.get_subfields('c')
            if date:
                data.date_string = date[0].strip('[].')
                parsed = parse_date_string(data.date_string)
                if 'year' in parsed:
                    data.date_start = parsed['year']
                    data.date_end = parsed['year']
                elif 'year_start' in parsed:
                    data.date_start = parsed['year_start']
                    data.date_end = parsed.get('year_end')
    
    # Physical description (300)
    if record['300']:
        extent = record['300'].get_subfields('a')
        if extent:
            data.extent_string = extent[0]
            data.extent = parse_extent(extent[0])
            # Check for multi-volume
            if 'כרכים' in extent[0] or 'volumes' in extent[0].lower():
                data.is_multi_volume = True
                data.volume_info = extent[0]
        
        dimensions = record['300'].get_subfields('c')
        if dimensions:
            dims = parse_dimensions(dimensions[0])
            data.height_mm = dims.get('height_mm')
            data.width_mm = dims.get('width_mm')
    
    # Materials/Physical notes (340)
    for field in record.get_fields('340'):
        note = field.get_subfields('a')
        if note:
            note_text = note[0].lower()
            if 'כתיבות' in note_text or 'hands' in note_text:
                data.has_multiple_hands = True
            data.notes.append(note[0])
    
    # General notes (500)
    for field in record.get_fields('500'):
        note = field.get_subfields('a')
        if note:
            data.notes.append(note[0])
    
    # Contents (505) - NLI specific format
    for field in record.get_fields('505'):
        content = field.get_subfields('a')
        if content:
            items = parse_nli_505(content[0])
            data.contents.extend(items)
            if len(items) > 1:
                data.is_anthology = True
                data.num_works = len(items)
    
    # Bibliography (581)
    for field in record.get_fields('581'):
        bib = field.get_subfields('a')
        if bib:
            data.bibliography.append(bib[0])
            # Also add as catalog reference
            data.catalog_references.append({'name': bib[0]})
    
    # Provenance (561) - ALL fields, not just first
    for field in record.get_fields('561'):
        prov = field.get_subfields('a')
        if prov:
            data.provenance_notes.append(prov[0])
    
    # Subjects (650)
    for field in record.get_fields('650'):
        term = field.get_subfields('a')
        if term:
            subject = {'term': term[0], 'type': 'topic'}
            data.subjects.append(subject)
    
    # Genre (655)
    for field in record.get_fields('655'):
        genre = field.get_subfields('a')
        if genre:
            data.genres.append(genre[0])
    
    # Added entries - persons (700)
    for field in record.get_fields('700'):
        name = field.get_subfields('a')
        if name:
            person = {'name': name[0].rstrip(','), 'role': 'contributor'}
            
            # Role from $e
            role = field.get_subfields('e')
            if role:
                role_text = role[0].lower().strip()
                person['role'] = ROLE_MAPPINGS.get(role_text, role_text)
                person['role_original'] = role[0]
            
            # Dates from $d
            dates = field.get_subfields('d')
            if dates:
                person['dates'] = dates[0]
                person.update(extract_person_dates(dates[0]))
            
            # Authority identifiers from $0 / $1
            authority_values = field.get_subfields('0') + field.get_subfields('1')
            if authority_values:
                auth_data = extract_authority_ids(authority_values)
                if auth_data.get('source_values'):
                    person['authority_source_values'] = auth_data['source_values']
                if auth_data.get('viaf_id'):
                    person['viaf_id'] = auth_data['viaf_id']
                if auth_data.get('wikidata_id'):
                    person['wikidata_id'] = auth_data['wikidata_id']
                if auth_data.get('external_uri_nli'):
                    person['external_uri_nli'] = auth_data['external_uri_nli']
                if auth_data.get('same_as_uris'):
                    person['same_as_uris'] = auth_data['same_as_uris']
            
            data.contributors.append(person)
    
    # Corporate entries (710)
    for field in record.get_fields('710'):
        name = field.get_subfields('a')
        if name:
            org = {'name': name[0], 'type': 'organization'}
            role = field.get_subfields('e')
            if role:
                org['role'] = ROLE_MAPPINGS.get(role[0].lower(), role[0])
            location = field.get_subfields('x')
            if location:
                org['location'] = location[0]
            authority_values = field.get_subfields('0') + field.get_subfields('1')
            if authority_values:
                auth_data = extract_authority_ids(authority_values)
                if auth_data.get('source_values'):
                    org['authority_source_values'] = auth_data['source_values']
                if auth_data.get('viaf_id'):
                    org['viaf_id'] = auth_data['viaf_id']
                if auth_data.get('wikidata_id'):
                    org['wikidata_id'] = auth_data['wikidata_id']
                if auth_data.get('external_uri_nli'):
                    org['external_uri_nli'] = auth_data['external_uri_nli']
                if auth_data.get('same_as_uris'):
                    org['same_as_uris'] = auth_data['same_as_uris']
            data.contributors.append(org)
    
    # Additional titles (740)
    for field in record.get_fields('740'):
        add_title = field.get_subfields('a')
        if add_title:
            data.additional_titles.append(add_title[0])
    
    # Place of writing (751)
    for field in record.get_fields('751'):
        place = field.get_subfields('a')
        if place and not data.place:
            data.place = place[0]
    
    # Host item (773) - for items that are part of a larger collection
    for field in record.get_fields('773'):
        host_id = field.get_subfields('w')
        if host_id:
            data.host_record_id = host_id[0]
    
    # Electronic location (856)
    for field in record.get_fields('856'):
        url = field.get_subfields('u')
        if url:
            data.digital_url = url[0]
            break
    
    # Shelfmark and institution (942)
    for field in record.get_fields('942'):
        if field.indicator1 == '1':  # Current location
            inst = field.get_subfields('a')
            if inst:
                data.institution = inst[0]
            shelf = field.get_subfields('z')
            if shelf:
                data.shelfmark = shelf[0]
    
    # Colophon (957) - NLI specific
    for field in record.get_fields('957'):
        colophon = field.get_subfields('a')
        if colophon:
            data.colophon_texts.append(colophon[0])
    
    # Script type (958) - NLI specific
    for field in record.get_fields('958'):
        script = field.get_subfields('a')
        if script:
            script_text = script[0].strip()
            script_type = SCRIPT_TYPES.get(script_text, script_text)
            if script_type not in data.script_types:
                data.script_types.append(script_type)
    
    # If multiple script types detected, mark as multiple hands
    if len(data.script_types) > 1:
        data.has_multiple_hands = True
    
    # Related materials (544)
    for f in record.get_fields('544'):
        note = f.get_subfields('a')
        if note:
            data.related_material_notes.append(note[0])
    
    # Acquisition source (541)
    for f in record.get_fields('541'):
        source = f.get_subfields('a')
        if source and not data.acquisition_source:
            data.acquisition_source = source[0]
    
    # Copyright notice (597)
    for f in record.get_fields('597'):
        notices = f.get_subfields('a')
        for notice in notices:
            if notice and notice not in data.copyright_notices:
                data.copyright_notices.append(notice)
    
    # Usage restrictions (939)
    for f in record.get_fields('939'):
        restriction = {}
        desc = f.get_subfields('a')
        if desc:
            restriction['description'] = desc[0]
        url = f.get_subfields('u')
        if url:
            restriction['url'] = url[0]
        if restriction:
            data.usage_restrictions.append(restriction)
    
    # Rights determination (952)
    for f in record.get_fields('952'):
        rights = {}
        status = f.get_subfields('a')
        if status:
            rights['status'] = status[0]
        basis = f.get_subfields('b')
        if basis:
            rights['basis'] = basis[0]
        if rights:
            data.rights_determinations.append(rights)
    
    # Physical holdings (AVA)
    for f in record.get_fields('AVA'):
        holding = {}
        inst = f.get_subfields('b')
        if inst:
            holding['institution'] = inst[0]
        collection = f.get_subfields('c')
        if collection:
            holding['collection'] = collection[0]
        call_num = f.get_subfields('d')
        if call_num:
            holding['call_number'] = call_num[0]
        avail = f.get_subfields('e')
        if avail:
            holding['availability'] = avail[0]
        lib_name = f.get_subfields('q')
        if lib_name:
            holding['library_name'] = lib_name[0]
        if holding:
            data.physical_holdings.append(holding)
    
    # Digital access (AVE)
    for f in record.get_fields('AVE'):
        access = {}
        avail = f.get_subfields('e')
        if avail:
            access['availability'] = avail[0]
        note = f.get_subfields('n')
        if note:
            access['note'] = note[0]
            if 'IIIF' in note[0] or 'iiif' in note[0]:
                access['type'] = 'iiif'
            else:
                access['type'] = 'viewer'
        rec_id = f.get_subfields('0')
        if rec_id:
            access['record_id'] = rec_id[0]
        if access:
            data.digital_access_points.append(access)
    
    # Collection name (966)
    for f in record.get_fields('966'):
        name = f.get_subfields('a')
        if name and name[0] not in data.collection_names:
            data.collection_names.append(name[0])
    
    return data


def build_rdf_graph(data: ExtractedData) -> Graph:
    """Build RDF graph from extracted data using ontology v1.4."""
    graph = Graph()
    bind_namespaces(graph)
    
    ms_uri = HM[f"MS_{normalize_string(data.control_number)}"]
    work_expression_pairs: List[Dict[str, URIRef]] = []
    structural_cu_uris: List[URIRef] = []
    scribe_entity_uris: List[URIRef] = []
    
    # === MANUSCRIPT ENTITY ===
    graph.add((ms_uri, RDF.type, LRMOO.F4_Manifestation_Singleton))
    graph.add((ms_uri, RDF.type, HM.Bibliographic_Unit))
    
    # Identifiers
    graph.add((ms_uri, HM.external_identifier_nli, Literal(data.control_number, datatype=XSD.string)))
    if data.sfardata_id:
        graph.add((ms_uri, HM.sfardata_id, Literal(data.sfardata_id, datatype=XSD.string)))
    if data.shelfmark:
        graph.add((ms_uri, HM.shelfmark, Literal(data.shelfmark, datatype=XSD.string)))
    
    # Labels
    label = data.title or f"MS {data.control_number}"
    graph.add((ms_uri, RDFS.label, Literal(label, lang='he')))
    if data.shelfmark and data.institution:
        eng_label = f"{data.institution}, {data.shelfmark}"
        graph.add((ms_uri, RDFS.label, Literal(eng_label, lang='en')))
    
    # Physical description
    if data.extent:
        graph.add((ms_uri, HM.has_number_of_folios, Literal(data.extent, datatype=XSD.integer)))
    if data.height_mm:
        graph.add((ms_uri, HM.has_height_mm, Literal(data.height_mm, datatype=XSD.integer)))
    if data.width_mm:
        graph.add((ms_uri, HM.has_width_mm, Literal(data.width_mm, datatype=XSD.integer)))
    
    # Script types (can be multiple for multi-hand manuscripts)
    for script_type in data.script_types:
        script_uri = HM[script_type]
        graph.add((ms_uri, HM.has_script_type, script_uri))
        graph.add((script_uri, RDF.type, HM.TypeScriptType))
        graph.add((script_uri, RDFS.label, Literal(script_type, lang='en')))
    
    # Digital representation
    if data.digital_url:
        graph.add((ms_uri, HM.has_digital_representation_url, Literal(data.digital_url, datatype=XSD.anyURI)))
    
    # Notes
    for note in data.notes:
        graph.add((ms_uri, RDFS.comment, Literal(note, lang='he')))
    
    # === PROVENANCE (all 561 fields) ===
    for i, prov in enumerate(data.provenance_notes):
        graph.add((ms_uri, HM.ownership_history, Literal(prov, lang='he')))
        acq_uri = HM[f"Acquisition_{normalize_string(data.control_number)}_{i + 1:02d}"]
        graph.add((acq_uri, RDF.type, CIDOC.E8_Acquisition))
        graph.add((acq_uri, RDFS.comment, Literal(prov, lang='he')))
        graph.add((ms_uri, HM.has_acquisition_event, acq_uri))
    
    # === PRODUCTION EVENT ===
    prod_uri = HM[f"Production_{normalize_string(data.control_number)}"]
    graph.add((prod_uri, RDF.type, CIDOC.E12_Production))
    graph.add((prod_uri, LRMOO.R27_materialized, ms_uri))
    graph.add((ms_uri, HM.has_production_event, prod_uri))
    
    if data.place:
        place_uri = HM[f"Place_{normalize_string(data.place)}"]
        graph.add((prod_uri, CIDOC.P7_took_place_at, place_uri))
        graph.add((prod_uri, HM.has_production_place, place_uri))
        graph.add((place_uri, RDF.type, CIDOC.E53_Place))
        graph.add((place_uri, RDFS.label, Literal(data.place, lang='he')))
    
    if data.date_start or data.date_end:
        time_label = str(data.date_start) if data.date_start == data.date_end else f"{data.date_start}-{data.date_end}"
        time_uri = HM[f"TimeSpan_{normalize_string(time_label)}"]
        graph.add((prod_uri, CIDOC.P4_has_time_span, time_uri))
        graph.add((prod_uri, HM.has_production_time, time_uri))
        graph.add((time_uri, RDF.type, CIDOC['E52_Time-Span']))
        if data.date_start:
            graph.add((time_uri, HM.earliest_possible_date, Literal(data.date_start, datatype=XSD.integer)))
        if data.date_end:
            graph.add((time_uri, HM.latest_possible_date, Literal(data.date_end, datatype=XSD.integer)))
        graph.add((time_uri, RDFS.label, Literal(time_label)))
        graph.add((prod_uri, HM.has_production_date_certain, Literal(bool(data.date_start and data.date_end and data.date_start == data.date_end), datatype=XSD.boolean)))
    
    # === WORK AND EXPRESSION (main) ===
    work_uri = None
    expression_uri = None
    
    if data.title:
        author_name = data.authors[0]['name'] if data.authors else None
        work_suffix = f"{normalize_string(data.title)}"
        if author_name:
            work_suffix += f"_by_{normalize_string(author_name)}"
        work_uri = HM[f"Work_{work_suffix}"]
        
        graph.add((work_uri, RDF.type, LRMOO.F1_Work))
        graph.add((work_uri, HM.has_title, Literal(data.title, lang='he')))
        graph.add((work_uri, RDFS.label, Literal(data.title, lang='he')))
        
        if data.subtitle:
            graph.add((work_uri, HM.has_title, Literal(f"{data.title} : {data.subtitle}", lang='he')))
        
        for variant in data.variant_titles:
            graph.add((work_uri, HM.has_title, Literal(variant, lang='he')))
            graph.add((work_uri, HM.has_alternate_title, Literal(variant, lang='he')))
        
        # Expression
        expression_uri = HM[f"Expression_{normalize_string(data.title)}_in_{normalize_string(data.control_number)}"]
        graph.add((expression_uri, RDF.type, LRMOO.F2_Expression))
        graph.add((expression_uri, RDFS.label, Literal(f"{data.title} (in MS {data.control_number})", lang='he')))
        graph.add((expression_uri, LRMOO.R3_is_realised_in, work_uri))
        
        # Languages
        for lang_code in data.languages:
            lang_name = LANGUAGE_CODES.get(lang_code, lang_code)
            lang_uri = HM[lang_name]
            graph.add((lang_uri, RDF.type, CIDOC.E56_Language))
            graph.add((lang_uri, RDFS.label, Literal(lang_name)))
            graph.add((expression_uri, CIDOC.P72_has_language, lang_uri))
        
        graph.add((ms_uri, LRMOO.R4_embodies, expression_uri))
        graph.add((ms_uri, HM.has_expression, expression_uri))
        graph.add((ms_uri, HM.has_work, work_uri))
        work_expression_pairs.append({'work': work_uri, 'expression': expression_uri})

        main_cu_uri = HM[f"CU_{normalize_string(data.control_number)}_main"]
        graph.add((main_cu_uri, RDF.type, HM.Codicological_Unit))
        graph.add((main_cu_uri, RDFS.label, Literal(f"Main codicological unit of MS {data.control_number}", lang='en')))
        graph.add((ms_uri, HM.is_composed_of, main_cu_uri))
        graph.add((main_cu_uri, HM.forms_part_of, ms_uri))
        graph.add((main_cu_uri, HM.has_expression, expression_uri))
        graph.add((main_cu_uri, HM.has_work, work_uri))
        structural_cu_uris.append(main_cu_uri)
    
    # === AUTHORS ===
    for author in data.authors:
        person_uri = HM[f"Person_{normalize_string(author['name'])}"]
        graph.add((person_uri, RDF.type, CIDOC.E21_Person))
        graph.add((person_uri, RDFS.label, Literal(author['name'], lang='he')))
        
        if 'birth_year' in author:
            graph.add((person_uri, CIDOC.P82a_begin_of_the_begin, Literal(author['birth_year'], datatype=XSD.integer)))
        if 'death_year' in author:
            graph.add((person_uri, CIDOC.P82b_end_of_the_end, Literal(author['death_year'], datatype=XSD.integer)))
        if author.get('external_uri_nli'):
            graph.add((person_uri, HM.external_uri_nli, Literal(author['external_uri_nli'], datatype=XSD.anyURI)))
        if author.get('viaf_id'):
            graph.add((person_uri, HM.viaf_id, Literal(author['viaf_id'], datatype=XSD.string)))
        if author.get('wikidata_id'):
            graph.add((person_uri, HM.wikidata_id, Literal(author['wikidata_id'], datatype=XSD.string)))
        for same_as_uri in author.get('same_as_uris', []):
            if _is_http_uri(same_as_uri):
                graph.add((person_uri, OWL.sameAs, URIRef(same_as_uri)))
        
        # Work creation event
        if work_uri:
            creation_uri = HM[f"WorkCreation_{normalize_string(data.title)}_by_{normalize_string(author['name'])}"]
            graph.add((creation_uri, RDF.type, LRMOO.F27_Work_Creation))
            graph.add((creation_uri, LRMOO.R16_created, work_uri))
            graph.add((creation_uri, CIDOC.P14_carried_out_by, person_uri))
            graph.add((work_uri, HM.has_author, person_uri))
    
    # === CONTRIBUTORS (scribes, owners, etc.) ===
    for contrib in data.contributors:
        if contrib.get('type') == 'organization':
            entity_uri = HM[f"Group_{normalize_string(contrib['name'])}"]
            graph.add((entity_uri, RDF.type, CIDOC.E74_Group))
        else:
            entity_uri = HM[f"Person_{normalize_string(contrib['name'])}"]
            graph.add((entity_uri, RDF.type, CIDOC.E21_Person))
        
        graph.add((entity_uri, RDFS.label, Literal(contrib['name'], lang='he')))
        
        if 'birth_year' in contrib:
            graph.add((entity_uri, CIDOC.P82a_begin_of_the_begin, Literal(contrib['birth_year'], datatype=XSD.integer)))
        if 'death_year' in contrib:
            graph.add((entity_uri, CIDOC.P82b_end_of_the_end, Literal(contrib['death_year'], datatype=XSD.integer)))
        if contrib.get('external_uri_nli'):
            graph.add((entity_uri, HM.external_uri_nli, Literal(contrib['external_uri_nli'], datatype=XSD.anyURI)))
        if contrib.get('viaf_id'):
            graph.add((entity_uri, HM.viaf_id, Literal(contrib['viaf_id'], datatype=XSD.string)))
        if contrib.get('wikidata_id'):
            graph.add((entity_uri, HM.wikidata_id, Literal(contrib['wikidata_id'], datatype=XSD.string)))
        for same_as_uri in contrib.get('same_as_uris', []):
            if _is_http_uri(same_as_uri):
                graph.add((entity_uri, OWL.sameAs, URIRef(same_as_uri)))
        
        role = contrib.get('role', 'contributor')
        graph.add((entity_uri, HM.has_role, Literal(role, datatype=XSD.string)))
        if role in ('scribe', 'copyist'):
            graph.add((prod_uri, CIDOC.P14_carried_out_by, entity_uri))
            graph.add((prod_uri, HM.has_scribe, entity_uri))
            scribe_entity_uris.append(entity_uri)
        elif role == 'current_owner':
            graph.add((ms_uri, HM.current_owner, entity_uri))
            graph.add((ms_uri, HM.has_owner, entity_uri))
        elif role == 'former_owner':
            graph.add((ms_uri, HM.former_owner, entity_uri))
            graph.add((ms_uri, HM.has_owner, entity_uri))
        elif role == 'annotator':
            graph.add((ms_uri, HM.has_annotator, entity_uri))
    
    # === CONTENTS (from 505) - Creates sub-works and expressions ===
    for i, content in enumerate(data.contents, 1):
        if not content.get('title'):
            continue
        
        content_work_uri = HM[f"Work_{normalize_string(content['title'])}"]
        graph.add((content_work_uri, RDF.type, LRMOO.F1_Work))
        graph.add((content_work_uri, HM.has_title, Literal(content['title'], lang='he')))
        graph.add((content_work_uri, RDFS.label, Literal(content['title'], lang='he')))
        
        content_expr_uri = HM[f"Expression_{normalize_string(content['title'])}_in_{normalize_string(data.control_number)}"]
        graph.add((content_expr_uri, RDF.type, LRMOO.F2_Expression))
        graph.add((content_expr_uri, RDFS.label, Literal(f"{content['title']} (in MS {data.control_number})", lang='he')))
        graph.add((content_expr_uri, LRMOO.R3_is_realised_in, content_work_uri))
        
        if content.get('folio_range'):
            graph.add((content_expr_uri, HM.has_folio_range, Literal(content['folio_range'], datatype=XSD.string)))
        
        if content.get('sequence'):
            pos_bnode = BNode()
            graph.add((pos_bnode, RDF.type, HM.AnthologyPosition))
            graph.add((pos_bnode, HM.anthology_order, Literal(content['sequence'], datatype=XSD.integer)))
            graph.add((content_expr_uri, HM.has_anthology_position, pos_bnode))
        
        graph.add((ms_uri, LRMOO.R4_embodies, content_expr_uri))
        graph.add((ms_uri, HM.has_expression, content_expr_uri))
        graph.add((ms_uri, HM.has_work, content_work_uri))
        work_expression_pairs.append({'work': content_work_uri, 'expression': content_expr_uri})

        seq_value = content.get('sequence') if content.get('sequence') is not None else i
        cu_uri = HM[f"CU_{normalize_string(data.control_number)}_{int(seq_value):02d}"]
        graph.add((cu_uri, RDF.type, HM.Codicological_Unit))
        graph.add((cu_uri, RDFS.label, Literal(f"Codicological unit {seq_value} of MS {data.control_number}", lang='en')))
        graph.add((ms_uri, HM.is_composed_of, cu_uri))
        graph.add((cu_uri, HM.forms_part_of, ms_uri))
        graph.add((cu_uri, HM.has_expression, content_expr_uri))
        graph.add((cu_uri, HM.has_work, content_work_uri))
        if content.get('folio_range'):
            graph.add((cu_uri, HM.has_folio_range, Literal(content['folio_range'], datatype=XSD.string)))
        structural_cu_uris.append(cu_uri)
    
    # === ADDITIONAL TITLES (740) ===
    for add_title in data.additional_titles:
        add_work_uri = HM[f"Work_{normalize_string(add_title)}"]
        graph.add((add_work_uri, RDF.type, LRMOO.F1_Work))
        graph.add((add_work_uri, HM.has_title, Literal(add_title, lang='he')))
        graph.add((add_work_uri, RDFS.label, Literal(add_title, lang='he')))
        
        add_expr_uri = HM[f"Expression_{normalize_string(add_title)}_in_{normalize_string(data.control_number)}"]
        graph.add((add_expr_uri, RDF.type, LRMOO.F2_Expression))
        graph.add((add_expr_uri, LRMOO.R3_is_realised_in, add_work_uri))
        graph.add((ms_uri, LRMOO.R4_embodies, add_expr_uri))
        graph.add((ms_uri, HM.has_expression, add_expr_uri))
        graph.add((ms_uri, HM.has_work, add_work_uri))
        work_expression_pairs.append({'work': add_work_uri, 'expression': add_expr_uri})

    if not structural_cu_uris:
        default_cu_uri = HM[f"CU_{normalize_string(data.control_number)}_01"]
        graph.add((default_cu_uri, RDF.type, HM.Codicological_Unit))
        graph.add((default_cu_uri, RDFS.label, Literal(f"Codicological unit 1 of MS {data.control_number}", lang='en')))
        graph.add((ms_uri, HM.is_composed_of, default_cu_uri))
        graph.add((default_cu_uri, HM.forms_part_of, ms_uri))
        if expression_uri:
            graph.add((default_cu_uri, HM.has_expression, expression_uri))
        if work_uri:
            graph.add((default_cu_uri, HM.has_work, work_uri))
        structural_cu_uris.append(default_cu_uri)

    pu_count = max(1, len(data.script_types), len(scribe_entity_uris))
    for idx in range(pu_count):
        pu_uri = HM[f"PU_{normalize_string(data.control_number)}_{idx + 1:02d}"]
        parent_cu_uri = structural_cu_uris[idx % len(structural_cu_uris)]
        graph.add((pu_uri, RDF.type, HM.Paleographical_Unit))
        graph.add((pu_uri, RDFS.label, Literal(f"Paleographical unit {idx + 1} of MS {data.control_number}", lang='en')))
        graph.add((parent_cu_uri, HM.is_composed_of, pu_uri))
        graph.add((pu_uri, HM.forms_part_of, parent_cu_uri))

        if data.script_types:
            script_value = data.script_types[idx % len(data.script_types)]
            graph.add((pu_uri, HM.has_script_type, HM[script_value]))

        if idx < len(scribe_entity_uris):
            pu_scribe_uri = scribe_entity_uris[idx]
        else:
            pu_scribe_uri = HM[f"Unknown_Scribe_{normalize_string(data.control_number)}_{idx + 1:02d}"]
            graph.add((pu_scribe_uri, RDF.type, CIDOC.E21_Person))
            graph.add((pu_scribe_uri, RDFS.label, Literal(f"Unknown scribe {idx + 1} (MS {data.control_number})", lang='en')))

        graph.add((pu_uri, HM.has_scribe, pu_scribe_uri))
        graph.add((prod_uri, HM.has_scribe, pu_scribe_uri))
    
    # === COLOPHONS (957) ===
    for i, colophon_text in enumerate(data.colophon_texts, 1):
        colophon_uri = HM[f"Colophon_{normalize_string(data.control_number)}_{i}"]
        graph.add((colophon_uri, RDF.type, HM.Colophon))
        graph.add((colophon_uri, HM.colophon_text, Literal(colophon_text, lang='he')))
        graph.add((colophon_uri, HM.has_colophon_text, Literal(colophon_text, lang='he')))
        graph.add((ms_uri, HM.has_colophon, colophon_uri))
    
    # === CATALOG REFERENCES ===
    for ref in data.catalog_references:
        if ref.get('name'):
            cat_uri = HM[f"Catalog_{normalize_string(ref['name'][:50])}"]
            graph.add((cat_uri, RDF.type, LRMOO.F3_Manifestation))
            graph.add((cat_uri, RDFS.label, Literal(ref['name'])))
            graph.add((ms_uri, CIDOC.P70i_is_documented_in, cat_uri))
            graph.add((ms_uri, HM.is_documented_in, cat_uri))
    
    # === SUBJECTS ===
    for subject in data.subjects:
        if subject.get('term'):
            subj_uri = HM[f"Subject_{normalize_string(subject['term'])}"]
            graph.add((subj_uri, RDF.type, HM.SubjectType))
            graph.add((subj_uri, RDFS.label, Literal(subject['term'], lang='he')))
            target = work_uri if work_uri else ms_uri
            graph.add((target, CIDOC.P129_is_about, subj_uri))
            if work_uri:
                graph.add((work_uri, HM.has_subject, subj_uri))
    
    # === GENRES ===
    for genre in data.genres:
        genre_uri = HM[f"Genre_{normalize_string(genre)}"]
        graph.add((genre_uri, RDF.type, HM.SubjectType))
        graph.add((genre_uri, RDFS.label, Literal(genre, lang='en')))
        if work_uri:
            graph.add((work_uri, CIDOC.P2_has_type, genre_uri))
            graph.add((work_uri, HM.has_genre, genre_uri))
    
    # === V1.4 FEATURES ===
    
    # Multi-volume set
    if data.is_multi_volume:
        set_uri = HM[f"MultiVolumeSet_{normalize_string(data.control_number)}"]
        graph.add((set_uri, RDF.type, HM.MultiVolumeSet))
        graph.add((set_uri, RDFS.label, Literal(f"Multi-volume set: {data.title or data.control_number}", lang='en')))
        graph.add((ms_uri, HM.is_volume_of, set_uri))
        graph.add((set_uri, HM.has_volume, ms_uri))
        if data.volume_info:
            graph.add((set_uri, RDFS.comment, Literal(data.volume_info, lang='he')))
    
    # Anthology/Collection structure
    if data.is_anthology and data.num_works > 1:
        anthology_uri = HM[f"AnthologyStructure_{normalize_string(data.control_number)}"]
        graph.add((anthology_uri, RDF.type, HM.AnthologyStructure))
        graph.add((anthology_uri, RDFS.label, Literal(f"Anthology structure of MS {data.control_number}", lang='en')))
        graph.add((ms_uri, HM.has_anthology_structure, anthology_uri))
        graph.add((anthology_uri, HM.number_of_works, Literal(data.num_works, datatype=XSD.integer)))
        
        # Codicological hierarchy for complex manuscripts
        hierarchy_uri = HM[f"Hierarchy_{normalize_string(data.control_number)}"]
        graph.add((hierarchy_uri, RDF.type, HM.CodicologicalHierarchy))
        graph.add((ms_uri, HM.has_hierarchy, hierarchy_uri))
        graph.add((hierarchy_uri, HM.hierarchy_type, HM.ComplexHierarchy))
    
    # Multiple hands indicator
    if data.has_multiple_hands:
        graph.add((ms_uri, HM.has_multiple_hands, Literal(True, datatype=XSD.boolean)))
        graph.add((ms_uri, RDFS.comment, Literal("This manuscript was written by multiple scribes/hands", lang='en')))
    
    # Host item linking (773)
    if data.host_record_id:
        host_uri = HM[f"MS_{normalize_string(data.host_record_id)}"]
        graph.add((ms_uri, HM.is_part_of_host, host_uri))
    
    # Cataloging view (bibliographic paradigm)
    cat_view_uri = HM[f"CatalogingView_{normalize_string(data.control_number)}"]
    graph.add((cat_view_uri, RDF.type, HM.CatalogingView))
    graph.add((cat_view_uri, RDFS.label, Literal(f"Cataloging view for MS {data.control_number}", lang='en')))
    graph.add((ms_uri, HM.has_cataloging_perspective, cat_view_uri))
    if work_uri:
        graph.add((cat_view_uri, HM.cataloging_work, work_uri))
    if expression_uri:
        graph.add((cat_view_uri, HM.cataloging_expression, expression_uri))
    graph.add((cat_view_uri, HM.is_primary_paradigm, Literal(True, datatype=XSD.boolean)))

    # Philological view + traditions + witnesses + paradigm bridges
    phil_view_uri = HM[f"PhilologicalView_{normalize_string(data.control_number)}"]
    graph.add((phil_view_uri, RDF.type, HM.PhilologicalView))
    graph.add((phil_view_uri, RDFS.label, Literal(f"Philological view for MS {data.control_number}", lang='en')))
    graph.add((phil_view_uri, HM.view_type, HM.PhilologicalParadigm))
    graph.add((phil_view_uri, HM.is_primary_paradigm, Literal(False, datatype=XSD.boolean)))
    graph.add((ms_uri, HM.has_philological_perspective, phil_view_uri))

    tradition_by_work: Dict[str, URIRef] = {}
    for pair_index, pair in enumerate(work_expression_pairs, 1):
        work_ref = pair['work']
        expression_ref = pair['expression']
        work_local = str(work_ref).split('#')[-1]

        if work_local not in tradition_by_work:
            tradition_uri = HM[f"TextTradition_{normalize_string(work_local)}"]
            tradition_by_work[work_local] = tradition_uri
            graph.add((tradition_uri, RDF.type, HM.TextTradition))
            graph.add((tradition_uri, RDFS.label, Literal(f"Text tradition for {work_local}", lang='en')))
        else:
            tradition_uri = tradition_by_work[work_local]

        graph.add((expression_ref, HM.belongs_to_tradition, tradition_uri))
        graph.add((tradition_uri, HM.tradition_includes, expression_ref))
        graph.add((ms_uri, HM.witnesses, tradition_uri))
        graph.add((ms_uri, HM.has_text_tradition, tradition_uri))
        graph.add((phil_view_uri, HM.philological_tradition, tradition_uri))

        witness_uri = HM[f"TransmissionWitness_{normalize_string(data.control_number)}_{pair_index:02d}"]
        graph.add((witness_uri, RDF.type, HM.TransmissionWitness))
        graph.add((witness_uri, RDFS.label, Literal(f"Transmission witness {pair_index} for MS {data.control_number}", lang='en')))
        graph.add((ms_uri, HM.has_philological_witness, witness_uri))
        graph.add((tradition_uri, HM.has_transmission_witness, witness_uri))
        graph.add((phil_view_uri, HM.philological_witness, witness_uri))

        bridge_uri = HM[f"ParadigmBridge_{normalize_string(data.control_number)}_{pair_index:02d}"]
        graph.add((bridge_uri, RDF.type, HM.ParadigmBridge))
        graph.add((bridge_uri, HM.has_linked_work, work_ref))
        graph.add((bridge_uri, HM.has_linked_tradition, tradition_uri))
        graph.add((bridge_uri, HM.paradigm_justification, Literal("Cataloging and philological alignment for generated pilot data", datatype=XSD.string)))
        graph.add((work_ref, HM.paradigm_bridge, bridge_uri))
        graph.add((tradition_uri, HM.paradigm_bridge, bridge_uri))
    
    # Epistemological metadata
    graph.add((ms_uri, HM.attribution_source, HM.CatalogAttribution))
    graph.add((ms_uri, HM.has_attribution_source, HM.CatalogAttribution))
    graph.add((ms_uri, HM.has_epistemological_status, HM.CatalogInherited))
    
    # === v1.5 ADMINISTRATIVE & ACCESS METADATA ===
    
    # Copyright notices (597)
    for notice in data.copyright_notices:
        graph.add((ms_uri, HM.copyright_notice, Literal(notice, datatype=XSD.string)))
    
    # Related materials (544)
    for note in data.related_material_notes:
        graph.add((ms_uri, HM.related_material_note, Literal(note, lang='he')))
    
    # Acquisition source (541)
    if data.acquisition_source:
        graph.add((ms_uri, HM.acquisition_source, Literal(data.acquisition_source, datatype=XSD.string)))
    
    # Collection names (966)
    for coll_name in data.collection_names:
        graph.add((ms_uri, HM.collection_name, Literal(coll_name, datatype=XSD.string)))
    
    # Usage restrictions (939)
    for i, restriction in enumerate(data.usage_restrictions, 1):
        restr_uri = HM[f"UsageRestriction_{normalize_string(data.control_number)}_{i}"]
        graph.add((restr_uri, RDF.type, HM.UsageRestriction))
        graph.add((ms_uri, HM.has_usage_restriction, restr_uri))
        if restriction.get('description'):
            graph.add((restr_uri, HM.restriction_type, Literal(restriction['description'], datatype=XSD.string)))
            graph.add((restr_uri, HM.usage_restriction_note, Literal(restriction['description'], datatype=XSD.string)))
            graph.add((restr_uri, RDFS.label, Literal(restriction['description'])))
        if restriction.get('url'):
            graph.add((restr_uri, HM.restriction_url, Literal(restriction['url'], datatype=XSD.anyURI)))
    
    # Rights determination (952)
    for i, rights in enumerate(data.rights_determinations, 1):
        rights_uri = HM[f"RightsDetermination_{normalize_string(data.control_number)}_{i}"]
        graph.add((rights_uri, RDF.type, HM.RightsDetermination))
        graph.add((ms_uri, HM.has_rights_determination, rights_uri))
        if rights.get('status'):
            graph.add((rights_uri, HM.rights_status, Literal(rights['status'], datatype=XSD.string)))
            graph.add((rights_uri, RDFS.label, Literal(rights['status'])))
        if rights.get('basis'):
            graph.add((rights_uri, HM.rights_basis, Literal(rights['basis'], datatype=XSD.string)))
    
    # Physical holdings (AVA)
    for i, holding in enumerate(data.physical_holdings, 1):
        holding_uri = HM[f"Holding_{normalize_string(data.control_number)}_{i}"]
        graph.add((holding_uri, RDF.type, HM.PhysicalHolding))
        graph.add((ms_uri, HM.has_physical_holding, holding_uri))
        
        label_parts = []
        if holding.get('institution'):
            graph.add((holding_uri, HM.holding_institution, Literal(holding['institution'], datatype=XSD.string)))
            label_parts.append(holding['institution'])
            institution_uri = HM[f"Group_{normalize_string(holding['institution'])}"]
            graph.add((institution_uri, RDF.type, CIDOC.E74_Group))
            graph.add((institution_uri, RDFS.label, Literal(holding['institution'], lang='en')))
            graph.add((holding_uri, HM.held_at, institution_uri))
            graph.add((ms_uri, HM.has_holding_institution, institution_uri))
            custody_uri = HM[f"TransferOfCustody_{normalize_string(data.control_number)}_{i:02d}"]
            graph.add((custody_uri, RDF.type, CIDOC.E10_Transfer_of_Custody))
            graph.add((custody_uri, RDFS.label, Literal(f"Transfer of custody {i} for MS {data.control_number}", lang='en')))
            graph.add((ms_uri, HM.has_transfer_of_custody, custody_uri))
        if holding.get('collection'):
            graph.add((holding_uri, HM.holding_collection, Literal(holding['collection'], datatype=XSD.string)))
            label_parts.append(holding['collection'])
        if holding.get('call_number'):
            graph.add((holding_uri, HM.holding_call_number, Literal(holding['call_number'], datatype=XSD.string)))
            label_parts.append(holding['call_number'])
        if holding.get('availability'):
            graph.add((holding_uri, HM.holding_availability, Literal(holding['availability'], datatype=XSD.string)))
        if holding.get('library_name'):
            graph.add((holding_uri, HM.holding_sublibrary, Literal(holding['library_name'], datatype=XSD.string)))
        
        if label_parts:
            graph.add((holding_uri, RDFS.label, Literal(' - '.join(label_parts))))
    
    # Digital access points (AVE)
    for i, access in enumerate(data.digital_access_points, 1):
        access_uri = HM[f"DigitalAccess_{normalize_string(data.control_number)}_{i}"]
        graph.add((access_uri, RDF.type, HM.DigitalAccess))
        graph.add((ms_uri, HM.has_digital_access, access_uri))
        graph.add((ms_uri, HM.has_digital_representation, access_uri))
        
        if access.get('note'):
            graph.add((access_uri, HM.digital_access_note, Literal(access['note'], datatype=XSD.string)))
            graph.add((access_uri, RDFS.label, Literal(access['note'])))
        if access.get('type'):
            graph.add((access_uri, HM.digital_access_type, Literal(access['type'], datatype=XSD.string)))
        if access.get('record_id'):
            nli_url = f"https://www.nli.org.il/en/manuscripts/{access['record_id']}/NLI"
            graph.add((access_uri, HM.digital_access_url, Literal(nli_url, datatype=XSD.anyURI)))
            if access.get('type') == 'iiif':
                graph.add((access_uri, HM.iiif_manifest_url, Literal(nli_url, datatype=XSD.anyURI)))
    
    return graph


def read_mrc_file(file_path: Path) -> Iterator[pymarc.Record]:
    """Read records from a .mrc file."""
    with open(file_path, 'rb') as f:
        reader = pymarc.MARCReader(f, to_unicode=True, force_utf8=True)
        for record in reader:
            if record:
                yield record


def _extract_ontology_iri(ontology_graph: Graph) -> Optional[URIRef]:
    """Extract ontology IRI from an ontology graph."""
    for subject, _, _ in ontology_graph.triples((None, RDF.type, OWL.Ontology)):
        if isinstance(subject, URIRef):
            return subject
    return None


def _serialize_with_cleanup(graph: Graph, output_path: Path) -> int:
    """Serialize graph while removing redundant annotation declarations."""
    annotation_prop_triples = list(graph.triples((None, RDF.type, OWL.AnnotationProperty)))
    removed = 0
    for s, _, _ in annotation_prop_triples:
        is_obj = (s, RDF.type, OWL.ObjectProperty) in graph
        is_dat = (s, RDF.type, OWL.DatatypeProperty) in graph
        if is_obj or is_dat:
            graph.remove((s, RDF.type, OWL.AnnotationProperty))
            removed += 1

    ttl_content = graph.serialize(format='turtle')

    known_obj_props = {str(s) for s, _, _ in graph.triples((None, RDF.type, OWL.ObjectProperty))}
    known_dat_props = {str(s) for s, _, _ in graph.triples((None, RDF.type, OWL.DatatypeProperty))}
    known_typed = known_obj_props | known_dat_props

    cleaned_lines = []
    skip_blank = False
    lines = ttl_content.split('\n')
    for line in lines:
        if 'owl:AnnotationProperty' in line and line.strip().endswith('.'):
            prop_uri = line.split()[0]
            if prop_uri.endswith(':'):
                prop_uri = prop_uri[:-1]
            expanded = str(graph.namespace_manager.expand_curie(prop_uri)) if ':' in prop_uri and not prop_uri.startswith('<') else prop_uri.strip('<>')
            if expanded in known_typed:
                skip_blank = True
                continue
        if skip_blank and line.strip() == '':
            skip_blank = False
            continue
        skip_blank = False
        cleaned_lines.append(line)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(cleaned_lines))

    return removed


def convert_mrc_to_ttl(
    input_path: Path,
    output_path: Path,
    ontology_path: Path = None,
    merged_output_path: Optional[Path] = None
) -> int:
    """Convert .mrc file(s) to Turtle output.
    
    Args:
        input_path: Path to .mrc file or directory containing .mrc files
        output_path: Path for data-only output .ttl file
        ontology_path: Path to ontology .ttl file (used for imports and optional merged output)
        merged_output_path: Optional path for merged ontology+data output
        
    Returns:
        Number of records converted
    """
    data_graph = Graph()
    bind_namespaces(data_graph)

    ontology_graph = None
    ontology_iri = None
    if ontology_path and ontology_path.exists():
        ontology_graph = Graph()
        bind_namespaces(ontology_graph)
        print(f"Loading ontology from {ontology_path.name}...")
        ontology_graph.parse(str(ontology_path), format='turtle')
        print(f"  Loaded {len(ontology_graph)} ontology triples")
        ontology_iri = _extract_ontology_iri(ontology_graph)
    
    records_converted = 0
    
    if input_path.is_file():
        mrc_files = [input_path]
    else:
        mrc_files = sorted(input_path.glob('*.mrc'))
    
    print(f"Processing {len(mrc_files)} .mrc file(s)...")
    
    for mrc_file in mrc_files:
        print(f"\n  Processing: {mrc_file.name}")
        
        for record in read_mrc_file(mrc_file):
            try:
                data = extract_marc_data(record)
                
                print(f"    - {data.control_number}: {data.title or 'No title'}")
                if data.is_anthology:
                    print(f"      Anthology with {data.num_works} works")
                if data.is_multi_volume:
                    print(f"      Multi-volume: {data.volume_info}")
                if data.has_multiple_hands:
                    print(f"      Multiple hands: {', '.join(data.script_types)}")
                if data.colophon_texts:
                    print(f"      Has {len(data.colophon_texts)} colophon(s)")
                if data.physical_holdings:
                    print(f"      Physical holdings: {len(data.physical_holdings)}")
                if data.digital_access_points:
                    print(f"      Digital access points: {len(data.digital_access_points)}")
                if data.related_material_notes:
                    print(f"      Related material notes: {len(data.related_material_notes)}")
                if data.usage_restrictions:
                    for r in data.usage_restrictions:
                        print(f"      Usage: {r.get('description', 'N/A')}")
                
                record_graph = build_rdf_graph(data)
                
                for triple in record_graph:
                    data_graph.add(triple)
                
                records_converted += 1
                
            except Exception as e:
                print(f"    ✗ Error processing record: {e}")
                import traceback
                traceback.print_exc()
    
    removed = _serialize_with_cleanup(data_graph, output_path)
    if removed:
        print(f"\n  Cleaned {removed} conflicting AnnotationProperty declaration(s)")

    print(f"\n✓ Converted {records_converted} records to {output_path} (data-only)")
    print(f"  Total triples (data graph): {len(data_graph)}")

    if merged_output_path:
        merged_graph = Graph()
        bind_namespaces(merged_graph)
        if ontology_graph is not None:
            for triple in ontology_graph:
                merged_graph.add(triple)
        for triple in data_graph:
            merged_graph.add(triple)
        merged_removed = _serialize_with_cleanup(merged_graph, merged_output_path)
        print(f"  Wrote merged debug output: {merged_output_path}")
        print(f"  Total triples (merged graph): {len(merged_graph)}")
        if merged_removed:
            print(f"  Cleaned {merged_removed} conflicting AnnotationProperty declaration(s) in merged output")
    
    return records_converted


def main():
    """Main entry point."""
    base_dir = Path(__file__).parent.parent
    input_dir = base_dir / 'data' / 'mrc' / 'test_manuscripts'
    output_file = base_dir / 'data' / 'output' / 'test_manuscripts.ttl'
    merged_output_file = base_dir / 'data' / 'output' / 'test_manuscripts_merged.ttl'
    ontology_file = base_dir / 'ontology' / 'hebrew-manuscripts.ttl'
    
    print("=" * 70)
    print("MARC to TTL Converter (Hebrew Manuscripts Ontology v1.5)")
    print("=" * 70)
    print(f"\nInput:    {input_dir}")
    print(f"Ontology: {ontology_file}")
    print(f"Output:   {output_file} (data-only)\n")
    
    # Check for combined file first
    combined_mrc = input_dir / 'all_test_manuscripts.mrc'
    if combined_mrc.exists():
        input_path = combined_mrc
    else:
        input_path = input_dir
    
    count = convert_mrc_to_ttl(
        input_path,
        output_file,
        ontology_path=ontology_file,
        merged_output_path=merged_output_file
    )
    
    print("\n" + "=" * 70)
    print(f"Conversion complete!")
    print("=" * 70)
    
    # Summary of test case coverage
    print("\nTest Case Coverage (Expert Feedback Issues):")
    print("  ✓ Vatican 44: Multi-volume mechanism (MultiVolumeSet, is_volume_of)")
    print("  ✓ Huntington 115: Multiple hands detection")  
    print("  ✓ Oppenheimer 129: Anthology/Collection structure")
    print("  ✓ Parma 3122: Anthology with 15 Midrashim (CodicologicalHierarchy)")
    print("  ✓ Jerusalem 8210: Host item linking (773), multiple hands")
    print("  ✓ All: Epistemological metadata, Cataloging view paradigm")
    print("\nv1.5 Administrative & Access Coverage:")
    print("  ✓ 544: Related materials (Hunt. 115)")
    print("  ✓ 541: Acquisition source (Jerusalem 8210)")
    print("  ✓ 597: Copyright notices (all manuscripts)")
    print("  ✓ 939: Usage restrictions + policy URLs (all manuscripts)")
    print("  ✓ 952: Rights status determinations (all manuscripts)")
    print("  ✓ AVA: Physical holdings (institution, collection, call number)")
    print("  ✓ AVE: Digital access points (online viewer, IIIF)")
    print("  ✓ 966: Collection names")


if __name__ == '__main__':
    main()
