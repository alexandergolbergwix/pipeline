"""MARC field-specific handlers for RDF transformation.

Fully updated for v1.4 ontology with support for:
- Certainty level extraction from MARC data patterns
- Attribution source tracking
- Data category classification
- Codicological unit detection (nested CU support)
- Scribal intervention detection
- Canonical hierarchy references
"""

import re
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

from ..parser.marc_reader import MarcField, MarcRecord
from ..config.vocabularies import (
    LANGUAGE_CODES, MATERIAL_TYPES, SCRIPT_TYPES, 
    SCRIPT_MODES, ROLE_MAPPINGS, DATE_TYPE_CODES,
    CERTAINTY_LEVELS, DATA_FACTUALITY, INTERVENTION_TYPES,
    CANONICAL_HIERARCHIES, UNIT_STATUS_TYPES
)


@dataclass
class ExtractedData:
    """Container for data extracted from MARC fields.
    
    Fully supports v1.4 ontology features including:
    - certainty_levels: Dict mapping field names to certainty levels
    - attribution_sources: Dict mapping field names to attribution sources
    - data_from_colophon: List of fields with colophon-derived data
    - codicological_units: List of detected CU structures
    - scribal_interventions: List of detected interventions
    - canonical_references: List of canonical text references
    - text_traditions: List of identified text traditions
    """
    # Core bibliographic data
    title: Optional[str] = None
    subtitle: Optional[str] = None
    variant_titles: List[str] = None
    authors: List[Dict[str, Any]] = None
    contributors: List[Dict[str, Any]] = None
    dates: Dict[str, Any] = None
    place: Optional[str] = None
    extent: Optional[int] = None
    height_mm: Optional[int] = None
    width_mm: Optional[int] = None
    materials: List[str] = None
    languages: List[str] = None
    script_type: Optional[str] = None
    script_mode: Optional[str] = None
    notes: List[str] = None
    contents: List[Dict[str, Any]] = None
    subjects: List[Dict[str, Any]] = None
    genres: List[str] = None
    provenance: Optional[str] = None
    digital_url: Optional[str] = None
    catalog_references: List[Dict[str, str]] = None
    external_ids: Dict[str, str] = None
    colophon_text: Optional[str] = None
    binding_info: Optional[str] = None
    
    # v1.4 Ontology features: Certainty and Attribution tracking
    certainty_levels: Dict[str, str] = None
    attribution_sources: Dict[str, str] = None
    data_from_colophon: List[str] = None
    
    # v1.4 Ontology features: Codicological structure
    codicological_units: List[Dict[str, Any]] = None
    hierarchy_type: Optional[str] = None
    is_anthology: bool = False
    is_multi_volume: bool = False
    volume_number: Optional[int] = None
    volume_info: Optional[str] = None
    
    # v1.4 Ontology features: Scribal interventions
    scribal_interventions: List[Dict[str, Any]] = None
    
    # v1.4 Ontology features: Canonical references
    canonical_references: List[Dict[str, Any]] = None
    
    # v1.4 Ontology features: Text tradition
    text_traditions: List[str] = None
    textual_variants: List[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.variant_titles is None:
            self.variant_titles = []
        if self.authors is None:
            self.authors = []
        if self.contributors is None:
            self.contributors = []
        if self.dates is None:
            self.dates = {}
        if self.materials is None:
            self.materials = []
        if self.languages is None:
            self.languages = []
        if self.notes is None:
            self.notes = []
        if self.contents is None:
            self.contents = []
        if self.subjects is None:
            self.subjects = []
        if self.genres is None:
            self.genres = []
        if self.catalog_references is None:
            self.catalog_references = []
        if self.external_ids is None:
            self.external_ids = {}
        # v1.4 additions
        if self.certainty_levels is None:
            self.certainty_levels = {}
        if self.attribution_sources is None:
            self.attribution_sources = {}
        if self.data_from_colophon is None:
            self.data_from_colophon = []
        if self.codicological_units is None:
            self.codicological_units = []
        if self.scribal_interventions is None:
            self.scribal_interventions = []
        if self.canonical_references is None:
            self.canonical_references = []
        if self.text_traditions is None:
            self.text_traditions = []
        if self.textual_variants is None:
            self.textual_variants = []
    
    def set_certainty(self, field_name: str, level: str, note: Optional[str] = None):
        """Set certainty level for a field.
        
        Args:
            field_name: Name of the data field
            level: Certainty level (Certain, Probable, Possible, Uncertain)
            note: Optional explanation
        """
        self.certainty_levels[field_name] = level
        if note:
            self.certainty_levels[f"{field_name}_note"] = note
    
    def set_attribution(self, field_name: str, source: str):
        """Set attribution source for a field.
        
        Args:
            field_name: Name of the data field
            source: Attribution source (CatalogAttribution, ColophonAttribution, etc.)
        """
        self.attribution_sources[field_name] = source
    
    def mark_from_colophon(self, field_name: str):
        """Mark a field as derived from colophon data."""
        if field_name not in self.data_from_colophon:
            self.data_from_colophon.append(field_name)


class FieldHandlers:
    """Collection of handlers for different MARC fields."""
    
    @staticmethod
    def handle_008(field: MarcField) -> Dict[str, Any]:
        """Extract data from 008 fixed-length field.
        
        Args:
            field: MARC 008 field
            
        Returns:
            Dictionary with extracted date and place info
        """
        result = {}
        data = field.data or ""
        
        if len(data) < 40:
            return result
        
        date_type = data[6] if len(data) > 6 else ''
        result['date_type'] = DATE_TYPE_CODES.get(date_type, 'unknown')
        
        date1 = data[7:11].strip() if len(data) >= 11 else ''
        if date1 and date1.isdigit():
            result['date_start'] = int(date1)
        
        date2 = data[11:15].strip() if len(data) >= 15 else ''
        if date2 and date2.isdigit():
            result['date_end'] = int(date2)
        
        place_code = data[15:18].strip() if len(data) >= 18 else ''
        if place_code and place_code not in ('xx', 'vp', '|||'):
            result['place_code'] = place_code
        
        lang_code = data[35:38].strip() if len(data) >= 38 else ''
        if lang_code and lang_code not in ('|||', '   '):
            result['language'] = lang_code
        
        return result
    
    @staticmethod
    def handle_041(field: MarcField) -> List[str]:
        """Extract language codes from 041 field.
        
        Args:
            field: MARC 041 field
            
        Returns:
            List of language codes
        """
        languages = []
        
        for code in ['a', 'b', 'd', 'e', 'f', 'g', 'h']:
            values = field.get_all_subfields(code)
            for val in values:
                for i in range(0, len(val), 3):
                    lang = val[i:i+3].strip()
                    if lang and lang not in languages:
                        languages.append(lang)
        
        return languages
    
    @staticmethod
    def handle_100(field: MarcField) -> Dict[str, Any]:
        """Extract author information from 100 field.
        
        Args:
            field: MARC 100 field
            
        Returns:
            Dictionary with author data
        """
        result = {
            'name': field.get_subfield('a'),
            'role': 'author',
        }
        
        dates = field.get_subfield('d')
        if dates:
            result['dates'] = dates
            parsed = FieldHandlers._parse_person_dates(dates)
            if parsed:
                result.update(parsed)
        
        role = field.get_subfield('e')
        if role:
            role_lower = role.lower().strip().rstrip('.')
            result['role'] = ROLE_MAPPINGS.get(role_lower, role_lower)
        
        authority_id = field.get_subfield('0')
        if authority_id:
            result['authority_id'] = authority_id
        
        return result
    
    @staticmethod
    def handle_110(field: MarcField) -> Dict[str, Any]:
        """Extract corporate name from 110 field.
        
        Args:
            field: MARC 110 field
            
        Returns:
            Dictionary with organization data
        """
        return {
            'name': field.get_subfield('a'),
            'type': 'organization',
            'authority_id': field.get_subfield('0'),
        }
    
    @staticmethod
    def handle_245(field: MarcField) -> Dict[str, str]:
        """Extract title from 245 field.
        
        Args:
            field: MARC 245 field
            
        Returns:
            Dictionary with title and subtitle
        """
        title = field.get_subfield('a')
        if title:
            title = title.rstrip(' /:')
        
        subtitle = field.get_subfield('b')
        if subtitle:
            subtitle = subtitle.rstrip(' /')
        
        return {
            'title': title,
            'subtitle': subtitle,
            'part_number': field.get_subfield('n'),
            'part_name': field.get_subfield('p'),
        }
    
    @staticmethod
    def handle_246(field: MarcField) -> Optional[str]:
        """Extract variant title from 246 field.
        
        Args:
            field: MARC 246 field
            
        Returns:
            Variant title or None
        """
        return field.get_subfield('a')
    
    @staticmethod
    def handle_260_264(field: MarcField) -> Dict[str, Any]:
        """Extract production information from 260/264 field.
        
        Args:
            field: MARC 260 or 264 field
            
        Returns:
            Dictionary with place and date
        """
        place = field.get_subfield('a')
        if place:
            place = place.rstrip(' :;,')
        
        date = field.get_subfield('c')
        if date:
            date = date.strip('[].')
        
        return {
            'place': place,
            'date_string': date,
            'parsed_date': FieldHandlers._parse_date_string(date) if date else None,
        }
    
    @staticmethod
    def handle_300(field: MarcField) -> Dict[str, Any]:
        """Extract physical description from 300 field.
        
        Args:
            field: MARC 300 field
            
        Returns:
            Dictionary with extent and dimensions
        """
        result = {}
        
        extent = field.get_subfield('a')
        if extent:
            folio_count = FieldHandlers._parse_extent(extent)
            if folio_count:
                result['folios'] = folio_count
            result['extent_string'] = extent
            extent_lower = extent.lower()
            if 'כרך' in extent or 'כרכים' in extent or 'volume' in extent_lower or 'volumes' in extent_lower:
                result['is_multi_volume'] = True
                result['volume_info'] = extent
        
        other = field.get_subfield('b')
        if other:
            result['other_physical'] = other
        
        dimensions = field.get_subfield('c')
        if dimensions:
            parsed = FieldHandlers._parse_dimensions(dimensions)
            if parsed:
                result.update(parsed)
            result['dimensions_string'] = dimensions
        
        return result
    
    @staticmethod
    def handle_340(field: MarcField) -> List[str]:
        """Extract material information from 340 field.
        
        Args:
            field: MARC 340 field
            
        Returns:
            List of material types
        """
        materials = []
        
        material = field.get_subfield('a')
        if material:
            for mat_key, mat_value in MATERIAL_TYPES.items():
                if mat_key.lower() in material.lower():
                    if mat_value not in materials:
                        materials.append(mat_value)
        
        return materials
    
    @staticmethod
    def handle_500(field: MarcField) -> Dict[str, Any]:
        """Extract and analyze general note from 500 field.
        
        Args:
            field: MARC 500 field
            
        Returns:
            Dictionary with note text and extracted data
        """
        note = field.get_subfield('a')
        if not note:
            return {}
        
        result = {'text': note}
        
        for script_key, script_value in SCRIPT_TYPES.items():
            if script_key.lower() in note.lower():
                result['script_type'] = script_value
                break
        
        for mode_key, mode_value in SCRIPT_MODES.items():
            if mode_key.lower() in note.lower():
                result['script_mode'] = mode_value
                break
        
        colophon_keywords = ['קולופון', 'colophon', 'כתב הסופר', 'כתב המעתיק']
        for keyword in colophon_keywords:
            if keyword.lower() in note.lower():
                result['is_colophon'] = True
                result['colophon_text'] = note
                break
        
        # v1.4: Detect codicological unit indicators
        cu_indicators = FieldHandlers._detect_codicological_units(note)
        if cu_indicators:
            result['codicological_units'] = cu_indicators
        
        # v1.4: Detect scribal interventions
        interventions = FieldHandlers._detect_scribal_interventions(note)
        if interventions:
            result['scribal_interventions'] = interventions
        
        # v1.4: Detect canonical references
        canonical_refs = FieldHandlers._detect_canonical_references(note)
        if canonical_refs:
            result['canonical_references'] = canonical_refs
        
        return result
    
    @staticmethod
    def _detect_codicological_units(note: str) -> List[Dict[str, Any]]:
        """Detect codicological unit indicators from note text.
        
        Looks for patterns indicating multiple CUs, composite manuscripts,
        Sammelbands, MTM (Multiple-Text Manuscripts), etc.
        
        Args:
            note: Note text to analyze
            
        Returns:
            List of detected CU information
        """
        units = []
        
        # Patterns for detecting composite manuscripts
        composite_patterns = [
            (r'יחידה\s*(\d+)', 'unit_number'),
            (r'חלק\s*(\d+)', 'part_number'),
            (r'קוויון[ים]*\s*(\d+[-–]\d+|\d+)', 'quire_range'),
            (r'part\s*(\d+)', 'part_number'),
            (r'unit\s*(\d+)', 'unit_number'),
            (r'ff?\.?\s*(\d+[rv]?)\s*[-–]\s*(\d+[rv]?)', 'folio_range'),
        ]
        
        for pattern, unit_type in composite_patterns:
            matches = re.finditer(pattern, note, re.IGNORECASE)
            for match in matches:
                unit_info = {'type': unit_type}
                if unit_type == 'folio_range':
                    unit_info['start'] = match.group(1)
                    unit_info['end'] = match.group(2)
                else:
                    unit_info['value'] = match.group(1)
                units.append(unit_info)
        
        # Detect anthology/Sammelband indicators
        anthology_keywords = ['אנתולוגיה', 'anthology', 'sammelband', 'קובץ', 
                              'מספר חיבורים', 'multiple texts', 'composite']
        for keyword in anthology_keywords:
            if keyword.lower() in note.lower():
                units.append({'type': 'anthology_indicator', 'keyword': keyword})
                break
        
        return units
    
    @staticmethod
    def _detect_scribal_interventions(note: str) -> List[Dict[str, Any]]:
        """Detect scribal interventions from note text.
        
        Args:
            note: Note text to analyze
            
        Returns:
            List of detected interventions
        """
        interventions = []
        
        # Hebrew and English patterns for interventions
        intervention_patterns = [
            (r'תיקון[ים]*', 'Correction_type'),
            (r'מחיק[הות]*', 'Erasure_type'),
            (r'הוספ[הות]*\s*(?:בין\s*ה?שורות)?', 'Interlinear_addition_type'),
            (r'הערות?\s*שוליים', 'Marginal_gloss_type'),
            (r'הגהות', 'Marginal_gloss_type'),
            (r'יד\s*(?:שנ[יה]+|אחרת|מאוחרת)', 'Later_hand_type'),
            (r'correction[s]?', 'Correction_type'),
            (r'erasure[s]?', 'Erasure_type'),
            (r'interlinear', 'Interlinear_addition_type'),
            (r'marginal(?:\s*note[s]?|\s*gloss(?:es)?)?', 'Marginal_gloss_type'),
            (r'later\s*hand', 'Later_hand_type'),
            (r'second\s*hand', 'Later_hand_type'),
        ]
        
        for pattern, intervention_type in intervention_patterns:
            if re.search(pattern, note, re.IGNORECASE):
                interventions.append({
                    'type': intervention_type,
                    'source_note': note[:100]
                })
        
        return interventions
    
    @staticmethod
    def _detect_canonical_references(note: str) -> List[Dict[str, Any]]:
        """Detect canonical text references from note text.
        
        Identifies references to biblical, talmudic, and other canonical texts.
        
        Args:
            note: Note text to analyze
            
        Returns:
            List of canonical references with hierarchy type
        """
        references = []
        
        # Biblical book patterns (Hebrew and English)
        biblical_books = {
            'בראשית': 'Genesis', 'שמות': 'Exodus', 'ויקרא': 'Leviticus',
            'במדבר': 'Numbers', 'דברים': 'Deuteronomy', 'יהושע': 'Joshua',
            'שופטים': 'Judges', 'שמואל': 'Samuel', 'מלכים': 'Kings',
            'ישעיה': 'Isaiah', 'ירמיה': 'Jeremiah', 'יחזקאל': 'Ezekiel',
            'תהלים': 'Psalms', 'משלי': 'Proverbs', 'איוב': 'Job',
            'genesis': 'Genesis', 'exodus': 'Exodus', 'leviticus': 'Leviticus',
            'numbers': 'Numbers', 'deuteronomy': 'Deuteronomy',
        }
        
        for heb_name, eng_name in biblical_books.items():
            if heb_name.lower() in note.lower():
                # Try to extract chapter/verse
                ref_match = re.search(
                    rf'{heb_name}\s*[,:\s]*(\d+)[,:\s]*(\d+)?',
                    note, re.IGNORECASE
                )
                ref_info = {
                    'hierarchy': 'Bible',
                    'book': eng_name,
                }
                if ref_match:
                    ref_info['chapter'] = ref_match.group(1)
                    if ref_match.group(2):
                        ref_info['verse'] = ref_match.group(2)
                references.append(ref_info)
        
        # Talmudic tractate patterns
        tractates = ['ברכות', 'שבת', 'פסחים', 'יומא', 'סוכה', 'ביצה',
                     'ראש השנה', 'תענית', 'מגילה', 'מועד קטן', 'חגיגה',
                     'יבמות', 'כתובות', 'נדרים', 'נזיר', 'סוטה', 'גיטין',
                     'קידושין', 'בבא קמא', 'בבא מציעא', 'בבא בתרא',
                     'סנהדרין', 'מכות', 'שבועות', 'עבודה זרה', 'הוריות',
                     'זבחים', 'מנחות', 'חולין', 'בכורות', 'ערכין',
                     'berakhot', 'shabbat', 'pesachim', 'yoma', 'sukkah']
        
        for tractate in tractates:
            if tractate.lower() in note.lower():
                # Try to extract folio reference
                folio_match = re.search(
                    rf'{tractate}\s*(\d+[ab]?)',
                    note, re.IGNORECASE
                )
                ref_info = {
                    'hierarchy': 'Talmud_Bavli',
                    'tractate': tractate,
                }
                if folio_match:
                    ref_info['folio'] = folio_match.group(1)
                references.append(ref_info)
        
        return references
    
    @staticmethod
    def handle_505(field: MarcField) -> List[Dict[str, Any]]:
        """Extract contents from 505 field.
        
        Args:
            field: MARC 505 field
            
        Returns:
            List of content items with titles and folio ranges
        """
        contents = []
        
        formatted_contents = field.get_subfield('a')
        if formatted_contents:
            items = FieldHandlers._parse_contents_string(formatted_contents)
            contents.extend(items)
        
        titles = field.get_all_subfields('t')
        responsibilities = field.get_all_subfields('r')
        misc = field.get_all_subfields('g')
        
        for i, title in enumerate(titles):
            item = {'title': title.rstrip(' /')}
            if i < len(responsibilities):
                item['responsibility'] = responsibilities[i]
            if i < len(misc):
                folio_range = FieldHandlers._extract_folio_range(misc[i])
                if folio_range:
                    item['folio_range'] = folio_range
            contents.append(item)
        
        return contents
    
    @staticmethod
    def handle_510(field: MarcField) -> Dict[str, str]:
        """Extract catalog reference from 510 field.
        
        Args:
            field: MARC 510 field
            
        Returns:
            Dictionary with catalog name and location
        """
        return {
            'name': field.get_subfield('a'),
            'location': field.get_subfield('c'),
        }
    
    @staticmethod
    def handle_561(field: MarcField) -> str:
        """Extract provenance from 561 field.
        
        Args:
            field: MARC 561 field
            
        Returns:
            Provenance text
        """
        return field.get_subfield('a') or ''
    
    @staticmethod
    def handle_563(field: MarcField) -> str:
        """Extract binding information from 563 field.
        
        Args:
            field: MARC 563 field
            
        Returns:
            Binding description
        """
        return field.get_subfield('a') or ''
    
    @staticmethod
    def handle_6xx(field: MarcField) -> Dict[str, Any]:
        """Extract subject information from 6XX fields.
        
        Args:
            field: MARC 6XX field
            
        Returns:
            Dictionary with subject data
        """
        tag = field.tag
        
        result = {
            'term': field.get_subfield('a'),
            'authority_id': field.get_subfield('0'),
            'source': field.get_subfield('2'),
        }
        
        if tag == '600':
            result['type'] = 'person'
            dates = field.get_subfield('d')
            if dates:
                result['dates'] = dates
        elif tag == '610':
            result['type'] = 'organization'
        elif tag == '650':
            result['type'] = 'topic'
        elif tag == '651':
            result['type'] = 'place'
        elif tag == '655':
            result['type'] = 'genre'
        
        return result
    
    @staticmethod
    def handle_700(field: MarcField) -> Dict[str, Any]:
        """Extract added personal name from 700 field.
        
        Args:
            field: MARC 700 field
            
        Returns:
            Dictionary with person and role data
        """
        result = FieldHandlers.handle_100(field)
        
        relator_code = field.get_subfield('4')
        if relator_code:
            result['relator_code'] = relator_code
        
        return result
    
    @staticmethod
    def handle_856(field: MarcField) -> Dict[str, str]:
        """Extract electronic location from 856 field.
        
        Args:
            field: MARC 856 field
            
        Returns:
            Dictionary with URL and description
        """
        return {
            'url': field.get_subfield('u'),
            'link_text': field.get_subfield('y'),
            'note': field.get_subfield('z'),
        }
    
    @staticmethod
    def _parse_person_dates(dates_str: str) -> Dict[str, int]:
        """Parse person dates from MARC date string.
        
        Args:
            dates_str: Date string like "1135-1204" or "פעיל 1407"
            
        Returns:
            Dictionary with birth_year and/or death_year
        """
        result = {}
        
        match = re.search(r'(\d{3,4})\s*-\s*(\d{3,4})', dates_str)
        if match:
            result['birth_year'] = int(match.group(1))
            result['death_year'] = int(match.group(2))
            return result
        
        match = re.search(r'(\d{3,4})\s*-', dates_str)
        if match:
            result['birth_year'] = int(match.group(1))
            return result
        
        match = re.search(r'-\s*(\d{3,4})', dates_str)
        if match:
            result['death_year'] = int(match.group(1))
            return result
        
        match = re.search(r'פעיל\s*(\d{3,4})|active\s*(\d{3,4})|fl\.?\s*(\d{3,4})', 
                          dates_str, re.IGNORECASE)
        if match:
            year = next(g for g in match.groups() if g)
            result['active_year'] = int(year)
        
        return result
    
    @staticmethod
    def _parse_date_string(date_str: str) -> Dict[str, Any]:
        """Parse a date string to extract year(s) with certainty and format detection.
        
        Args:
            date_str: Date string from MARC record
            
        Returns:
            Dictionary with parsed date information including:
            - certainty level (v1.4)
            - date_format: GregorianYear, HebrewYear, FullDate, or UnstructuredDate
            - original_string: the original date text
        """
        result = {
            'original_string': date_str,
            'date_format': 'UnstructuredDate',  # Default
        }
        
        # 1. Check for full date format (dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy)
        full_date_match = re.search(
            r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})', 
            date_str
        )
        if full_date_match:
            day, month, year = full_date_match.groups()
            year_int = int(year)
            if len(year) == 2:
                year_int = 1900 + year_int if year_int > 50 else 2000 + year_int
            result['day'] = int(day)
            result['month'] = int(month)
            result['year'] = year_int
            result['date_format'] = 'FullDate'
        
        # 2. Check for Hebrew year format (תש״ה, תשפ״ד, ה'קס"ז, etc.)
        # Matches: ה'xxx, הת'xxx, תxxx, with various quote/gershayim combinations
        hebrew_year_patterns = [
            r"[הת]['\u05F3]?[א-ת]{1,4}[\"״\u05F4]?[א-ת]?",  # תשפ״ד, ה'קסז, etc.
            r"\([הת]['\u05F3]?[א-ת]{1,5}\)",  # (תשפד)
            r"שנת\s+[א-ת]{1,5}",  # שנת תשפד
        ]
        for pattern in hebrew_year_patterns:
            hebrew_year_match = re.search(pattern, date_str)
            if hebrew_year_match:
                result['hebrew_date'] = hebrew_year_match.group(0)
                result['date_format'] = 'HebrewYear'
                break
        
        # 3. Check for full Hebrew date (day + month + year)
        hebrew_full_date = re.search(
            r"[א-ת]['\u05F3\"״]?\s+[א-ת]+\s+[הת]['\u05F3]?[א-ת]{1,5}",
            date_str
        )
        if hebrew_full_date:
            result['hebrew_full_date'] = hebrew_full_date.group(0)
            result['date_format'] = 'FullDate'
        
        # 4. Check for Gregorian year (yyyy) - only if no format detected yet
        if result['date_format'] == 'UnstructuredDate':
            gregorian_year_match = re.search(r'\b(\d{4})\b', date_str)
            if gregorian_year_match:
                result['year'] = int(gregorian_year_match.group(1))
                result['date_format'] = 'GregorianYear'
        elif 'year' not in result:
            # Also extract year even if format already detected (for FullDate/HebrewYear)
            gregorian_year_match = re.search(r'\b(\d{4})\b', date_str)
            if gregorian_year_match:
                result['year'] = int(gregorian_year_match.group(1))
        
        # Detect uncertainty markers and set certainty level (v1.4)
        if '[' in date_str or '?' in date_str or 'ca.' in date_str.lower():
            result['approximate'] = True
            result['certainty'] = 'Possible'  # Approximate dates are uncertain
        elif 'בערך' in date_str or 'לערך' in date_str:  # Hebrew approximate markers
            result['approximate'] = True
            result['certainty'] = 'Possible'
        else:
            result['certainty'] = 'Probable'  # Most catalog dates are interpretations
        
        # Handle century-level dating
        century_match = re.search(r'(\d{1,2})(?:th|st|nd|rd)?\s*cent', date_str, re.IGNORECASE)
        if century_match:
            century = int(century_match.group(1))
            result['century'] = century
            result['year_start'] = (century - 1) * 100 + 1
            result['year_end'] = century * 100
            result['certainty'] = 'Possible'  # Century-level dating is less certain
            if result['date_format'] == 'UnstructuredDate':
                result['date_format'] = 'UnstructuredDate'  # Century notation is unstructured
        
        return result
    
    @staticmethod
    def _parse_extent(extent_str: str) -> Optional[int]:
        """Parse folio count from extent string.
        
        Args:
            extent_str: Extent string like "248 leaves" or "150 ff."
            
        Returns:
            Number of folios or None
        """
        match = re.search(r'\[?(\d+)\]?\s*(?:leaves?|ff?\.?|folios?|דפים)', 
                          extent_str, re.IGNORECASE)
        if match:
            return int(match.group(1))
        
        match = re.search(r'\[?(\d+)\]?,?\s*\[?(\d+)\]?\s*(?:leaves?|ff?\.?|folios?|דפים)', 
                          extent_str, re.IGNORECASE)
        if match:
            return int(match.group(1)) + int(match.group(2))
        
        return None
    
    @staticmethod
    def _parse_dimensions(dim_str: str) -> Dict[str, int]:
        """Parse dimensions from dimension string.
        
        Args:
            dim_str: Dimension string like "280 x 200 mm" or "28 cm"
            
        Returns:
            Dictionary with height_mm and width_mm
        """
        result = {}
        
        match = re.search(r'(\d+)\s*[xX×]\s*(\d+)\s*(?:mm|מ"מ)', dim_str)
        if match:
            result['height_mm'] = int(match.group(1))
            result['width_mm'] = int(match.group(2))
            return result
        
        match = re.search(r'(\d+)\s*(?:cm|ס"מ)', dim_str)
        if match:
            result['height_mm'] = int(match.group(1)) * 10
            return result
        
        match = re.search(r'(\d+)\s*[xX×]\s*(\d+)', dim_str)
        if match:
            h, w = int(match.group(1)), int(match.group(2))
            if h > 100 and w > 100:
                result['height_mm'] = h
                result['width_mm'] = w
            else:
                result['height_mm'] = h * 10
                result['width_mm'] = w * 10
        
        return result
    
    @staticmethod
    def _parse_contents_string(contents: str) -> List[Dict[str, Any]]:
        """Parse unstructured contents string into items.
        
        Args:
            contents: Contents string from 505$a
            
        Returns:
            List of content items
        """
        items = []
        
        parts = re.split(r'[;.\-\-]', contents)
        
        for part in parts:
            part = part.strip()
            if not part or len(part) < 3:
                continue
            
            item = {}
            
            folio_match = re.search(r'\(?\s*(?:ff?\.?|דפים?)\s*(\d+[rv]?(?:\s*-\s*\d+[rv]?)?)\s*\)?', 
                                    part, re.IGNORECASE)
            if folio_match:
                item['folio_range'] = folio_match.group(1)
                part = part[:folio_match.start()] + part[folio_match.end():]
            
            part = part.strip(' ,.:;')
            if part:
                item['title'] = part
                items.append(item)
        
        return items
    
    @staticmethod
    def _extract_folio_range(text: str) -> Optional[str]:
        """Extract folio range from miscellaneous text.
        
        Args:
            text: Text that might contain folio references
            
        Returns:
            Folio range string or None
        """
        match = re.search(r'(?:ff?\.?|דפים?|fol\.?)?\s*(\d+[rv]?(?:\s*-\s*\d+[rv]?)?)', 
                          text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None


def extract_all_data(record: MarcRecord) -> ExtractedData:
    """Extract all data from a MARC record using appropriate handlers.
    
    Includes v1.4 ontology features:
    - Certainty level detection for uncertain data
    - Attribution source tracking (catalog vs colophon)
    - Data category classification
    
    Args:
        record: Parsed MARC record
        
    Returns:
        ExtractedData object with all extracted information
    """
    data = ExtractedData()
    handlers = FieldHandlers()
    
    data.external_ids['nli'] = record.control_number
    
    # All data is catalog-derived by default (v1.4)
    data.set_attribution('record', 'CatalogAttribution')
    
    field_008 = record.get_field('008')
    if field_008:
        info_008 = handlers.handle_008(field_008)
        data.dates.update(info_008)
        if 'language' in info_008:
            data.languages.append(info_008['language'])
    
    for field in record.get_fields('041'):
        langs = handlers.handle_041(field)
        for lang in langs:
            if lang not in data.languages:
                data.languages.append(lang)
    
    for field in record.get_fields('100'):
        author = handlers.handle_100(field)
        if author.get('name'):
            # Add certainty metadata to author (v1.4)
            author['certainty'] = 'Probable'  # Author attribution is interpretive
            author['attribution_source'] = 'CatalogAttribution'
            data.authors.append(author)
    
    for field in record.get_fields('110'):
        org = handlers.handle_110(field)
        if org.get('name'):
            data.authors.append(org)
    
    field_245 = record.get_field('245')
    if field_245:
        title_info = handlers.handle_245(field_245)
        data.title = title_info.get('title')
        data.subtitle = title_info.get('subtitle')
    
    for field in record.get_fields('246'):
        variant = handlers.handle_246(field)
        if variant:
            data.variant_titles.append(variant)
    
    for tag in ['260', '264']:
        for field in record.get_fields(tag):
            production = handlers.handle_260_264(field)
            if production.get('place'):
                data.place = production['place']
                # Place attribution is typically interpretive (v1.4)
                data.set_certainty('place', 'Probable')
                data.set_attribution('place', 'CatalogAttribution')
            if production.get('parsed_date'):
                data.dates.update(production['parsed_date'])
                # Transfer certainty from date parsing (v1.4)
                if 'certainty' in production['parsed_date']:
                    data.set_certainty('date', production['parsed_date']['certainty'])
    
    field_300 = record.get_field('300')
    if field_300:
        physical = handlers.handle_300(field_300)
        data.extent = physical.get('folios')
        data.height_mm = physical.get('height_mm')
        data.width_mm = physical.get('width_mm')
        data.is_multi_volume = physical.get('is_multi_volume', False)
        data.volume_info = physical.get('volume_info')
        # Physical measurements are factual (v1.4)
        if data.extent:
            data.set_certainty('extent', 'Certain')
        if data.height_mm or data.width_mm:
            data.set_certainty('dimensions', 'Certain')
    
    for field in record.get_fields('340'):
        materials = handlers.handle_340(field)
        for mat in materials:
            if mat not in data.materials:
                data.materials.append(mat)
    
    for field in record.get_fields('500'):
        note_info = handlers.handle_500(field)
        if note_info.get('text'):
            data.notes.append(note_info['text'])
        if note_info.get('script_type'):
            data.script_type = note_info['script_type']
            data.set_certainty('script_type', 'Probable')
            data.set_attribution('script_type', 'PaleographicAttribution')
        if note_info.get('script_mode'):
            data.script_mode = note_info['script_mode']
            data.set_certainty('script_mode', 'Probable')
            data.set_attribution('script_mode', 'PaleographicAttribution')
        if note_info.get('colophon_text'):
            data.colophon_text = note_info['colophon_text']
            data.set_certainty('colophon_text', 'Certain')
            data.set_attribution('colophon_text', 'ColophonAttribution')
            data.mark_from_colophon('colophon_text')
        
        # v1.4: Extract codicological unit indicators
        if note_info.get('codicological_units'):
            data.codicological_units.extend(note_info['codicological_units'])
            # Check for anthology indicators
            for cu in note_info['codicological_units']:
                if cu.get('type') == 'anthology_indicator':
                    data.is_anthology = True
                    data.hierarchy_type = 'ComplexHierarchy'
        
        # v1.4: Extract scribal interventions
        if note_info.get('scribal_interventions'):
            data.scribal_interventions.extend(note_info['scribal_interventions'])
        
        # v1.4: Extract canonical references
        if note_info.get('canonical_references'):
            data.canonical_references.extend(note_info['canonical_references'])
    
    for field in record.get_fields('505'):
        contents = handlers.handle_505(field)
        for index, item in enumerate(contents, 1):
            if 'sequence' not in item:
                item['sequence'] = index
        data.contents.extend(contents)
    if len(data.contents) > 1:
        data.is_anthology = True
        if not data.hierarchy_type:
            data.hierarchy_type = 'ComplexHierarchy'
    
    for field in record.get_fields('510'):
        ref = handlers.handle_510(field)
        if ref.get('name'):
            data.catalog_references.append(ref)
    
    field_561 = record.get_field('561')
    if field_561:
        data.provenance = handlers.handle_561(field_561)
    
    field_563 = record.get_field('563')
    if field_563:
        data.binding_info = handlers.handle_563(field_563)
    
    for tag in ['600', '610', '650', '651', '655']:
        for field in record.get_fields(tag):
            subject = handlers.handle_6xx(field)
            if subject.get('term'):
                if tag == '655':
                    data.genres.append(subject['term'])
                else:
                    data.subjects.append(subject)
    
    for field in record.get_fields('700'):
        person = handlers.handle_700(field)
        if person.get('name'):
            data.contributors.append(person)
    
    for field in record.get_fields('710'):
        org = handlers.handle_110(field)
        if org.get('name'):
            data.contributors.append(org)
    
    for field in record.get_fields('856'):
        url_info = handlers.handle_856(field)
        if url_info.get('url'):
            data.digital_url = url_info['url']
    
    return data


