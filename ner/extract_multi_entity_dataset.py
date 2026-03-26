#!/usr/bin/env python3
"""
Extract Multi-Entity Dataset from MARC Records

Key differences from single-entity approach:
1. Concatenate ALL notes fields per record (500$a, 520$a, 545$a, 546$a, 561$a, 957$a)
2. Extract ALL persons from ALL person fields (100$a, 600$a, 700$a, 800$a)
3. Label ALL persons found in same concatenated text (multiple B-PERSON tags)
4. Create samples with 1 or more persons naturally occurring together

This creates natural multi-entity training data!
"""

import pandas as pd
import re
import logging
import json
from pathlib import Path
from typing import List, Dict, Tuple
from collections import Counter

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


# Load role consolidation mapping
with open('processed-data/relator_consolidation_mapping.json', 'r') as f:
    ROLE_MAPPING = json.load(f)


def consolidate_role(raw_role: str) -> str:
    """Map raw role to consolidated category"""
    raw_role = raw_role.strip()
    return ROLE_MAPPING.get(raw_role, 'OTHER')


class MultiEntityExtractor:
    def __init__(self, input_file: Path):
        self.input_file = input_file
        self.stats = {
            'total_records': 0,
            'records_with_persons': 0,
            'samples_created': 0,
            'single_person_samples': 0,
            'multi_person_samples': 0,
            'total_person_extractions': 0
        }
    
    def clean_text(self, text: str) -> str:
        """Clean MARC text"""
        if pd.isna(text):
            return ""
        text = str(text).strip('"\'').strip()
        text = re.sub(r'\s+', ' ', text)
        return text
    
    def normalize_hebrew(self, text: str) -> str:
        """Normalize Hebrew for matching"""
        text = re.sub(r'[\u0591-\u05C7]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def tokenize_text(self, text: str) -> List[str]:
        """Simple tokenization"""
        tokens = re.split(r'([\s,.:;!?()"\[\]|])', text)
        return [t for t in tokens if t and t.strip()]
    
    def find_person_in_tokens(self, tokens: List[str], person_name: str) -> List[Tuple[int, int]]:
        """
        Find all occurrences of person_name in tokens
        Returns list of (start_idx, end_idx) tuples
        """
        person_tokens = person_name.split()
        person_norm = self.normalize_hebrew(person_name)
        matches = []
        
        for i in range(len(tokens) - len(person_tokens) + 1):
            window = ' '.join(tokens[i:i+len(person_tokens)])
            if self.normalize_hebrew(window) == person_norm:
                matches.append((i, i + len(person_tokens)))
        
        return matches
    
    def extract_from_record(self, row: pd.Series) -> List[Dict]:
        """Extract all persons from one MARC record with full concatenated notes"""
        samples = []
        
        # 1. Concatenate ALL notes fields
        notes_fields_list = ['500$a', '520$a', '545$a', '546$a', '561$a', '957$a']
        notes_parts = []
        
        for field in notes_fields_list:
            if field in row.index and pd.notna(row[field]):
                text = self.clean_text(str(row[field]))
                if len(text) > 10:
                    notes_parts.append(text)
        
        if not notes_parts:
            return samples
        
        # Concatenate with separator
        all_notes = ' | '.join(notes_parts)
        
        if len(all_notes) < 20:
            return samples
        
        # 2. Extract ALL persons from ALL structured fields
        persons_with_roles = []
        
        # Check all person fields
        person_fields = {
            '100$a': '100$e',
            '600$a': '600$e',
            '700$a': '700$e',
            '800$a': '800$e'
        }
        
        for person_field, role_field in person_fields.items():
            if person_field not in row.index:
                continue
            
            if pd.notna(row[person_field]):
                person_values = str(row[person_field]).split('|')
                role_values = []
                
                if role_field in row.index and pd.notna(row[role_field]):
                    role_values = str(row[role_field]).split('|')
                
                for i, person in enumerate(person_values):
                    person = self.clean_text(person)
                    if not person or len(person) < 3:
                        continue
                    
                    # Get corresponding role
                    raw_role = role_values[i] if i < len(role_values) else "Unknown"
                    raw_role = self.clean_text(raw_role)
                    consolidated_role = consolidate_role(raw_role)
                    
                    # Check if person appears in concatenated notes
                    person_norm = self.normalize_hebrew(person)
                    notes_norm = self.normalize_hebrew(all_notes)
                    
                    if person_norm in notes_norm:
                        persons_with_roles.append({
                            'name': person,
                            'role': consolidated_role,
                            'original_role': raw_role,
                            'field': person_field
                        })
        
        if not persons_with_roles:
            return samples
        
        # 3. Create BIO labels for ALL persons in the full concatenated text
        tokens = self.tokenize_text(all_notes)
        
        if len(tokens) < 5 or len(tokens) > 500:
            return samples
        
        # Initialize all as 'O'
        ner_tags = ['O'] * len(tokens)
        
        # Label each person
        persons_found = []
        for person_info in persons_with_roles:
            matches = self.find_person_in_tokens(tokens, person_info['name'])
            
            if matches:
                # Label first occurrence only to avoid overlapping labels
                start_idx, end_idx = matches[0]
                
                # Check no overlap with already labeled tokens
                if all(ner_tags[i] == 'O' for i in range(start_idx, end_idx)):
                    ner_tags[start_idx] = 'B-PERSON'
                    for i in range(start_idx + 1, end_idx):
                        ner_tags[i] = 'I-PERSON'
                    
                    persons_found.append(person_info)
        
        if not persons_found:
            return samples
        
        # Create sample
        sample = {
            'tokens': tokens,
            'ner_tags': ner_tags,
            'persons': persons_found,
            'person_count': len(persons_found),
            'notes_text': all_notes
        }
        
        samples.append(sample)
        
        # Update stats
        if len(persons_found) == 1:
            self.stats['single_person_samples'] += 1
        else:
            self.stats['multi_person_samples'] += 1
        
        self.stats['total_person_extractions'] += len(persons_found)
        
        return samples
    
    def process_dataset(self):
        """Process entire MARC dataset"""
        logger.info("="*80)
        logger.info("MULTI-ENTITY DATASET EXTRACTION")
        logger.info("="*80)
        
        logger.info(f"\nLoading MARC data from: {self.input_file}")
        df = pd.read_csv(self.input_file, low_memory=False)
        
        self.stats['total_records'] = len(df)
        logger.info(f"  Total records: {len(df)}")
        
        all_samples = []
        
        logger.info(f"\nProcessing records...")
        for idx, row in df.iterrows():
            if idx % 10000 == 0:
                logger.info(f"  Processed {idx}/{len(df)} records...")
            
            samples = self.extract_from_record(row)
            if samples:
                self.stats['records_with_persons'] += 1
                all_samples.extend(samples)
        
        self.stats['samples_created'] = len(all_samples)
        
        logger.info(f"\n{'='*80}")
        logger.info("EXTRACTION COMPLETE")
        logger.info(f"{'='*80}")
        logger.info(f"\nStatistics:")
        logger.info(f"  Total records processed: {self.stats['total_records']}")
        logger.info(f"  Records with persons: {self.stats['records_with_persons']}")
        logger.info(f"  Samples created: {self.stats['samples_created']}")
        logger.info(f"  Single-person samples: {self.stats['single_person_samples']}")
        logger.info(f"  Multi-person samples: {self.stats['multi_person_samples']}")
        logger.info(f"  Total person extractions: {self.stats['total_person_extractions']}")
        
        # Distribution
        entity_counts = Counter(s['person_count'] for s in all_samples)
        logger.info(f"\nPerson count distribution:")
        for count in sorted(entity_counts.keys()):
            pct = entity_counts[count] / len(all_samples) * 100
            logger.info(f"  {count} person(s): {entity_counts[count]} ({pct:.1f}%)")
        
        # Save
        output_file = 'processed-data/multi_entity_dataset.jsonl'
        with open(output_file, 'w', encoding='utf-8') as f:
            for sample in all_samples:
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')
        
        logger.info(f"\n✅ Saved to: {output_file}")
        
        # Save stats
        with open('processed-data/multi_entity_extraction_stats.json', 'w') as f:
            json.dump(self.stats, f, indent=2)
        
        return all_samples


def main():
    input_file = Path('processed-data/filtered_data.csv')
    
    if not input_file.exists():
        logger.error(f"Input file not found: {input_file}")
        return
    
    extractor = MultiEntityExtractor(input_file)
    samples = extractor.process_dataset()
    
    logger.info(f"\n✅ Multi-entity dataset creation complete!")
    logger.info(f"   Created {len(samples)} samples")
    logger.info(f"   Single-person: {extractor.stats['single_person_samples']}")
    logger.info(f"   Multi-person: {extractor.stats['multi_person_samples']}")


if __name__ == "__main__":
    main()


