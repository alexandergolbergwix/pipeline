"""Mazal Authority Index Builder.

Parses NLI authority XML files and builds a SQLite database for fast lookups.
Supports indexing of persons, places, corporate bodies, and works/titles.
"""

import gzip
import logging
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


class MazalIndex:
    """SQLite-based index for Mazal authority records."""

    # MARC field tags for different entity types
    PERSON_TAGS = ("100", "400")  # Main + variant forms
    PLACE_TAGS = ("151", "451")
    CORPORATE_TAGS = ("110", "410")
    WORK_TAGS = ("130", "430")

    def __init__(self, db_path: str):
        """Initialize the index.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Get database connection (lazy initialization)."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @staticmethod
    def normalize_name(text: str) -> str:
        """Normalize a name for index lookup.

        - Removes diacritics/niqqud
        - Lowercases
        - Removes punctuation
        - Collapses whitespace

        Args:
            text: Input name

        Returns:
            Normalized name for matching
        """
        if not text:
            return ""

        text = text.strip()

        # Remove Hebrew niqqud (vowel points)
        text = "".join(
            char
            for char in text
            if not (0x0591 <= ord(char) <= 0x05C7 and ord(char) not in range(0x05D0, 0x05EB))
        )

        # Normalize Unicode and remove combining diacritics
        text = unicodedata.normalize("NFD", text)
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")

        # Remove punctuation except hyphens
        text = re.sub(r"[^\w\s\u0590-\u05FF-]", "", text)

        # Collapse whitespace and convert to lowercase
        text = re.sub(r"\s+", " ", text).strip().lower()

        return text

    def create_schema(self):
        """Create database tables."""
        cursor = self.conn.cursor()

        # Main authority records table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS authorities (
                nli_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                preferred_name_heb TEXT,
                preferred_name_lat TEXT,
                dates TEXT,
                aleph_id TEXT
            )
        """)

        # Name variants index (for fuzzy matching)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS name_index (
                normalized_name TEXT NOT NULL,
                nli_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                script TEXT,
                FOREIGN KEY (nli_id) REFERENCES authorities(nli_id)
            )
        """)

        # Create indexes for fast lookup
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_name_normalized 
            ON name_index(normalized_name, entity_type)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_name_entity_type 
            ON name_index(entity_type, normalized_name)
        """)

        self.conn.commit()

    def insert_authority(
        self,
        nli_id: str,
        entity_type: str,
        preferred_name_heb: str = None,
        preferred_name_lat: str = None,
        dates: str = None,
        aleph_id: str = None,
    ):
        """Insert an authority record."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO authorities 
            (nli_id, entity_type, preferred_name_heb, preferred_name_lat, dates, aleph_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (nli_id, entity_type, preferred_name_heb, preferred_name_lat, dates, aleph_id),
        )

    def insert_name_variant(
        self, normalized_name: str, nli_id: str, entity_type: str, script: str = None
    ):
        """Insert a name variant into the index."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO name_index (normalized_name, nli_id, entity_type, script)
            VALUES (?, ?, ?, ?)
        """,
            (normalized_name, nli_id, entity_type, script),
        )

    def lookup(self, name: str, entity_type: str) -> str | None:
        """Look up an NLI authority ID by name.

        Args:
            name: Name to search for
            entity_type: Type of entity ('person', 'place', 'corporate', 'work')

        Returns:
            NLI authority ID or None if not found
        """
        normalized = self.normalize_name(name)
        if not normalized:
            return None

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT nli_id FROM name_index
            WHERE normalized_name = ? AND entity_type = ?
            LIMIT 1
        """,
            (normalized, entity_type),
        )

        row = cursor.fetchone()
        return row["nli_id"] if row else None

    def lookup_person(self, name: str, dates: str = None) -> str | None:
        """Look up a person by name, optionally with dates for disambiguation.

        Args:
            name: Person's name
            dates: Optional date string (e.g., "1138-1204")

        Returns:
            NLI authority ID or None
        """
        normalized = self.normalize_name(name)
        if not normalized:
            return None

        cursor = self.conn.cursor()

        if dates:
            # Try exact match with dates first
            cursor.execute(
                """
                SELECT a.nli_id FROM authorities a
                JOIN name_index n ON a.nli_id = n.nli_id
                WHERE n.normalized_name = ? 
                AND n.entity_type = 'person'
                AND a.dates = ?
                LIMIT 1
            """,
                (normalized, dates),
            )
            row = cursor.fetchone()
            if row:
                return row["nli_id"]

        # Fall back to name-only match
        return self.lookup(name, "person")

    def lookup_place(self, name: str) -> str | None:
        """Look up a place by name."""
        return self.lookup(name, "place")

    def lookup_work(self, title: str) -> str | None:
        """Look up a work by title."""
        return self.lookup(title, "work")

    def lookup_corporate(self, name: str) -> str | None:
        """Look up a corporate body by name."""
        return self.lookup(name, "corporate")

    def get_record(self, nli_id: str) -> dict | None:
        """Get full authority record by NLI ID.

        Args:
            nli_id: NLI authority identifier

        Returns:
            Dictionary with record data or None
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM authorities WHERE nli_id = ?
        """,
            (nli_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_stats(self) -> dict:
        """Get index statistics."""
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) as total FROM authorities")
        total = cursor.fetchone()["total"]

        cursor.execute("""
            SELECT entity_type, COUNT(*) as count 
            FROM authorities GROUP BY entity_type
        """)
        by_type = {row["entity_type"]: row["count"] for row in cursor.fetchall()}

        cursor.execute("SELECT COUNT(*) as total FROM name_index")
        name_variants = cursor.fetchone()["total"]

        return {"total_records": total, "by_type": by_type, "name_variants": name_variants}


def parse_xml_records(xml_path: str) -> Iterator[tuple[str, dict]]:
    """Parse MARC XML authority records from a file.

    Args:
        xml_path: Path to XML file (can be .xml or .xml.gz)

    Yields:
        Tuples of (nli_id, record_data)
    """
    # Handle gzipped files
    if xml_path.endswith(".gz"):
        file_handle = gzip.open(xml_path, "rt", encoding="utf-8")
    else:
        file_handle = open(xml_path, encoding="utf-8")

    try:
        # Use iterparse for memory efficiency
        context = ET.iterparse(file_handle, events=("end",))

        for _event, elem in context:
            if elem.tag == "record":
                record = parse_record(elem)
                if record and record.get("nli_id"):
                    yield record["nli_id"], record
                elem.clear()
    finally:
        file_handle.close()


def parse_record(record_elem: ET.Element) -> dict | None:
    """Parse a single MARC authority record.

    Args:
        record_elem: XML element for the record

    Returns:
        Dictionary with parsed record data
    """
    result = {
        "nli_id": None,
        "entity_type": None,
        "names_heb": [],
        "names_lat": [],
        "dates": None,
        "aleph_id": None,
    }

    # Extract control field 001 (NLI ID)
    for cf in record_elem.findall("controlfield"):
        if cf.get("tag") == "001":
            result["nli_id"] = cf.text
            break

    if not result["nli_id"]:
        return None

    # Process data fields
    for df in record_elem.findall("datafield"):
        tag = df.get("tag")

        # Determine entity type and extract names
        if tag in ("100", "400"):
            result["entity_type"] = "person"
            name, dates, script = extract_name_from_field(df)
            if name:
                if script == "heb":
                    result["names_heb"].append(name)
                else:
                    result["names_lat"].append(name)
            if dates and not result["dates"]:
                result["dates"] = dates

        elif tag in ("110", "410"):
            result["entity_type"] = "corporate"
            name, _, script = extract_name_from_field(df)
            if name:
                if script == "heb":
                    result["names_heb"].append(name)
                else:
                    result["names_lat"].append(name)

        elif tag in ("130", "430"):
            result["entity_type"] = "work"
            name, _, script = extract_name_from_field(df, subfield="a")
            if name:
                if script == "heb":
                    result["names_heb"].append(name)
                else:
                    result["names_lat"].append(name)

        elif tag in ("151", "451"):
            result["entity_type"] = "place"
            name, _, script = extract_name_from_field(df)
            if name:
                if script == "heb":
                    result["names_heb"].append(name)
                else:
                    result["names_lat"].append(name)

        elif tag == "035":
            # Aleph ID
            for sf in df.findall("subfield"):
                if sf.get("code") == "a" and sf.text:
                    result["aleph_id"] = sf.text
                    break

    return result if result["entity_type"] else None


def extract_name_from_field(df: ET.Element, subfield: str = "a") -> tuple[str, str, str]:
    """Extract name, dates, and script from a MARC data field.

    Args:
        df: datafield XML element
        subfield: Subfield code to extract (default 'a')

    Returns:
        Tuple of (name, dates, script)
    """
    name = None
    dates = None
    script = "lat"  # Default to Latin

    for sf in df.findall("subfield"):
        code = sf.get("code")
        if code == subfield and sf.text:
            name = sf.text.rstrip(",").strip()
        elif code == "d" and sf.text:
            dates = sf.text.rstrip(".").strip()
        elif code == "9" and sf.text:
            script = sf.text

    return name, dates, script


def build_index(input_dir: str, output_db: str, verbose: bool = True) -> MazalIndex:
    """Build a Mazal authority index from XML files.

    Args:
        input_dir: Directory containing NLI authority XML files
        output_db: Path for the output SQLite database
        verbose: Whether to print progress information

    Returns:
        MazalIndex instance
    """
    input_path = Path(input_dir)

    # Find all XML files (prefer uncompressed for speed)
    xml_files = sorted(input_path.glob("NLIAUT*.xml"))
    if not xml_files:
        # Fall back to compressed files
        xml_files = sorted(input_path.glob("NLIAUT*.xml.gz"))

    if not xml_files:
        raise ValueError(f"No NLIAUT*.xml files found in {input_dir}")

    if verbose:
        logger.info(f"Found {len(xml_files)} authority files to process")

    # Create index
    index = MazalIndex(output_db)
    index.create_schema()

    total_records = 0
    total_names = 0

    for xml_file in xml_files:
        if verbose:
            logger.info(f"Processing {xml_file.name}...")

        file_records = 0

        for nli_id, record in parse_xml_records(str(xml_file)):
            # Insert authority record
            index.insert_authority(
                nli_id=nli_id,
                entity_type=record["entity_type"],
                preferred_name_heb=record["names_heb"][0] if record["names_heb"] else None,
                preferred_name_lat=record["names_lat"][0] if record["names_lat"] else None,
                dates=record["dates"],
                aleph_id=record["aleph_id"],
            )

            # Index all name variants
            for name in record["names_heb"]:
                normalized = MazalIndex.normalize_name(name)
                if normalized:
                    index.insert_name_variant(normalized, nli_id, record["entity_type"], "heb")
                    total_names += 1

            for name in record["names_lat"]:
                normalized = MazalIndex.normalize_name(name)
                if normalized:
                    index.insert_name_variant(normalized, nli_id, record["entity_type"], "lat")
                    total_names += 1

            file_records += 1
            total_records += 1

            # Commit periodically
            if total_records % 10000 == 0:
                index.conn.commit()
                if verbose:
                    logger.info(f"  Processed {total_records:,} records...")

        if verbose:
            logger.info(f"  {xml_file.name}: {file_records:,} records")

    # Final commit
    index.conn.commit()

    if verbose:
        stats = index.get_stats()
        logger.info("Index complete:")
        logger.info(f"  Total records: {stats['total_records']:,}")
        logger.info(f"  Name variants: {stats['name_variants']:,}")
        for etype, count in stats["by_type"].items():
            logger.info(f"  {etype}: {count:,}")

    return index
