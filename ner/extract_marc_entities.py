#!/usr/bin/env python3
"""
Extract entities from structured MARC fields and match them in unstructured notes
Uses distant supervision: if entity from structured field appears in notes, label it
"""

import pandas as pd
import re
import logging
import sys
from pathlib import Path
from typing import List, Dict, Set, Tuple
from collections import Counter, defaultdict
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("marc_extraction.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# MARC field mappings - FOCUSED on PERSON and RELATOR terms
ENTITY_FIELD_MAPPINGS = {
    'PERSON': ['100$a', '600$a', '700$a', '800$a'],          # Person names from various fields
    'RELATOR': ['100$e', '700$e']                            # Relator terms (Transcriber, Copyist, etc.)
}

# Unstructured notes fields to search in - EXPANDED for maximum coverage
NOTES_FIELDS = ['500$a', '561$a', '957$a', '520$a', '545$a', '546$a']  # Added summary, biographical, language


class MARCEntityExtractor:
    """Extract and match entities from MARC records"""
    
    def __init__(self, input_file: Path):
        self.input_file = input_file
        self.stats = {
            'total_rows': 0,
            'rows_with_entities': 0,
            'rows_with_notes': 0,
            'matched_rows': 0,
            'total_matches': 0,
            'matches_by_type': Counter(),
            'matches_by_notes_field': Counter(),
            'entities_extracted': Counter(),
            'unique_entities': defaultdict(set)
        }
        
    def clean_marc_text(self, text: str) -> str:
        """Clean MARC field text - remove quotes, extra spaces, MARC codes"""
        if pd.isna(text) or not isinstance(text, str):
            return ""
        
        # Remove surrounding quotes
        text = text.strip('"\'')
        
        # Remove common MARC artifacts
        text = re.sub(r'\|\|', '|', text)  # Double pipes
        text = re.sub(r'\s+', ' ', text)  # Multiple spaces
        text = re.sub(r'[,;]+$', '', text)  # Trailing punctuation
        
        return text.strip()
    
    def normalize_hebrew(self, text: str) -> str:
        """Normalize Hebrew text for matching"""
        if not text:
            return ""
        
        # Remove nikud (Hebrew vowel points) - Unicode range 0x0591-0x05C7
        text = re.sub(r'[\u0591-\u05C7]', '', text)
        
        # Normalize spaces
        text = re.sub(r'\s+', ' ', text)
        
        # Remove common punctuation that might interfere
        text = text.replace('"', '').replace("'", '').replace('`', '')
        
        return text.strip()
    
    def extract_entities_from_row(self, row: pd.Series) -> Dict[str, List[str]]:
        """Extract all entities from structured fields in a row"""
        entities_by_type = defaultdict(list)
        
        for entity_type, fields in ENTITY_FIELD_MAPPINGS.items():
            for field in fields:
                if field in row.index and pd.notna(row[field]):
                    raw_value = str(row[field])
                    
                    # Handle pipe-separated multiple values (e.g., in 700$a)
                    if '|' in raw_value:
                        values = raw_value.split('|')
                    else:
                        values = [raw_value]
                    
                    for value in values:
                        cleaned = self.clean_marc_text(value)
                        if cleaned and len(cleaned) > 2:  # Min 3 characters
                            entities_by_type[entity_type].append(cleaned)
                            self.stats['entities_extracted'][entity_type] += 1
                            self.stats['unique_entities'][entity_type].add(cleaned)
        
        return entities_by_type
    
    def get_notes_text(self, row: pd.Series) -> List[Tuple[str, str]]:
        """Extract text from all notes fields"""
        notes = []
        
        for field in NOTES_FIELDS:
            if field in row.index and pd.notna(row[field]):
                text = self.clean_marc_text(str(row[field]))
                if text and len(text) > 20:  # Minimum meaningful text length
                    notes.append((field, text))
        
        return notes
    
    def find_entity_in_text(self, entity: str, text: str, entity_type: str = None) -> List[Tuple[int, int, str]]:
        """
        Find all occurrences of entity in text
        Returns list of (start_pos, end_pos, matched_text)
        """
        matches = []
        
        # Regular entity matching
        # Normalize for matching
        entity_norm = self.normalize_hebrew(entity)
        text_norm = self.normalize_hebrew(text)
        
        if not entity_norm or len(entity_norm) < 3:
            return matches
        
        # Try exact match first
        if entity_norm in text_norm:
            # Find all occurrences
            start = 0
            while True:
                pos = text_norm.find(entity_norm, start)
                if pos == -1:
                    break
                
                # Verify it's a word boundary match (not part of larger word)
                is_word_start = pos == 0 or text_norm[pos-1] in ' \t\n.,;:"|()[]'
                is_word_end = (pos + len(entity_norm)) >= len(text_norm) or \
                             text_norm[pos + len(entity_norm)] in ' \t\n.,;:"|()[]'
                
                if is_word_start and is_word_end:
                    # Find original positions in non-normalized text
                    matches.append((pos, pos + len(entity_norm), entity))
                
                start = pos + 1
        
        # Try partial match for complex names (e.g., "חיים ויטל" might appear as "חיים בן יוסף ויטל")
        elif len(entity_norm.split()) > 1:
            # For multi-word entities, try matching individual significant words
            words = [w for w in entity_norm.split() if len(w) > 2]
            if len(words) >= 2:
                # Check if at least 2 significant words appear close together
                word_positions = []
                for word in words[:2]:  # Check first 2 words
                    if word in text_norm:
                        pos = text_norm.find(word)
                        word_positions.append(pos)
                
                # If words appear within 30 characters of each other, consider it a match
                if len(word_positions) >= 2 and max(word_positions) - min(word_positions) < 30:
                    matches.append((min(word_positions), max(word_positions) + len(words[-1]), entity))
        
        return matches
    
    def process_row(self, idx: int, row: pd.Series) -> List[Dict]:
        """Process a single row and find entity matches"""
        self.stats['total_rows'] += 1
        
        # Extract entities from structured fields
        entities_by_type = self.extract_entities_from_row(row)
        
        if not entities_by_type:
            return []
        
        self.stats['rows_with_entities'] += 1
        
        # Get notes text
        notes_list = self.get_notes_text(row)
        
        if not notes_list:
            return []
        
        self.stats['rows_with_notes'] += 1
        
        # Find matches
        matches = []
        
        for notes_field, notes_text in notes_list:
            for entity_type, entity_list in entities_by_type.items():
                for entity in entity_list:
                    # Find entity in notes (with entity_type for special handling)
                    occurrences = self.find_entity_in_text(entity, notes_text, entity_type)
                    
                    if occurrences:
                        for start_pos, end_pos, matched_text in occurrences:
                            match = {
                                'row_id': idx,
                                'source_file': row.get('File', ''),
                                'entity': entity,
                                'entity_type': entity_type,
                                'entity_source_field': None,  # Can track which field
                                'notes_field': notes_field,
                                'notes_text': notes_text,
                                'match_start': start_pos,
                                'match_end': end_pos,
                                'matched_text': matched_text
                            }
                            matches.append(match)
                            
                            self.stats['total_matches'] += 1
                            self.stats['matches_by_type'][entity_type] += 1
                            self.stats['matches_by_notes_field'][notes_field] += 1
        
        if matches:
            self.stats['matched_rows'] += 1
        
        return matches
    
    def extract_all(self, max_rows: int = None) -> pd.DataFrame:
        """Extract entities from entire dataset"""
        logger.info("="*80)
        logger.info("MARC ENTITY EXTRACTION - DISTANT SUPERVISION")
        logger.info("="*80)
        logger.info(f"\nReading data from: {self.input_file}")
        
        # Read CSV
        if max_rows:
            df = pd.read_csv(self.input_file, nrows=max_rows)
            logger.info(f"✓ Loaded {len(df):,} rows (limited for testing)")
        else:
            df = pd.read_csv(self.input_file)
            logger.info(f"✓ Loaded {len(df):,} rows")
        
        logger.info(f"\nProcessing rows to find entity matches...")
        logger.info(f"Entity types: {list(ENTITY_FIELD_MAPPINGS.keys())}")
        logger.info(f"Notes fields: {NOTES_FIELDS}")
        
        all_matches = []
        
        for idx, row in df.iterrows():
            if (idx + 1) % 1000 == 0:
                logger.info(f"  Processed {idx + 1:,} rows... (Matched: {self.stats['matched_rows']:,})")
            
            matches = self.process_row(idx, row)
            all_matches.extend(matches)
        
        logger.info(f"\n{'='*80}")
        logger.info("EXTRACTION STATISTICS")
        logger.info(f"{'='*80}")
        logger.info(f"\n📊 Row Statistics:")
        logger.info(f"  Total rows processed: {self.stats['total_rows']:,}")
        logger.info(f"  Rows with entities in structured fields: {self.stats['rows_with_entities']:,} ({self.stats['rows_with_entities']/self.stats['total_rows']*100:.1f}%)")
        logger.info(f"  Rows with notes: {self.stats['rows_with_notes']:,} ({self.stats['rows_with_notes']/self.stats['total_rows']*100:.1f}%)")
        logger.info(f"  Rows with matches: {self.stats['matched_rows']:,} ({self.stats['matched_rows']/self.stats['total_rows']*100:.1f}%)")
        
        logger.info(f"\n📈 Entity Extraction:")
        logger.info(f"  Total entities extracted from structured fields:")
        for entity_type, count in self.stats['entities_extracted'].items():
            unique_count = len(self.stats['unique_entities'][entity_type])
            logger.info(f"    {entity_type}: {count:,} (unique: {unique_count:,})")
        
        logger.info(f"\n🎯 Entity Matches in Notes:")
        logger.info(f"  Total matches: {self.stats['total_matches']:,}")
        logger.info(f"  Matches per matched row: {self.stats['total_matches']/max(self.stats['matched_rows'], 1):.2f}")
        logger.info(f"\n  Matches by entity type:")
        for entity_type, count in sorted(self.stats['matches_by_type'].items()):
            logger.info(f"    {entity_type}: {count:,}")
        
        logger.info(f"\n  Matches by notes field:")
        for field, count in sorted(self.stats['matches_by_notes_field'].items()):
            logger.info(f"    {field}: {count:,}")
        
        # Convert to DataFrame
        matches_df = pd.DataFrame(all_matches)
        
        if len(matches_df) > 0:
            logger.info(f"\n✓ Created DataFrame with {len(matches_df):,} matches")
            logger.info(f"  Unique texts with matches: {matches_df['notes_text'].nunique():,}")
            logger.info(f"  Unique entities matched: {matches_df['entity'].nunique():,}")
        else:
            logger.warning("⚠️  No matches found!")
        
        return matches_df
    
    def save_results(self, matches_df: pd.DataFrame, output_file: str):
        """Save matched results"""
        logger.info(f"\nSaving results to: {output_file}")
        matches_df.to_csv(output_file, index=False, encoding='utf-8')
        logger.info(f"✓ Saved {len(matches_df):,} matches")
        
        # Save statistics
        stats_file = "marc_extraction_stats.json"
        stats_serializable = {
            k: dict(v) if isinstance(v, (Counter, defaultdict)) else v
            for k, v in self.stats.items()
            if k != 'unique_entities'  # Skip sets
        }
        stats_serializable['unique_entities_count'] = {
            k: len(v) for k, v in self.stats['unique_entities'].items()
        }
        
        with open(stats_file, 'w', encoding='utf-8') as f:
            json.dump(stats_serializable, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ Statistics saved to: {stats_file}")


def main():
    input_file = Path("/Users/alexandergo/Documents/Doctorat/ner-extruction-system/processed-data/filtered_data.csv")
    output_file = "marc_entity_matches.csv"
    
    # For initial testing, process first 10,000 rows
    # Set to None to process all rows
    test_mode = False  # Process full dataset
    max_rows = None
    
    if not input_file.exists():
        logger.error(f"Input file not found: {input_file}")
        sys.exit(1)
    
    extractor = MARCEntityExtractor(input_file)
    
    # Extract matches
    matches_df = extractor.extract_all(max_rows=max_rows)
    
    # Save results
    if len(matches_df) > 0:
        extractor.save_results(matches_df, output_file)
        
        # Show sample matches
        logger.info(f"\n{'='*80}")
        logger.info("SAMPLE MATCHES (First 5)")
        logger.info(f"{'='*80}")
        
        for idx, row in matches_df.head(5).iterrows():
            logger.info(f"\nMatch {idx + 1}:")
            logger.info(f"  Entity: {row['entity']}")
            logger.info(f"  Type: {row['entity_type']}")
            logger.info(f"  Notes field: {row['notes_field']}")
            logger.info(f"  Context: ...{row['notes_text'][max(0, row['match_start']-30):row['match_end']+30]}...")
        
        logger.info(f"\n{'='*80}")
        logger.info("NEXT STEPS")
        logger.info(f"{'='*80}")
        logger.info(f"\n1. Review matches in: {output_file}")
        logger.info(f"2. Check entity distribution meets CoNLL benchmarks:")
        logger.info(f"   - Target: 3,000+ PERSON entities")
        logger.info(f"   - Target: 3,000+ WORK entities")
        logger.info(f"   - Target: 1,000+ PLACE entities")
        logger.info(f"   - Target: 1,000+ ORGANIZATION entities")
        logger.info(f"\n3. If targets not met, run on full dataset (set test_mode=False)")
        logger.info(f"4. Proceed to create_bio_labels.py to generate training data")
        
    else:
        logger.error("\n❌ No matches found. Check:")
        logger.error("  - Are entity fields populated?")
        logger.error("  - Are notes fields populated?")
        logger.error("  - Is matching logic too strict?")
    
    logger.info(f"\n{'='*80}")
    logger.info("EXTRACTION COMPLETE")
    logger.info(f"{'='*80}")


if __name__ == "__main__":
    main()

