#!/usr/bin/env python3
"""
Hebrew Manuscripts MARC to TTL Converter

Main entry point for the application.
"""

import sys
import argparse
from pathlib import Path


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Convert MARC records to RDF/TTL format"
    )
    parser.add_argument(
        '--gui', '-g',
        action='store_true',
        help='Launch GUI mode'
    )
    parser.add_argument(
        '--input', '-i',
        type=Path,
        help='Input file (.mrc, .csv, or .tsv)'
    )
    parser.add_argument(
        '--output', '-o',
        type=Path,
        help='Output TTL file'
    )
    parser.add_argument(
        '--validate', '-v',
        action='store_true',
        default=True,
        help='Validate output with SHACL shapes (default: True)'
    )
    parser.add_argument(
        '--no-validate',
        action='store_true',
        help='Skip SHACL validation'
    )
    
    args = parser.parse_args()
    
    if args.gui or (not args.input and not args.output):
        from converter.gui.main_window import run_gui
        sys.exit(run_gui())
    
    if not args.input:
        parser.error("--input is required for CLI mode")
    
    if not args.output:
        args.output = args.input.with_suffix('.ttl')
    
    run_cli(args.input, args.output, validate=not args.no_validate)


def run_cli(input_path: Path, output_path: Path, validate: bool = True):
    """Run in command-line mode.
    
    Args:
        input_path: Path to input file (.mrc, .csv, or .tsv)
        output_path: Path for output TTL file
        validate: Whether to run SHACL validation
    """
    print(f"Converting {input_path} to {output_path}...")
    
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)
    
    from converter.parser.unified_reader import UnifiedReader
    from converter.transformer.mapper import MarcToRdfMapper
    from converter.validation.shacl_validator import ShaclValidator
    from converter.config.namespaces import bind_namespaces
    from rdflib import Graph
    
    reader = UnifiedReader(input_path)
    
    try:
        total = reader.count_records()
        print(f"Found {total} records (format: {reader.detected_format.value})")
    except:
        total = 0
        print("Reading records...")
    
    mapper = MarcToRdfMapper()
    combined_graph = Graph()
    bind_namespaces(combined_graph)
    
    count = 0
    errors = 0
    
    for record in reader.read_file():
        try:
            record_graph = mapper.map_record(record)
            for triple in record_graph:
                combined_graph.add(triple)
            count += 1
            
            if total > 0 and count % 10 == 0:
                print(f"  Processed {count}/{total} records...")
        except Exception as e:
            errors += 1
            print(f"  Warning: Error processing record {record.control_number}: {e}")
    
    print(f"\nConverted {count} records ({errors} errors)")
    
    print(f"Saving to {output_path}...")
    combined_graph.serialize(destination=str(output_path), format='turtle')
    print(f"Saved {len(combined_graph)} triples")
    
    if validate:
        print("\nRunning SHACL validation...")
        validator = ShaclValidator()
        result = validator.validate(combined_graph)
        
        if result.conforms:
            print("✓ Validation passed!")
        else:
            print(f"✗ Validation found {result.violation_count} issues:")
            for v in result.violations[:10]:
                print(f"  - {v}")
            if result.violation_count > 10:
                print(f"  ... and {result.violation_count - 10} more")
    
    print("\nDone!")


if __name__ == '__main__':
    main()

