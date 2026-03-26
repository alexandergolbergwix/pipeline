"""CSV/TSV record reader for tabular MARC-like data."""

import csv
from pathlib import Path
from typing import Iterator, Optional, List, Dict, Any
from dataclasses import dataclass, field

from .marc_reader import MarcField, MarcRecord


# Default column mappings for common CSV export formats
DEFAULT_COLUMN_MAPPINGS = {
    # NLI export format columns - maps various column names to standard field names
    'control_number': ['001', 'control_number', 'id', 'record_id', 'מספר_שליטה'],
    'title': ['245$a', '245', 'title', 'כותרת'],
    'subtitle': ['245$b'],
    'author': ['100$a', '100', 'author', 'מחבר'],
    'author_dates': ['100$d'],
    'date': ['260$c', '264$c', 'date', 'תאריך', 'production_date'],
    'place': ['260$a', '264$a', 'place', 'מקום', 'production_place'],
    'language': ['041$a', '041', '008/35-37', 'language', 'שפה', 'lang'],
    'extent': ['300$a', 'extent', 'היקף', 'folios', 'pages'],
    'dimensions': ['300$c', 'dimensions', 'מידות', 'size'],
    'material': ['340$a', 'material', 'חומר', 'support'],
    'notes': ['500$a', '500', 'notes', 'הערות', 'note'],
    'contents': ['505$a', '505', 'contents', 'תוכן', 'content'],
    'provenance': ['561$a', '561', 'provenance', 'פרובננס', 'ownership'],
    'digital_url': ['856$u', 'url', 'digital_url', 'קישור'],
    'subject': ['650$a', '650', 'subject', 'נושא', 'subjects'],
    'genre': ['655$a', '655', 'genre', 'ז\'אנר', 'form'],
    'catalog_ref': ['510$a', '510', 'catalog', 'קטלוג', 'reference'],
    'call_number': ['090$a', '090', 'call_number', 'סימן_קריאה', 'shelfmark'],
    'contributor': ['700$a', '700'],
    'contributor_role': ['700$e'],
    'corporate': ['710$a', '710'],
    'place_subject': ['651$a', '651'],
}

# Direct MARC field tag patterns (for columns like "245$a", "100$d", etc.)
MARC_TAG_PATTERN = r'^(\d{3})(\$([a-z0-9]))?$'


@dataclass
class ColumnMapping:
    """Mapping configuration for CSV columns to MARC-like fields."""
    mappings: Dict[str, str] = field(default_factory=dict)
    # Store direct MARC tag mappings for columns like "245$a"
    marc_tags: Dict[str, tuple] = field(default_factory=dict)
    
    @classmethod
    def from_header(cls, headers: List[str]) -> 'ColumnMapping':
        """Create mapping from CSV headers by matching known patterns.
        
        Args:
            headers: List of column headers from CSV
            
        Returns:
            ColumnMapping instance
        """
        import re
        mappings = {}
        marc_tags = {}
        
        for header in headers:
            header_clean = header.strip()
            header_lower = header_clean.lower()
            
            # Check if it's a direct MARC tag (e.g., "245$a", "001", "100$d")
            marc_match = re.match(r'^(\d{3})(\$([a-z0-9]))?$', header_clean)
            if marc_match:
                tag = marc_match.group(1)
                subfield = marc_match.group(3) if marc_match.group(3) else 'a'
                marc_tags[header] = (tag, subfield)
                continue
            
            # Check against known column name mappings
            for field_name, possible_names in DEFAULT_COLUMN_MAPPINGS.items():
                for possible in possible_names:
                    if possible.lower() == header_lower:
                        mappings[header] = field_name
                        break
                if header in mappings:
                    break
        
        return cls(mappings=mappings, marc_tags=marc_tags)
    
    def get_field_name(self, column: str) -> Optional[str]:
        """Get the standardized field name for a column."""
        return self.mappings.get(column)
    
    def get_marc_tag(self, column: str) -> Optional[tuple]:
        """Get the MARC tag and subfield for a column.
        
        Returns:
            Tuple of (tag, subfield) or None
        """
        return self.marc_tags.get(column)


class CsvTsvReader:
    """Reader for CSV/TSV files containing manuscript data."""
    
    # Encodings to try in order of likelihood for Hebrew content
    ENCODINGS_TO_TRY = ['utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'cp1255', 'iso-8859-8', 'cp862', 'latin-1']
    
    def __init__(self, file_path: Optional[Path] = None, 
                 delimiter: Optional[str] = None,
                 column_mapping: Optional[ColumnMapping] = None,
                 encoding: Optional[str] = None):
        """Initialize the CSV/TSV reader.
        
        Args:
            file_path: Optional path to CSV/TSV file
            delimiter: Field delimiter (',' for CSV, '\t' for TSV). Auto-detected if None.
            column_mapping: Optional custom column mapping
            encoding: File encoding. Auto-detected if None.
        """
        self.file_path = file_path
        self.delimiter = delimiter
        self.column_mapping = column_mapping
        self.encoding = encoding
        self._detected_encoding: Optional[str] = None
        self._records_processed = 0
        self._errors: List[str] = []
    
    @property
    def records_processed(self) -> int:
        """Number of records successfully processed."""
        return self._records_processed
    
    @property
    def errors(self) -> List[str]:
        """List of errors encountered during parsing."""
        return self._errors
    
    def _detect_encoding(self, file_path: Path) -> str:
        """Auto-detect the file encoding.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Detected encoding string
        """
        # Try chardet first if available
        try:
            import chardet
            with open(file_path, 'rb') as f:
                raw = f.read(10000)
                result = chardet.detect(raw)
                if result['encoding'] and result['confidence'] > 0.7:
                    return result['encoding']
        except ImportError:
            pass
        
        # Try each encoding
        for encoding in self.ENCODINGS_TO_TRY:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    content = f.read(5000)
                    # Check if content has Hebrew characters (indicates correct encoding)
                    has_hebrew = any(0x0590 <= ord(c) <= 0x05FF for c in content)
                    if has_hebrew:
                        return encoding
                    # If no Hebrew but readable, keep as candidate
                return encoding
            except (UnicodeDecodeError, UnicodeError, LookupError):
                continue
        
        # Fallback to latin-1 which accepts all bytes
        return 'latin-1'
    
    def _detect_delimiter(self, file_path: Path, encoding: str) -> str:
        """Auto-detect the delimiter based on file extension and content.
        
        Args:
            file_path: Path to the file
            encoding: File encoding to use
            
        Returns:
            Detected delimiter character
        """
        suffix = file_path.suffix.lower()
        if suffix == '.tsv':
            return '\t'
        elif suffix == '.csv':
            return ','
        
        # Try to detect from content
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                first_line = f.readline()
                if '\t' in first_line:
                    return '\t'
        except:
            pass
        return ','
    
    def read_file(self, file_path: Optional[Path] = None) -> Iterator[MarcRecord]:
        """Read and parse all records from a CSV/TSV file.
        
        Args:
            file_path: Path to CSV/TSV file (overrides constructor path)
            
        Yields:
            MarcRecord objects for each row in the file
        """
        path = file_path or self.file_path
        if not path:
            raise ValueError("No file path provided")
        
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        self._records_processed = 0
        self._errors = []
        
        # Detect encoding
        encoding = self.encoding or self._detect_encoding(path)
        self._detected_encoding = encoding
        
        delimiter = self.delimiter or self._detect_delimiter(path, encoding)
        
        with open(path, 'r', encoding=encoding, errors='replace') as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            
            # Create column mapping from headers if not provided
            if not self.column_mapping and reader.fieldnames:
                self.column_mapping = ColumnMapping.from_header(list(reader.fieldnames))
            
            row_num = 1
            for row in reader:
                row_num += 1
                try:
                    marc_record = self._parse_row(row, row_num)
                    if marc_record:
                        self._records_processed += 1
                        yield marc_record
                except Exception as e:
                    self._errors.append(f"Error parsing row {row_num}: {str(e)}")
    
    def _parse_row(self, row: Dict[str, str], row_num: int) -> Optional[MarcRecord]:
        """Convert a CSV row to MarcRecord format.
        
        Args:
            row: Dictionary of column->value from CSV
            row_num: Row number for generating control number
            
        Returns:
            MarcRecord object or None if parsing fails
        """
        # Find control number - check both mapped name and direct 001 column
        control_number = None
        for col, value in row.items():
            if not value or not value.strip():
                continue
            col_clean = col.strip()
            # Check if it's the 001 field directly
            if col_clean == '001':
                control_number = value.strip().strip('"')
                break
            # Check mapped field names
            field_name = self.column_mapping.get_field_name(col) if self.column_mapping else None
            if field_name == 'control_number':
                control_number = value.strip().strip('"')
                break
        
        if not control_number:
            control_number = f"csv_row_{row_num}"
        
        marc_record = MarcRecord(control_number=control_number)
        
        # Map each column to MARC-like fields
        for col, value in row.items():
            if not value or not value.strip():
                continue
            
            # Clean value (remove surrounding quotes common in exports)
            value = value.strip().strip('"')
            if not value:
                continue
            
            # Check if this column is a direct MARC tag (e.g., "245$a")
            marc_tag_info = self.column_mapping.get_marc_tag(col) if self.column_mapping else None
            
            if marc_tag_info:
                tag, subfield = marc_tag_info
                marc_fields = self._create_marc_field_direct(tag, subfield, value)
            else:
                # Try mapped field name
                field_name = self.column_mapping.get_field_name(col) if self.column_mapping else col
                if not field_name:
                    field_name = col
                result = self._create_marc_field(field_name, value, col)
                marc_fields = [result] if result else []
            
            for marc_field in marc_fields:
                if marc_field:
                    tag = marc_field.tag
                    if tag not in marc_record.fields:
                        marc_record.fields[tag] = []
                    marc_record.fields[tag].append(marc_field)
        
        return marc_record
    
    def _create_marc_field_direct(self, tag: str, subfield: str, value: str) -> List[MarcField]:
        """Create MarcField(s) from direct MARC tag and subfield.
        
        Handles pipe-separated values by creating multiple fields.
        
        Args:
            tag: MARC tag (e.g., "245")
            subfield: Subfield code (e.g., "a")
            value: Field value (may contain | separators for multiple values)
            
        Returns:
            List of MarcField objects
        """
        # Control fields (00X) have data, not subfields
        if tag.startswith('00'):
            return [MarcField(tag=tag, data=value)]
        
        # Split pipe-separated values into multiple fields
        # This is common in NLI CSV exports for repeatable fields like 700$a
        if '|' in value:
            values = [v.strip() for v in value.split('|') if v.strip()]
        else:
            values = [value]
        
        return [
            MarcField(
                tag=tag,
                indicators=(' ', ' '),
                subfields={subfield: [v]}
            )
            for v in values
        ]
    
    def _create_marc_field(self, field_name: str, value: str, 
                           original_col: str) -> Optional[MarcField]:
        """Create a MarcField from a field name and value.
        
        Args:
            field_name: Standardized field name
            value: Field value
            original_col: Original column name
            
        Returns:
            MarcField object
        """
        # Map field names to MARC tags
        field_tag_map = {
            'control_number': '001',
            'title': '245',
            'author': '100',
            'date': '260',
            'place': '260',
            'language': '041',
            'extent': '300',
            'dimensions': '300',
            'material': '340',
            'notes': '500',
            'contents': '505',
            'provenance': '561',
            'digital_url': '856',
            'subject': '650',
            'genre': '655',
            'catalog_ref': '510',
            'call_number': '090',
        }
        
        # Map field names to subfield codes
        subfield_map = {
            'title': 'a',
            'author': 'a',
            'date': 'c',
            'place': 'a',
            'language': 'a',
            'extent': 'a',
            'dimensions': 'c',
            'material': 'a',
            'notes': 'a',
            'contents': 'a',
            'provenance': 'a',
            'digital_url': 'u',
            'subject': 'a',
            'genre': 'a',
            'catalog_ref': 'a',
            'call_number': 'a',
        }
        
        tag = field_tag_map.get(field_name)
        if not tag:
            # Try to extract tag from field name like "245$a" or just "245"
            if '$' in field_name:
                parts = field_name.split('$')
                tag = parts[0]
            elif field_name.isdigit() and len(field_name) == 3:
                tag = field_name
            else:
                # Use 500 (general note) for unknown fields
                tag = '500'
        
        # Control fields (00X) have data, not subfields
        if tag.startswith('00'):
            return MarcField(tag=tag, data=value)
        
        # Get subfield code
        subfield_code = subfield_map.get(field_name, 'a')
        if '$' in field_name:
            parts = field_name.split('$')
            if len(parts) > 1 and parts[1]:
                subfield_code = parts[1]
        
        return MarcField(
            tag=tag,
            indicators=(' ', ' '),
            subfields={subfield_code: [value]}
        )
    
    def count_records(self, file_path: Optional[Path] = None) -> int:
        """Count the number of records in a CSV/TSV file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Number of data rows in the file
        """
        path = file_path or self.file_path
        if not path:
            raise ValueError("No file path provided")
        
        path = Path(path)
        encoding = self.encoding or self._detect_encoding(path)
        delimiter = self.delimiter or self._detect_delimiter(path, encoding)
        
        count = 0
        with open(path, 'r', encoding=encoding, errors='replace') as f:
            reader = csv.reader(f, delimiter=delimiter)
            next(reader, None)  # Skip header
            for _ in reader:
                count += 1
        return count


def read_csv_file(file_path: Path) -> Iterator[MarcRecord]:
    """Convenience function to read a CSV file.
    
    Args:
        file_path: Path to CSV file
        
    Yields:
        MarcRecord objects
    """
    reader = CsvTsvReader(file_path)
    yield from reader.read_file()


def read_tsv_file(file_path: Path) -> Iterator[MarcRecord]:
    """Convenience function to read a TSV file.
    
    Args:
        file_path: Path to TSV file
        
    Yields:
        MarcRecord objects
    """
    reader = CsvTsvReader(file_path, delimiter='\t')
    yield from reader.read_file()

