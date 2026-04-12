"""MARC record reader using pymarc library."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pymarc


@dataclass
class MarcField:
    """Represents a single MARC field with its subfields."""

    tag: str
    indicators: tuple = ("", "")
    subfields: dict[str, list[str]] = field(default_factory=dict)
    data: str | None = None

    def get_subfield(self, code: str) -> str | None:
        """Get first value of a subfield."""
        values = self.subfields.get(code, [])
        return values[0] if values else None

    def get_all_subfields(self, code: str) -> list[str]:
        """Get all values of a subfield."""
        return self.subfields.get(code, [])


@dataclass
class MarcRecord:
    """Represents a parsed MARC record."""

    control_number: str
    fields: dict[str, list[MarcField]] = field(default_factory=dict)
    leader: str = ""

    def get_field(self, tag: str) -> MarcField | None:
        """Get first field by tag."""
        fields = self.fields.get(tag, [])
        return fields[0] if fields else None

    def get_fields(self, tag: str) -> list[MarcField]:
        """Get all fields by tag."""
        return self.fields.get(tag, [])

    def get_control_field(self, tag: str) -> str | None:
        """Get value of a control field (00X)."""
        field = self.get_field(tag)
        return field.data if field else None

    def get_fixed_field_value(self, tag: str, start: int, end: int) -> str | None:
        """Extract substring from a fixed-length field like 008."""
        data = self.get_control_field(tag)
        if data and len(data) >= end:
            return data[start:end]
        return None


class MarcReader:
    """Reader for MARC .mrc files."""

    def __init__(self, file_path: Path | None = None):
        """Initialize the MARC reader.

        Args:
            file_path: Optional path to .mrc file
        """
        self.file_path = file_path
        self._records_processed = 0
        self._errors: list[str] = []

    @property
    def records_processed(self) -> int:
        """Number of records successfully processed."""
        return self._records_processed

    @property
    def errors(self) -> list[str]:
        """List of errors encountered during parsing."""
        return self._errors

    def read_file(self, file_path: Path | None = None) -> Iterator[MarcRecord]:
        """Read and parse all records from a MARC file.

        Args:
            file_path: Path to .mrc file (overrides constructor path)

        Yields:
            MarcRecord objects for each record in the file
        """
        path = file_path or self.file_path
        if not path:
            raise ValueError("No file path provided")

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        self._records_processed = 0
        self._errors = []

        with open(path, "rb") as f:
            reader = pymarc.MARCReader(f, to_unicode=True, force_utf8=True)
            for record in reader:
                if record is None:
                    continue
                try:
                    marc_record = self._parse_record(record)
                    if marc_record:
                        self._records_processed += 1
                        yield marc_record
                except Exception as e:
                    self._errors.append(f"Error parsing record: {str(e)}")

    def _parse_record(self, record: pymarc.Record) -> MarcRecord | None:
        """Convert pymarc Record to our MarcRecord format.

        Args:
            record: pymarc Record object

        Returns:
            MarcRecord object or None if parsing fails
        """
        control_number = record["001"].data if record["001"] else "unknown"

        marc_record = MarcRecord(
            control_number=control_number, leader=str(record.leader) if record.leader else ""
        )

        for field in record.get_fields():
            tag = field.tag

            if tag.startswith("00"):
                marc_field = MarcField(
                    tag=tag, data=field.data if hasattr(field, "data") else str(field)
                )
            else:
                subfields_dict: dict[str, list[str]] = {}
                if hasattr(field, "subfields"):
                    for sf in field.subfields:
                        if hasattr(sf, "code") and hasattr(sf, "value"):
                            code = sf.code
                            value = sf.value
                        else:
                            continue
                        if code not in subfields_dict:
                            subfields_dict[code] = []
                        subfields_dict[code].append(value)

                indicators = ("", "")
                if hasattr(field, "indicators"):
                    indicators = tuple(field.indicators)
                elif hasattr(field, "indicator1") and hasattr(field, "indicator2"):
                    indicators = (field.indicator1 or "", field.indicator2 or "")

                marc_field = MarcField(tag=tag, indicators=indicators, subfields=subfields_dict)

            if tag not in marc_record.fields:
                marc_record.fields[tag] = []
            marc_record.fields[tag].append(marc_field)

        return marc_record

    def count_records(self, file_path: Path | None = None) -> int:
        """Count the number of records in a MARC file without fully parsing.

        Args:
            file_path: Path to .mrc file

        Returns:
            Number of records in the file
        """
        path = file_path or self.file_path
        if not path:
            raise ValueError("No file path provided")

        count = 0
        with open(path, "rb") as f:
            reader = pymarc.MARCReader(f)
            for _ in reader:
                count += 1
        return count


def read_marc_file(file_path: Path) -> Iterator[MarcRecord]:
    """Convenience function to read a MARC file.

    Args:
        file_path: Path to .mrc file

    Yields:
        MarcRecord objects
    """
    reader = MarcReader(file_path)
    yield from reader.read_file()
