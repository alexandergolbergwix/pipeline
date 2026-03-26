"""Unified reader that handles multiple input formats (.mrc, .csv, .tsv)."""

from pathlib import Path
from typing import Iterator, Optional, List, Literal
from enum import Enum

from .marc_reader import MarcReader, MarcRecord
from .csv_reader import CsvTsvReader


class FileFormat(Enum):
    """Supported input file formats."""
    MRC = "mrc"
    CSV = "csv"
    TSV = "tsv"
    UNKNOWN = "unknown"


def detect_file_format(file_path: Path) -> FileFormat:
    """Detect file format based on extension and content.
    
    Args:
        file_path: Path to the input file
        
    Returns:
        Detected FileFormat
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    
    if suffix == '.mrc':
        return FileFormat.MRC
    elif suffix == '.csv':
        return FileFormat.CSV
    elif suffix == '.tsv':
        return FileFormat.TSV
    
    # Try to detect from content
    try:
        with open(path, 'rb') as f:
            first_bytes = f.read(100)
            # MARC files start with a 5-digit record length
            if first_bytes[:5].isdigit():
                return FileFormat.MRC
    except:
        pass
    
    # Try reading as text
    try:
        with open(path, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            if '\t' in first_line:
                return FileFormat.TSV
            elif ',' in first_line:
                return FileFormat.CSV
    except:
        pass
    
    return FileFormat.UNKNOWN


class UnifiedReader:
    """Unified reader that automatically handles .mrc, .csv, and .tsv files."""
    
    def __init__(self, file_path: Optional[Path] = None,
                 format_hint: Optional[FileFormat] = None):
        """Initialize the unified reader.
        
        Args:
            file_path: Path to input file
            format_hint: Optional format hint to override auto-detection
        """
        self.file_path = Path(file_path) if file_path else None
        self.format_hint = format_hint
        self._internal_reader = None
        self._detected_format: Optional[FileFormat] = None
        self._records_processed = 0
        self._errors: List[str] = []
    
    @property
    def detected_format(self) -> Optional[FileFormat]:
        """The detected or specified file format."""
        return self._detected_format
    
    @property
    def records_processed(self) -> int:
        """Number of records successfully processed."""
        return self._records_processed
    
    @property
    def errors(self) -> List[str]:
        """List of errors encountered during parsing."""
        return self._errors
    
    def _create_reader(self, path: Path):
        """Create the appropriate reader for the file format.
        
        Args:
            path: Path to the file
        """
        self._detected_format = self.format_hint or detect_file_format(path)
        
        if self._detected_format == FileFormat.MRC:
            self._internal_reader = MarcReader(path)
        elif self._detected_format in (FileFormat.CSV, FileFormat.TSV):
            delimiter = '\t' if self._detected_format == FileFormat.TSV else ','
            self._internal_reader = CsvTsvReader(path, delimiter=delimiter)
        else:
            # Default to trying as CSV
            self._internal_reader = CsvTsvReader(path)
            self._detected_format = FileFormat.CSV
    
    def read_file(self, file_path: Optional[Path] = None) -> Iterator[MarcRecord]:
        """Read and parse all records from the input file.
        
        Automatically detects file format and uses appropriate parser.
        
        Args:
            file_path: Path to file (overrides constructor path)
            
        Yields:
            MarcRecord objects for each record in the file
        """
        path = Path(file_path) if file_path else self.file_path
        if not path:
            raise ValueError("No file path provided")
        
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        self._records_processed = 0
        self._errors = []
        
        self._create_reader(path)
        
        for record in self._internal_reader.read_file():
            self._records_processed += 1
            yield record
        
        # Collect errors from internal reader
        if hasattr(self._internal_reader, 'errors'):
            self._errors.extend(self._internal_reader.errors)
    
    def count_records(self, file_path: Optional[Path] = None) -> int:
        """Count records in the file without full parsing.
        
        Args:
            file_path: Path to file
            
        Returns:
            Number of records
        """
        path = Path(file_path) if file_path else self.file_path
        if not path:
            raise ValueError("No file path provided")
        
        self._create_reader(path)
        return self._internal_reader.count_records()
    
    @staticmethod
    def get_supported_extensions() -> List[str]:
        """Get list of supported file extensions.
        
        Returns:
            List of extension strings
        """
        return ['.mrc', '.csv', '.tsv']
    
    @staticmethod
    def get_file_filter() -> str:
        """Get file filter string for file dialogs.
        
        Returns:
            Filter string for QFileDialog
        """
        return (
            "All Supported Files (*.mrc *.csv *.tsv);;"
            "MARC Files (*.mrc);;"
            "CSV Files (*.csv);;"
            "TSV Files (*.tsv);;"
            "All Files (*.*)"
        )


def read_file(file_path: Path) -> Iterator[MarcRecord]:
    """Convenience function to read any supported file format.
    
    Args:
        file_path: Path to input file (.mrc, .csv, or .tsv)
        
    Yields:
        MarcRecord objects
    """
    reader = UnifiedReader(file_path)
    yield from reader.read_file()


