#!/usr/bin/env python3
"""Build Mazal Authority Index from NLI XML files.

This script parses NLI authority XML files and builds a SQLite database
for fast entity name lookups during MARC to TTL conversion.

Usage:
    python build_mazal_index.py --input /path/to/NLI_AUTHORITY_XML/ --output mazal_index.db
    
Example:
    python scripts/build_mazal_index.py \
        --input /Users/alexandergo/Documents/Doctorat/first_paper/NLI_AUTHORITY_XML/ \
        --output converter/authority/mazal_index.db
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from converter.authority.mazal_index import build_index, MazalIndex


def setup_logging(verbose: bool = True):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def main():
    parser = argparse.ArgumentParser(
        description='Build Mazal Authority Index from NLI XML files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        '--input', '-i',
        required=True,
        help='Directory containing NLI authority XML files (NLIAUT*.xml)'
    )
    
    parser.add_argument(
        '--output', '-o',
        default='converter/authority/mazal_index.db',
        help='Output SQLite database path (default: converter/authority/mazal_index.db)'
    )
    
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress progress output'
    )
    
    parser.add_argument(
        '--test', '-t',
        action='store_true',
        help='Run test lookups after building index'
    )
    
    args = parser.parse_args()
    
    setup_logging(not args.quiet)
    logger = logging.getLogger(__name__)
    
    # Validate input directory
    input_path = Path(args.input)
    if not input_path.exists():
        logger.error(f"Input directory not found: {args.input}")
        sys.exit(1)
    
    # Create output directory if needed
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Remove existing database
    if output_path.exists():
        logger.info(f"Removing existing database: {output_path}")
        output_path.unlink()
    
    # Build index
    logger.info(f"Building Mazal index from: {input_path}")
    logger.info(f"Output database: {output_path}")
    
    start_time = time.time()
    
    try:
        index = build_index(
            str(input_path),
            str(output_path),
            verbose=not args.quiet
        )
        
        elapsed = time.time() - start_time
        logger.info(f"Index built successfully in {elapsed:.1f} seconds")
        
        # Show final statistics
        stats = index.get_stats()
        logger.info("\n=== Index Statistics ===")
        logger.info(f"Total authority records: {stats['total_records']:,}")
        logger.info(f"Total name variants: {stats['name_variants']:,}")
        logger.info("\nBy entity type:")
        for etype, count in sorted(stats['by_type'].items()):
            logger.info(f"  {etype}: {count:,}")
        
        # Run test lookups if requested
        if args.test:
            logger.info("\n=== Running Test Lookups ===")
            test_lookups = [
                ('person', 'רמב"ם'),
                ('person', 'Maimonides'),
                ('person', 'משה בן מימון'),
                ('place', 'ירושלים'),
                ('place', 'Jerusalem'),
                ('work', 'מורה נבוכים'),
                ('work', 'משנה תורה'),
            ]
            
            for entity_type, name in test_lookups:
                if entity_type == 'person':
                    result = index.lookup_person(name)
                elif entity_type == 'place':
                    result = index.lookup_place(name)
                elif entity_type == 'work':
                    result = index.lookup_work(name)
                else:
                    result = index.lookup(name, entity_type)
                
                status = f"✓ {result}" if result else "✗ Not found"
                logger.info(f"  {entity_type}: '{name}' -> {status}")
        
        index.close()
        
    except Exception as e:
        logger.error(f"Failed to build index: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    logger.info("\nDone!")
    

if __name__ == '__main__':
    main()



