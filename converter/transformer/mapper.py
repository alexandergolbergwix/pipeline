"""Main mapper that orchestrates MARC to RDF transformation."""

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from rdflib import Graph

from ..parser.marc_reader import MarcRecord
from ..parser.unified_reader import UnifiedReader
from ..rdf.graph_builder import GraphBuilder
from .field_handlers import extract_all_data
from .uri_generator import UriGenerator

if TYPE_CHECKING:
    from ..authority.mazal_matcher import MazalMatcher

logger = logging.getLogger(__name__)


RAW_AUTHORITY_FILENAME = "authority_enriched.json"
REVIEWED_AUTHORITY_FILENAME = "authority_enriched_reviewed.json"
WIKIDATA_VERIFIED_SUFFIX = "_wikidata_verified.json"


def is_wikidata_verified_json(path: Path) -> bool:
    """Return True for Wikidata Studio review-state JSON files."""
    return path.name.endswith(WIKIDATA_VERIFIED_SUFFIX)


def select_rdf_source_path(input_path: Path) -> tuple[Path, str]:
    """Choose the safest/richest manuscript-source file for Stage 4 RDF.

    If the user points Stage 4 at ``authority_enriched.json`` and the
    user-reviewed sibling exists, the reviewed file is preferred.  A
    ``*_wikidata_verified.json`` file is already a Wikidata Studio review
    state, not manuscript-source data, and must never feed RDF construction.
    """
    if is_wikidata_verified_json(input_path):
        raise ValueError(
            f"{input_path.name} is a Wikidata Studio review file, not a "
            "manuscript-source file. Build RDF from authority_enriched.json "
            "or authority_enriched_reviewed.json instead."
        )
    if input_path.name == RAW_AUTHORITY_FILENAME:
        reviewed = input_path.with_name(REVIEWED_AUTHORITY_FILENAME)
        if reviewed.exists():
            return reviewed, "user-reviewed authority enriched"
        return input_path, "raw authority enriched"
    if input_path.name == REVIEWED_AUTHORITY_FILENAME:
        return input_path, "user-reviewed authority enriched"
    if input_path.suffix.lower() == ".json":
        return input_path, "raw authority enriched"
    return input_path, "direct MARC/CSV/TSV source"


def validate_rdf_json_records(raw: object, source_path: Path) -> list[dict]:
    """Validate JSON input shape before converting authority data to RDF."""
    if not isinstance(raw, list):
        raise ValueError(f"Expected JSON array, got {type(raw).__name__}")
    if raw and isinstance(raw[0], dict) and "item" in raw[0] and "validation" in raw[0]:
        raise ValueError(
            f"{source_path.name} is a Wikidata Studio review file. Use "
            "authority_enriched.json or authority_enriched_reviewed.json for RDF."
        )
    return raw


class MarcToRdfMapper:
    """Maps MARC records to RDF graphs using the Hebrew Manuscripts Ontology."""

    def __init__(
        self, uri_generator: UriGenerator | None = None, mazal_matcher: "MazalMatcher" = None
    ):
        """Initialize the mapper.

        Args:
            uri_generator: Optional custom URI generator
            mazal_matcher: Optional Mazal authority matcher for NLI URI resolution
        """
        self.mazal_matcher = mazal_matcher
        self.uri_generator = uri_generator or UriGenerator(mazal_matcher=mazal_matcher)
        self.graph_builder = GraphBuilder(self.uri_generator)
        self._records_mapped = 0
        self._mapping_errors: list[str] = []

    @property
    def records_mapped(self) -> int:
        """Number of records successfully mapped."""
        return self._records_mapped

    @property
    def mapping_errors(self) -> list[str]:
        """List of errors encountered during mapping."""
        return self._mapping_errors

    def map_record(self, record: MarcRecord) -> Graph:
        """Map a single MARC record to an RDF graph.

        Args:
            record: Parsed MARC record

        Returns:
            RDF graph representing the manuscript
        """
        extracted = extract_all_data(record)

        graph = self.graph_builder.build_graph(extracted, record.control_number)

        self._records_mapped += 1
        return graph

    def map_file(
        self,
        file_path: Path,
        output_path: Path | None = None,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> Graph:
        """Map all records from a file to a single RDF graph.

        Supports .mrc, .csv, .tsv, and .json (authority_enriched) formats.

        Args:
            file_path: Path to input file (.mrc, .csv, .tsv, or .json)
            output_path: Optional path to save the resulting TTL file
            progress_cb: Optional callback(i, total, control_number) invoked
                once per record. ``total`` may be 0 for streaming readers
                where the count is unknown until end-of-file.

        Returns:
            Combined RDF graph for all records
        """
        combined_graph = Graph()

        from ..config.namespaces import bind_namespaces

        bind_namespaces(combined_graph)

        source_path, _source_marker = select_rdf_source_path(file_path)

        if source_path.suffix == ".json":
            # JSON input: authority_enriched.json or authority_enriched_reviewed.json
            raw = json.loads(source_path.read_text(encoding="utf-8"))
            records = validate_rdf_json_records(raw, source_path)
            combined_graph = self.map_json_records(records, progress_cb=progress_cb)
            from ..config.namespaces import bind_namespaces

            bind_namespaces(combined_graph)
        else:
            # MARC/CSV/TSV input via UnifiedReader
            reader = UnifiedReader(source_path)
            for i, record in enumerate(reader.read_file()):
                try:
                    record_graph = self.map_record(record)
                    for triple in record_graph:
                        combined_graph.add(triple)
                except Exception as e:
                    self._mapping_errors.append(
                        f"Error mapping record {record.control_number}: {e!s}"
                    )
                if progress_cb is not None:
                    progress_cb(i + 1, 0, str(record.control_number))

        if output_path:
            combined_graph.serialize(destination=str(output_path), format="turtle")

        return combined_graph

    def map_json_records(
        self,
        records: list[dict],
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> Graph:
        """Map authority-enriched JSON records directly to RDF.

        Skips extract_all_data() since JSON records are already extracted.
        Builds RDF graph from the dict fields directly.
        """
        combined_graph = Graph()
        from ..config.namespaces import bind_namespaces

        bind_namespaces(combined_graph)

        total = len(records)
        for i, rec in enumerate(records):
            cn = str(rec.get("_control_number", f"json_{id(rec)}"))
            try:
                # Build ExtractedData from the JSON dict
                from converter.transformer.field_handlers import ExtractedData

                extracted = ExtractedData()
                for field_name in vars(extracted):
                    if field_name.startswith("_"):
                        continue
                    if field_name in rec:
                        setattr(extracted, field_name, rec[field_name])
                extracted.control_number = cn

                graph = self.graph_builder.build_graph(extracted, cn)
                for triple in graph:
                    combined_graph.add(triple)
                self._records_mapped += 1
            except Exception as e:
                self._mapping_errors.append(
                    f"Error mapping JSON record {rec.get('_control_number', '?')}: {e!s}"
                )
            if progress_cb is not None:
                progress_cb(i + 1, total, cn)

        return combined_graph

    def map_records(self, records: Iterator[MarcRecord]) -> Graph:
        """Map multiple MARC records to a single RDF graph.

        Args:
            records: Iterator of MARC records

        Returns:
            Combined RDF graph
        """
        combined_graph = Graph()

        from ..config.namespaces import bind_namespaces

        bind_namespaces(combined_graph)

        for record in records:
            try:
                record_graph = self.map_record(record)
                for triple in record_graph:
                    combined_graph.add(triple)
            except Exception as e:
                self._mapping_errors.append(
                    f"Error mapping record {record.control_number}: {str(e)}"
                )

        return combined_graph

    def reset_stats(self):
        """Reset mapping statistics."""
        self._records_mapped = 0
        self._mapping_errors = []
