"""Main mapper that orchestrates MARC to RDF transformation."""

import logging
from collections.abc import Iterator
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

    def map_file(self, file_path: Path, output_path: Path | None = None) -> Graph:
        """Map all records from a file to a single RDF graph.

        Supports .mrc, .csv, .tsv, and .json (authority_enriched) formats.

        Args:
            file_path: Path to input file (.mrc, .csv, .tsv, or .json)
            output_path: Optional path to save the resulting TTL file

        Returns:
            Combined RDF graph for all records
        """
        combined_graph = Graph()

        from ..config.namespaces import bind_namespaces

        bind_namespaces(combined_graph)

        if file_path.suffix == ".json":
            # JSON input: authority_enriched.json from Stage 2
            import json

            raw = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError(f"Expected JSON array, got {type(raw).__name__}")
            combined_graph = self.map_json_records(raw)
            from ..config.namespaces import bind_namespaces

            bind_namespaces(combined_graph)
        else:
            # MARC/CSV/TSV input via UnifiedReader
            reader = UnifiedReader(file_path)
            for record in reader.read_file():
                try:
                    record_graph = self.map_record(record)
                    for triple in record_graph:
                        combined_graph.add(triple)
                except Exception as e:
                    self._mapping_errors.append(
                        f"Error mapping record {record.control_number}: {e!s}"
                    )

        if output_path:
            combined_graph.serialize(destination=str(output_path), format="turtle")

        return combined_graph

    def map_json_records(self, records: list[dict]) -> Graph:
        """Map authority-enriched JSON records directly to RDF.

        Skips extract_all_data() since JSON records are already extracted.
        Builds RDF graph from the dict fields directly.
        """
        combined_graph = Graph()
        from ..config.namespaces import bind_namespaces

        bind_namespaces(combined_graph)

        for rec in records:
            try:
                cn = str(rec.get("_control_number", f"json_{id(rec)}"))
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
