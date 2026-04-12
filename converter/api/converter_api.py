"""
Clean API for native app integration.

This module provides a simple, JSON-serializable interface for calling
the MARC/CSV to TTL conversion logic from native apps (SwiftUI, WinUI 3).

All functions return dictionaries that can be easily serialized to JSON
for cross-language communication.

Features:
- MARC/CSV/TSV to TTL conversion
- SHACL validation
- Scholarly annotation import (certainty, text traditions, scribal interventions, etc.)
"""

import json
import traceback
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# Version info
API_VERSION = "1.2.0"
ONTOLOGY_VERSION = "1.5"


class InputFormat(Enum):
    """Supported input formats."""

    MARC = "marc"
    CSV = "csv"
    TSV = "tsv"
    UNKNOWN = "unknown"


@dataclass
class ConversionResult:
    """Result of a conversion operation."""

    success: bool
    records_processed: int
    ttl_content: str
    output_path: str
    validation_passed: bool | None
    validation_report: str
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def get_version() -> dict[str, Any]:
    """
    Get API and ontology version information.

    Returns:
        Dictionary with version info including Mazal availability
    """
    # Check if Mazal index is available
    try:
        from ..authority.mazal_matcher import create_matcher

        matcher = create_matcher()
        mazal_available = matcher is not None
        if matcher:
            mazal_stats = matcher.index.get_stats() if matcher.is_available else None
            matcher.close()
        else:
            mazal_stats = None
    except:
        mazal_available = False
        mazal_stats = None

    return {
        "api_version": API_VERSION,
        "ontology_version": ONTOLOGY_VERSION,
        "python_package": "hebrew-manuscripts-converter",
        "mazal_authority_enabled": mazal_available,
        "mazal_records": mazal_stats["total_records"] if mazal_stats else 0,
    }


def get_supported_formats() -> dict[str, list[str]]:
    """
    Get list of supported input and output formats.

    Returns:
        Dictionary with supported formats
    """
    return {
        "input_formats": ["marc", "mrc", "csv", "tsv"],
        "output_formats": ["ttl", "turtle"],
        "file_extensions": {"marc": [".mrc", ".marc"], "csv": [".csv"], "tsv": [".tsv"]},
    }


def detect_format(input_path: str) -> str:
    """
    Detect the format of an input file.

    Args:
        input_path: Path to input file

    Returns:
        Format string: "marc", "csv", "tsv", or "unknown"
    """
    path = Path(input_path)
    suffix = path.suffix.lower()

    if suffix in [".mrc", ".marc"]:
        return "marc"
    elif suffix == ".csv":
        return "csv"
    elif suffix == ".tsv":
        return "tsv"
    else:
        return "unknown"


def convert_file(
    input_path: str,
    output_path: str | None = None,
    include_ontology: bool = False,
    validate: bool = True,
    progress_callback: callable | None = None,
) -> dict[str, Any]:
    """
    Convert a MARC/CSV/TSV file to TTL format.

    This is the main entry point for native apps.

    Args:
        input_path: Path to input file (.mrc, .csv, .tsv)
        output_path: Path for output TTL file (optional, auto-generated if not provided)
        include_ontology: Whether to include ontology definitions in output
        validate: Whether to run SHACL validation
        progress_callback: Optional callback function(percent: int, message: str)

    Returns:
        Dictionary with conversion results (JSON-serializable)
    """
    errors = []
    warnings = []
    records_processed = 0
    ttl_content = ""
    validation_passed = None
    validation_report = ""

    try:
        # Import here to avoid circular imports
        from rdflib import Graph

        from ..authority.mazal_matcher import create_matcher
        from ..config.namespaces import bind_namespaces
        from ..parser.unified_reader import UnifiedReader
        from ..transformer.mapper import MarcToRdfMapper
        from ..validation.shacl_validator import ShaclValidator

        input_file = Path(input_path)

        # Validate input file exists
        if not input_file.exists():
            return ConversionResult(
                success=False,
                records_processed=0,
                ttl_content="",
                output_path="",
                validation_passed=None,
                validation_report="",
                errors=[f"Input file not found: {input_path}"],
                warnings=[],
            ).to_dict()

        # Auto-generate output path if not provided
        if output_path is None:
            output_path = str(input_file.with_suffix(".ttl"))

        if progress_callback:
            progress_callback(5, "Initializing...")

        # Initialize Mazal authority matcher (if index exists)
        mazal_matcher = create_matcher()
        if mazal_matcher:
            if progress_callback:
                progress_callback(7, "Loaded Mazal authority index")

        # Create reader and mapper
        reader = UnifiedReader(input_file)
        mapper = MarcToRdfMapper(mazal_matcher=mazal_matcher)

        # Get record count for progress
        try:
            total_records = reader.count_records()
        except:
            total_records = 0

        if progress_callback:
            progress_callback(10, f"Found {total_records} records")

        # Create combined graph
        combined_graph = Graph()
        bind_namespaces(combined_graph)

        # Optionally include ontology definitions
        if include_ontology:
            if progress_callback:
                progress_callback(15, "Loading ontology definitions...")

            ontology_path = (
                Path(__file__).parent.parent.parent / "ontology" / "hebrew-manuscripts.ttl"
            )
            if ontology_path.exists():
                try:
                    combined_graph.parse(str(ontology_path), format="turtle")
                except Exception as e:
                    warnings.append(f"Could not load ontology: {e}")
            else:
                warnings.append(f"Ontology file not found: {ontology_path}")

        # Process records
        if progress_callback:
            progress_callback(20, "Converting records...")

        for record in reader.read_file():
            try:
                record_graph = mapper.map_record(record)
                for triple in record_graph:
                    combined_graph.add(triple)
                records_processed += 1

                if total_records > 0 and progress_callback:
                    progress = 20 + int((records_processed / total_records) * 50)
                    progress_callback(
                        progress, f"Converted {records_processed}/{total_records} records"
                    )

            except Exception as e:
                warnings.append(
                    f"Error converting record {getattr(record, 'control_number', 'unknown')}: {e}"
                )

        if progress_callback:
            progress_callback(75, "Serializing to Turtle...")

        # Serialize to Turtle
        ttl_content = combined_graph.serialize(format="turtle")

        # Save to file
        if progress_callback:
            progress_callback(80, "Saving output file...")

        output_file = Path(output_path)
        output_file.write_text(ttl_content, encoding="utf-8")

        # Run validation if requested
        if validate:
            if progress_callback:
                progress_callback(85, "Running SHACL validation...")

            try:
                validator = ShaclValidator()
                result = validator.validate(combined_graph)
                validation_passed = result.conforms
                validation_report = result.to_report()
            except Exception as e:
                warnings.append(f"Validation error: {e}")
                validation_passed = None
                validation_report = f"Validation failed: {e}"

        # Add Mazal matching statistics
        if mazal_matcher:
            stats = mazal_matcher.get_stats()
            if stats["total_attempts"] > 0:
                warnings.append(
                    f"Mazal authority matching: {stats['total_matched']}/{stats['total_attempts']} "
                    f"entities matched ({stats['match_rate']:.1%})"
                )
            mazal_matcher.close()

        if progress_callback:
            progress_callback(100, "Done!")

        return ConversionResult(
            success=True,
            records_processed=records_processed,
            ttl_content=ttl_content,
            output_path=str(output_path),
            validation_passed=validation_passed,
            validation_report=validation_report,
            errors=errors,
            warnings=warnings,
        ).to_dict()

    except Exception as e:
        error_msg = f"Conversion failed: {str(e)}"
        error_trace = traceback.format_exc()
        errors.append(error_msg)
        errors.append(error_trace)

        return ConversionResult(
            success=False,
            records_processed=records_processed,
            ttl_content=ttl_content,
            output_path=str(output_path) if output_path else "",
            validation_passed=None,
            validation_report="",
            errors=errors,
            warnings=warnings,
        ).to_dict()


def validate_file(ttl_path: str) -> dict[str, Any]:
    """
    Validate an existing TTL file against SHACL shapes.

    Args:
        ttl_path: Path to TTL file to validate

    Returns:
        Dictionary with validation results
    """
    try:
        from rdflib import Graph

        from ..validation.shacl_validator import ShaclValidator

        ttl_file = Path(ttl_path)
        if not ttl_file.exists():
            return {
                "success": False,
                "conforms": False,
                "report": "",
                "error": f"File not found: {ttl_path}",
            }

        # Parse the TTL file
        graph = Graph()
        graph.parse(str(ttl_file), format="turtle")

        # Run validation
        validator = ShaclValidator()
        result = validator.validate(graph)

        return {
            "success": True,
            "conforms": result.conforms,
            "violation_count": result.violation_count,
            "warning_count": result.warning_count,
            "report": result.to_report(),
            "error": None,
        }

    except Exception as e:
        return {"success": False, "conforms": False, "report": "", "error": str(e)}


def convert_to_json(
    input_path: str,
    output_path: str | None = None,
    include_ontology: bool = False,
    validate: bool = True,
) -> str:
    """
    Wrapper that returns JSON string directly.

    Useful for subprocess communication with native apps.

    Args:
        input_path: Path to input file
        output_path: Path for output file
        include_ontology: Include ontology definitions
        validate: Run SHACL validation

    Returns:
        JSON string with results
    """
    result = convert_file(input_path, output_path, include_ontology, validate)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ============================================================================
# ANNOTATION API
# ============================================================================


def get_annotation_types() -> dict[str, Any]:
    """
    Get supported annotation types and their fields.

    Returns:
        Dictionary describing each annotation type and required fields
    """
    return {
        "certainty": {
            "description": "Add certainty/confidence levels to attributions",
            "required_fields": ["manuscript_id", "certainty_level", "attribution_source"],
            "optional_fields": ["certainty_percentage", "note"],
            "certainty_levels": ["Certain", "Probable", "Possible", "Uncertain"],
            "attribution_sources": [
                "CatalogAttribution",
                "ColophonAttribution",
                "PaleographicAttribution",
                "ExpertAttribution",
                "AIAttribution",
            ],
        },
        "text_tradition": {
            "description": "Add text tradition and variant readings",
            "required_fields": ["manuscript_id", "tradition_name"],
            "optional_fields": [
                "work_title",
                "siglum",
                "variant_text",
                "standard_text",
                "folio",
                "line",
                "significance",
            ],
            "significance_levels": [
                "Orthographic_variant",
                "Lexical_variant",
                "Syntactic_variant",
                "Semantic_variant",
                "Addition_variant",
                "Omission_variant",
            ],
        },
        "scribal_intervention": {
            "description": "Document scribal interventions (hand changes, corrections)",
            "required_fields": ["manuscript_id", "intervention_type"],
            "optional_fields": ["folio_range", "description", "scribe_name"],
            "intervention_types": [
                "HandChange",
                "TextCorrection",
                "MarginalAddition",
                "Correction_type",
                "Erasure_type",
                "Interlinear_addition_type",
                "Marginal_gloss_type",
                "Later_hand_type",
            ],
        },
        "canonical_reference": {
            "description": "Link to canonical texts (Bible, Talmud, etc.)",
            "required_fields": ["manuscript_id", "reference_type"],
            "optional_fields": ["book_name", "chapter", "verse", "tractate", "folio"],
            "reference_types": ["Biblical", "Talmudic", "Mishnaic", "Halachic", "Zoharic"],
        },
        "textual_relationship": {
            "description": "Document relationships between texts",
            "required_fields": ["source_id", "target_id", "relationship_type"],
            "relationship_types": [
                "variant_of",
                "abridgment_of",
                "adaptation_of",
                "parallels",
                "possibly_realises",
            ],
        },
        "foreign_unit": {
            "description": "Mark core vs. foreign/added content",
            "required_fields": ["manuscript_id", "unit_id", "is_foreign", "status"],
            "optional_fields": ["addition_period", "folio_range"],
            "status_types": [
                "CoreUnit_status",
                "LaterAddition_status",
                "BinderAddition_status",
                "UnrelatedFragment_status",
                "ProtectiveLeaf_status",
            ],
        },
    }


def add_annotations(
    ttl_path: str, annotations: dict[str, list[dict]], output_path: str | None = None
) -> dict[str, Any]:
    """
    Add scholarly annotations to an existing TTL file.

    Args:
        ttl_path: Path to existing TTL file
        annotations: Dictionary with annotation type keys and list of annotation dicts
                    Example: {"certainty": [{...}, {...}], "text_tradition": [{...}]}
        output_path: Path for output file (optional, defaults to *_annotated.ttl)

    Returns:
        Dictionary with results
    """
    try:
        from ..annotation import (
            AnnotationImporter,
            CanonicalReferenceAnnotation,
            CertaintyAnnotation,
            ForeignUnitAnnotation,
            ScribalInterventionAnnotation,
            TextTraditionAnnotation,
            TextualRelationshipAnnotation,
        )

        ttl_file = Path(ttl_path)
        if not ttl_file.exists():
            return {
                "success": False,
                "annotations_added": 0,
                "output_path": "",
                "error": f"File not found: {ttl_path}",
            }

        importer = AnnotationImporter()

        # Process each annotation type
        for ann_data in annotations.get("certainty", []):
            importer.add_certainty(CertaintyAnnotation(**ann_data))

        for ann_data in annotations.get("text_tradition", []):
            importer.add_text_tradition(TextTraditionAnnotation(**ann_data))

        for ann_data in annotations.get("scribal_intervention", []):
            importer.add_scribal_intervention(ScribalInterventionAnnotation(**ann_data))

        for ann_data in annotations.get("canonical_reference", []):
            importer.add_canonical_reference(CanonicalReferenceAnnotation(**ann_data))

        for ann_data in annotations.get("textual_relationship", []):
            importer.add_textual_relationship(TextualRelationshipAnnotation(**ann_data))

        for ann_data in annotations.get("foreign_unit", []):
            importer.add_foreign_unit(ForeignUnitAnnotation(**ann_data))

        # Determine output path
        if output_path is None:
            output_path = str(ttl_file.parent / f"{ttl_file.stem}_annotated.ttl")

        # Merge with existing TTL
        result_path = importer.merge_with_ttl(ttl_file, Path(output_path))

        return {
            "success": True,
            "annotations_added": importer.annotation_count,
            "output_path": str(result_path),
            "error": None,
        }

    except Exception as e:
        return {"success": False, "annotations_added": 0, "output_path": "", "error": str(e)}


def import_annotations_from_csv(
    ttl_path: str, csv_paths: dict[str, str], output_path: str | None = None
) -> dict[str, Any]:
    """
    Import annotations from CSV files.

    Args:
        ttl_path: Path to existing TTL file
        csv_paths: Dictionary mapping annotation type to CSV file path
                  Example: {"certainty": "/path/to/certainty.csv"}
        output_path: Path for output file

    Returns:
        Dictionary with results
    """
    try:
        from ..annotation import AnnotationImporter, AnnotationType

        ttl_file = Path(ttl_path)
        if not ttl_file.exists():
            return {
                "success": False,
                "annotations_added": 0,
                "output_path": "",
                "error": f"File not found: {ttl_path}",
            }

        importer = AnnotationImporter()

        type_map = {
            "certainty": AnnotationType.CERTAINTY,
            "text_tradition": AnnotationType.TEXT_TRADITION,
            "scribal_intervention": AnnotationType.SCRIBAL_INTERVENTION,
            "canonical_reference": AnnotationType.CANONICAL_REFERENCE,
            "textual_relationship": AnnotationType.TEXTUAL_RELATIONSHIP,
            "foreign_unit": AnnotationType.FOREIGN_UNIT,
        }

        for ann_type, csv_path in csv_paths.items():
            if ann_type in type_map:
                csv_file = Path(csv_path)
                if csv_file.exists():
                    importer.import_from_csv(csv_file, type_map[ann_type])

        # Determine output path
        if output_path is None:
            output_path = str(ttl_file.parent / f"{ttl_file.stem}_annotated.ttl")

        result_path = importer.merge_with_ttl(ttl_file, Path(output_path))

        return {
            "success": True,
            "annotations_added": importer.annotation_count,
            "output_path": str(result_path),
            "error": None,
        }

    except Exception as e:
        return {"success": False, "annotations_added": 0, "output_path": "", "error": str(e)}


def create_annotation_templates(output_dir: str) -> dict[str, Any]:
    """
    Create sample CSV template files for annotations.

    Args:
        output_dir: Directory to create templates in

    Returns:
        Dictionary with created file paths
    """
    try:
        from ..annotation import create_sample_annotation_files

        output_path = Path(output_dir)
        files = create_sample_annotation_files(output_path)

        return {
            "success": True,
            "template_files": [str(f) for f in files],
            "output_dir": str(output_path),
            "error": None,
        }

    except Exception as e:
        return {"success": False, "template_files": [], "output_dir": "", "error": str(e)}


# CLI interface for subprocess calls
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hebrew Manuscripts Converter API")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Convert command
    convert_parser = subparsers.add_parser("convert", help="Convert MARC/CSV to TTL")
    convert_parser.add_argument("--input", "-i", required=True, help="Input file path")
    convert_parser.add_argument("--output", "-o", help="Output file path")
    convert_parser.add_argument(
        "--include-ontology", action="store_true", help="Include ontology definitions"
    )
    convert_parser.add_argument("--no-validate", action="store_true", help="Skip SHACL validation")

    # Annotate command
    annotate_parser = subparsers.add_parser("annotate", help="Add annotations to TTL")
    annotate_parser.add_argument("--ttl", required=True, help="TTL file path")
    annotate_parser.add_argument("--annotations", required=True, help="JSON file with annotations")
    annotate_parser.add_argument("--output", "-o", help="Output file path")

    # Templates command
    templates_parser = subparsers.add_parser("templates", help="Create annotation templates")
    templates_parser.add_argument(
        "--output-dir", "-o", default="annotation_templates", help="Output directory"
    )

    # Version command
    parser.add_argument("--version", action="store_true", help="Show version info")

    args = parser.parse_args()

    if args.version:
        print(json.dumps(get_version(), indent=2))
    elif args.command == "convert":
        result = convert_to_json(
            input_path=args.input,
            output_path=args.output,
            include_ontology=args.include_ontology,
            validate=not args.no_validate,
        )
        print(result)
    elif args.command == "annotate":
        with open(args.annotations, encoding="utf-8") as f:
            annotations = json.load(f)
        result = add_annotations(args.ttl, annotations, args.output)
        print(json.dumps(result, indent=2))
    elif args.command == "templates":
        result = create_annotation_templates(args.output_dir)
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
