"""
Multiple Labeling Functions using Snorkel Framework
Implements weak supervision with multiple heuristic labeling functions
Expected improvement: +500-1000 training samples, +0.3-0.5% recall
"""

import json
import re
from typing import List, Dict, Tuple, Optional
import numpy as np
from collections import defaultdict
import argparse


class LabelingFunction:
    """Base class for labeling functions"""
    
    def __init__(self, name: str, weight: float = 1.0):
        self.name = name
        self.weight = weight
        self.coverage = 0
        self.conflicts = 0
    
    def apply(self, text: str, metadata: Dict = None) -> List[Dict]:
        """
        Apply labeling function to text
        
        Returns:
            List of labeled entities: [{'start': int, 'end': int, 'text': str, 'label': str}]
        """
        raise NotImplementedError
    
    def __repr__(self):
        return f"{self.name} (weight={self.weight:.2f}, coverage={self.coverage})"


class MARCStructuredFieldLF(LabelingFunction):
    """Label entities based on MARC structured fields (100$a, 700$a, etc.)"""
    
    def __init__(self, weight: float = 1.0):
        super().__init__("MARC_Structured_Fields", weight)
    
    def apply(self, text: str, metadata: Dict = None) -> List[Dict]:
        entities = []
        
        if not metadata or 'marc_persons' not in metadata:
            return entities
        
        marc_persons = metadata['marc_persons']
        
        for person in marc_persons:
            if not person:
                continue
            
            # Find all occurrences in text
            pattern = re.escape(person)
            for match in re.finditer(pattern, text):
                entities.append({
                    'start': match.start(),
                    'end': match.end(),
                    'text': person,
                    'label': 'PERSON',
                    'source': self.name
                })
        
        return entities


class PatronymicPatternLF(LabelingFunction):
    """Label entities based on patronymic patterns: [Name] בן [Name]"""
    
    def __init__(self, weight: float = 0.5):
        super().__init__("Patronymic_Pattern", weight)
    
    def apply(self, text: str, metadata: Dict = None) -> List[Dict]:
        entities = []
        
        # Pattern: Hebrew name + בן/בת + Hebrew name
        # Hebrew letters: \u0590-\u05FF
        pattern = r'([\u0590-\u05FF]+(?:\s[\u0590-\u05FF]+)?)\s+(בן|בת|אבן)\s+([\u0590-\u05FF]+(?:\s[\u0590-\u05FF]+)?)'
        
        for match in re.finditer(pattern, text):
            full_name = match.group(0)
            entities.append({
                'start': match.start(),
                'end': match.end(),
                'text': full_name,
                'label': 'PERSON',
                'source': self.name
            })
        
        return entities


class RoleKeywordProximityLF(LabelingFunction):
    """Label entities based on proximity to role keywords"""
    
    def __init__(self, weight: float = 0.4):
        super().__init__("Role_Keyword_Proximity", weight)
        
        self.role_keywords = {
            'TRANSCRIBER': ['מעתיק', 'העתיק', 'כתב', 'העתקה'],
            'AUTHOR': ['מחבר', 'חיבר', 'כותב', 'מאת', 'חובר'],
            'OWNER': ['בעלים', 'קנה', 'ירש', 'רכש', 'ברשותו'],
            'COMMENTATOR': ['מפרש', 'פירש', 'ביאור', 'פירוש'],
            'TRANSLATOR': ['מתרגם', 'תירגם', 'תרגום']
        }
    
    def apply(self, text: str, metadata: Dict = None) -> List[Dict]:
        entities = []
        
        # Find role keywords
        for role, keywords in self.role_keywords.items():
            for keyword in keywords:
                if keyword not in text:
                    continue
                
                # Find keyword position
                keyword_pos = text.find(keyword)
                
                # Look for Hebrew names within 30 characters before/after
                window_start = max(0, keyword_pos - 30)
                window_end = min(len(text), keyword_pos + len(keyword) + 30)
                window = text[window_start:window_end]
                
                # Find Hebrew name patterns
                name_pattern = r'[\u0590-\u05FF]+(?:\s[\u0590-\u05FF]+){0,3}'
                for match in re.finditer(name_pattern, window):
                    name = match.group(0).strip()
                    
                    # Filter out common words and short names
                    if len(name) < 3 or name in ['של', 'את', 'על', 'אל']:
                        continue
                    
                    # Calculate absolute position in original text
                    abs_start = window_start + match.start()
                    abs_end = window_start + match.end()
                    
                    entities.append({
                        'start': abs_start,
                        'end': abs_end,
                        'text': name,
                        'label': 'PERSON',
                        'source': self.name,
                        'role_hint': role
                    })
        
        return entities


class TitleMarkerLF(LabelingFunction):
    """Label entities with title markers: רבי, הרב, etc."""
    
    def __init__(self, weight: float = 0.6):
        super().__init__("Title_Marker", weight)
        
        self.title_markers = ['רבי', 'הרב', 'רבינו', 'מורנו', "הרמב\"ם", "הרמב\"ן", 'כבוד']
    
    def apply(self, text: str, metadata: Dict = None) -> List[Dict]:
        entities = []
        
        for title in self.title_markers:
            pattern = f'{re.escape(title)}\\s+([\\u0590-\\u05FF]+(?:\\s[\\u0590-\\u05FF]+){{0,2}})'
            
            for match in re.finditer(pattern, text):
                full_name = match.group(0)
                entities.append({
                    'start': match.start(),
                    'end': match.end(),
                    'text': full_name,
                    'label': 'PERSON',
                    'source': self.name
                })
        
        return entities


class CrossFieldValidationLF(LabelingFunction):
    """High confidence if person appears in multiple MARC fields"""
    
    def __init__(self, weight: float = 1.0):
        super().__init__("Cross_Field_Validation", weight)
    
    def apply(self, text: str, metadata: Dict = None) -> List[Dict]:
        entities = []
        
        if not metadata or 'marc_persons' not in metadata:
            return entities
        
        marc_persons = metadata.get('marc_persons', [])
        
        # Count occurrences
        person_counts = defaultdict(int)
        for person in marc_persons:
            if person:
                person_counts[person] += 1
        
        # High confidence entities (appear 2+ times in MARC)
        for person, count in person_counts.items():
            if count >= 2:
                pattern = re.escape(person)
                for match in re.finditer(pattern, text):
                    entities.append({
                        'start': match.start(),
                        'end': match.end(),
                        'text': person,
                        'label': 'PERSON',
                        'source': self.name,
                        'high_confidence': True
                    })
        
        return entities


class SnorkelLabelAggregator:
    """
    Aggregates labels from multiple labeling functions
    Simplified version of Snorkel's label model
    """
    
    def __init__(self, labeling_functions: List[LabelingFunction]):
        self.lfs = labeling_functions
    
    def aggregate_labels(self, all_entity_proposals: List[List[Dict]]) -> List[Dict]:
        """
        Aggregate entity proposals from multiple labeling functions
        
        Args:
            all_entity_proposals: List of entity lists from each LF
            
        Returns:
            Deduplicated and weighted entity list
        """
        # Group overlapping entities
        entity_groups = []
        
        for lf_entities in all_entity_proposals:
            for entity in lf_entities:
                # Find if this entity overlaps with existing groups
                merged = False
                for group in entity_groups:
                    # Check overlap with any entity in group
                    for existing in group:
                        if self._entities_overlap(entity, existing):
                            group.append(entity)
                            merged = True
                            break
                    if merged:
                        break
                
                if not merged:
                    entity_groups.append([entity])
        
        # Aggregate each group
        aggregated = []
        for group in entity_groups:
            agg_entity = self._aggregate_group(group)
            if agg_entity:
                aggregated.append(agg_entity)
        
        return aggregated
    
    def _entities_overlap(self, e1: Dict, e2: Dict, threshold: float = 0.5) -> bool:
        """Check if two entities overlap significantly"""
        start1, end1 = e1['start'], e1['end']
        start2, end2 = e2['start'], e2['end']
        
        # Calculate overlap
        overlap_start = max(start1, start2)
        overlap_end = min(end1, end2)
        
        if overlap_start >= overlap_end:
            return False
        
        overlap_length = overlap_end - overlap_start
        min_length = min(end1 - start1, end2 - start2)
        
        return (overlap_length / min_length) >= threshold
    
    def _aggregate_group(self, group: List[Dict]) -> Optional[Dict]:
        """Aggregate a group of overlapping entities"""
        if not group:
            return None
        
        # Weight votes by LF weights
        weighted_votes = defaultdict(float)
        for entity in group:
            source = entity.get('source', 'unknown')
            
            # Find LF weight
            weight = 1.0
            for lf in self.lfs:
                if lf.name == source:
                    weight = lf.weight
                    break
            
            # Vote for span
            span = (entity['start'], entity['end'])
            weighted_votes[span] += weight
        
        # Select span with highest weight
        best_span = max(weighted_votes.items(), key=lambda x: x[1])
        start, end = best_span[0]
        
        # Find entity with this span
        for entity in group:
            if entity['start'] == start and entity['end'] == end:
                aggregated = entity.copy()
                aggregated['confidence'] = best_span[1] / sum(weighted_votes.values())
                aggregated['num_votes'] = len(group)
                return aggregated
        
        return group[0]
    
    def create_training_sample(self, text: str, metadata: Dict = None) -> Optional[Dict]:
        """
        Create training sample from text using all labeling functions
        
        Returns:
            Dict with 'tokens', 'labels', etc., or None if no entities found
        """
        # Apply all labeling functions
        all_proposals = []
        for lf in self.lfs:
            proposals = lf.apply(text, metadata)
            if proposals:
                lf.coverage += 1
            all_proposals.append(proposals)
        
        # Aggregate labels
        entities = self.aggregate_labels(all_proposals)
        
        if not entities:
            return None
        
        # Create BIO labels
        tokens = text.split()
        labels = ['O'] * len(tokens)
        
        # Map character positions to token indices
        char_to_token = {}
        current_pos = 0
        for i, token in enumerate(tokens):
            token_start = text.find(token, current_pos)
            token_end = token_start + len(token)
            for pos in range(token_start, token_end):
                char_to_token[pos] = i
            current_pos = token_end
        
        # Assign BIO labels
        for entity in entities:
            start_token = char_to_token.get(entity['start'])
            end_token = char_to_token.get(entity['end'] - 1)
            
            if start_token is not None and end_token is not None:
                labels[start_token] = 'B-PERSON'
                for t in range(start_token + 1, end_token + 1):
                    if t < len(labels):
                        labels[t] = 'I-PERSON'
        
        return {
            'tokens': tokens,
            'labels': labels,
            'text': text,
            'entities': entities,
            'metadata': metadata
        }


def create_snorkel_dataset(marc_records_path: str,
                          output_path: str,
                          max_samples: int = None):
    """Create training dataset using Snorkel labeling functions"""
    
    print("="*60)
    print("Creating Snorkel Dataset with Multiple Labeling Functions")
    print("="*60)
    
    # Initialize labeling functions
    labeling_functions = [
        MARCStructuredFieldLF(weight=1.0),        # Highest confidence
        CrossFieldValidationLF(weight=1.0),       # High confidence
        TitleMarkerLF(weight=0.6),                # Medium confidence
        PatronymicPatternLF(weight=0.5),          # Medium confidence
        RoleKeywordProximityLF(weight=0.4)        # Lower confidence
    ]
    
    print("\nLabeling Functions:")
    for lf in labeling_functions:
        print(f"  {lf}")
    
    # Initialize aggregator
    aggregator = SnorkelLabelAggregator(labeling_functions)
    
    # Process MARC records
    samples_created = 0
    
    with open(marc_records_path, 'r', encoding='utf-8') as f_in, \
         open(output_path, 'w', encoding='utf-8') as f_out:
        
        for line_num, line in enumerate(f_in):
            if max_samples and samples_created >= max_samples:
                break
            
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            # Extract text and metadata
            notes_text = []
            for field in ['500', '520', '545', '546', '561', '957']:
                if field in record:
                    for entry in record[field]:
                        if 'a' in entry:
                            notes_text.append(entry['a'])
            
            if not notes_text:
                continue
            
            text = ' '.join(notes_text)
            
            # Extract MARC persons for metadata
            marc_persons = []
            for field in ['100', '600', '700', '800']:
                if field in record:
                    for entry in record[field]:
                        if 'a' in entry:
                            marc_persons.append(entry['a'].strip())
            
            metadata = {
                'marc_persons': marc_persons,
                'record_id': record.get('001', f'record_{line_num}')
            }
            
            # Create training sample
            sample = aggregator.create_training_sample(text, metadata)
            
            if sample:
                f_out.write(json.dumps(sample, ensure_ascii=False) + '\n')
                samples_created += 1
                
                if samples_created % 100 == 0:
                    print(f"\rCreated {samples_created} samples", end='')
    
    print(f"\n\nTotal samples created: {samples_created}")
    
    # Print statistics
    print("\nLabeling Function Coverage:")
    for lf in labeling_functions:
        coverage_pct = (lf.coverage / samples_created * 100) if samples_created > 0 else 0
        print(f"  {lf.name:30s}: {lf.coverage:5d} samples ({coverage_pct:.1f}%)")
    
    print("\n" + "="*60)
    print("Dataset creation complete!")
    print("="*60)


def main():
    parser = argparse.ArgumentParser(description='Create Snorkel Dataset')
    parser.add_argument('--marc-records', type=str,
                       default='processed-data/marc_records.jsonl',
                       help='Path to MARC records')
    parser.add_argument('--output', type=str,
                       default='processed-data/snorkel_dataset.jsonl',
                       help='Output dataset path')
    parser.add_argument('--max-samples', type=int, default=None,
                       help='Maximum number of samples to create')
    
    args = parser.parse_args()
    
    create_snorkel_dataset(
        args.marc_records,
        args.output,
        max_samples=args.max_samples
    )


if __name__ == "__main__":
    main()

