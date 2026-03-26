#!/usr/bin/env python3
"""
Convert MARC entity matches into BIO-labeled training data
Creates sentences with proper token-level NER annotations
"""

import pandas as pd
import re
import logging
import sys
from pathlib import Path
from typing import List, Dict, Tuple
from collections import Counter, defaultdict
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bio_labeling.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class BIOLabeler:
    """Create BIO-labeled dataset from entity matches"""
    
    def __init__(self, matches_file: Path):
        self.matches_file = matches_file
        self.stats = {
            'total_matches': 0,
            'unique_texts': 0,
            'sentences_created': 0,
            'entities_labeled': 0,
            'tokens_total': 0,
            'tokens_entity': 0,
            'labels_by_type': Counter(),
            'sentences_by_entity_count': Counter(),
            'skipped_too_long': 0,
            'skipped_too_short': 0,
            'skipped_no_entity': 0
        }
        
    def tokenize_hebrew(self, text: str) -> List[str]:
        """Tokenize Hebrew text"""
        # Simple whitespace tokenization (works well for Hebrew)
        tokens = text.split()
        
        # Clean tokens
        cleaned_tokens = []
        for token in tokens:
            # Keep the token but note if it has punctuation
            cleaned_tokens.append(token)
        
        return cleaned_tokens
    
    def normalize_for_matching(self, text: str) -> str:
        """Normalize text for entity matching"""
        # Remove nikud
        text = re.sub(r'[\u0591-\u05C7]', '', text)
        # Remove quotes and special chars
        text = re.sub(r'["\'""`׳״]', '', text)
        # Normalize spaces
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def find_entity_tokens(self, tokens: List[str], entity: str, start_hint: int = None) -> Tuple[int, int]:
        """
        Find token span for entity in token list
        Returns (start_token_idx, end_token_idx) or (-1, -1) if not found
        """
        entity_norm = self.normalize_for_matching(entity)
        entity_words = entity_norm.split()
        
        if not entity_words:
            return (-1, -1)
        
        # Try to find the entity tokens
        tokens_norm = [self.normalize_for_matching(t) for t in tokens]
        
        # Search for entity word sequence
        for i in range(len(tokens_norm) - len(entity_words) + 1):
            # Check if entity words match tokens starting at position i
            match = True
            for j, entity_word in enumerate(entity_words):
                token_norm = tokens_norm[i + j]
                # Allow partial match (entity word is substring of token or vice versa)
                if entity_word not in token_norm and token_norm not in entity_word:
                    # Try fuzzy match for Hebrew variations
                    if len(entity_word) > 3 and len(token_norm) > 3:
                        # Check if they share significant overlap
                        if entity_word[:3] != token_norm[:3]:
                            match = False
                            break
                    else:
                        match = False
                        break
            
            if match:
                return (i, i + len(entity_words) - 1)
        
        return (-1, -1)
    
    def create_bio_labels(self, tokens: List[str], entities: List[Dict]) -> List[str]:
        """
        Create BIO labels for tokens given entity annotations
        entities: list of {'entity': str, 'type': str, 'start': int, 'end': int}
        """
        labels = ['O'] * len(tokens)
        
        # Sort entities by start position to handle overlaps
        sorted_entities = sorted(entities, key=lambda x: (x['start'], -(x['end'] - x['start'])))
        
        # Track occupied positions to handle overlaps
        occupied = set()
        
        for entity in sorted_entities:
            start, end = entity['start'], entity['end']
            entity_type = entity['type']
            
            # Check if any position is already occupied
            if any(i in occupied for i in range(start, end + 1)):
                continue  # Skip overlapping entity
            
            # Label tokens
            if start == end:
                labels[start] = f"B-{entity_type}"
            else:
                labels[start] = f"B-{entity_type}"
                for i in range(start + 1, end + 1):
                    labels[i] = f"I-{entity_type}"
            
            # Mark as occupied
            for i in range(start, end + 1):
                occupied.add(i)
            
            self.stats['entities_labeled'] += 1
            self.stats['labels_by_type'][entity_type] += 1
        
        return labels
    
    def process_text_with_matches(self, text_id: str, text: str, matches: List[Dict]) -> List[Dict]:
        """
        Process a text with its entity matches and create BIO-labeled samples
        
        Strategy:
        - Split long text into sentences (by periods, pipes, etc.)
        - For each sentence, find which entities appear
        - Create BIO labels for that sentence
        """
        # Split text into sentences (Hebrew uses | and . as separators in MARC)
        sentence_separators = r'[|]|(?<=[\.!?])\s+'
        sentences_raw = re.split(sentence_separators, text)
        
        samples = []
        
        for sent_idx, sentence in enumerate(sentences_raw):
            sentence = sentence.strip()
            
            if not sentence or len(sentence) < 10:
                self.stats['skipped_too_short'] += 1
                continue
            
            # Tokenize sentence
            tokens = self.tokenize_hebrew(sentence)
            
            if len(tokens) < 3:
                self.stats['skipped_too_short'] += 1
                continue
            
            if len(tokens) > 100:
                self.stats['skipped_too_long'] += 1
                continue
            
            # Find which entities from matches appear in this sentence
            sentence_entities = []
            
            for match in matches:
                entity = match['entity']
                entity_type = match['entity_type']
                
                # Try to find entity in this sentence's tokens
                start_tok, end_tok = self.find_entity_tokens(tokens, entity)
                
                if start_tok != -1:
                    sentence_entities.append({
                        'entity': entity,
                        'type': entity_type,
                        'start': start_tok,
                        'end': end_tok
                    })
            
            # Only keep sentences with at least one entity
            if not sentence_entities:
                continue
            
            # Create BIO labels
            labels = self.create_bio_labels(tokens, sentence_entities)
            
            # Verify label sequence integrity
            if not self.validate_bio_sequence(labels):
                logger.warning(f"Invalid BIO sequence in text_id={text_id}, sentence={sent_idx}")
                continue
            
            # Create sample
            sample = {
                'text_id': f"{text_id}_sent{sent_idx}",
                'tokens': tokens,
                'labels': labels,
                'num_entities': len(sentence_entities),
                'entity_types': list(set(e['type'] for e in sentence_entities)),
                'source_text': text_id
            }
            
            samples.append(sample)
            self.stats['sentences_created'] += 1
            self.stats['tokens_total'] += len(tokens)
            self.stats['tokens_entity'] += sum(1 for l in labels if l != 'O')
            self.stats['sentences_by_entity_count'][len(sentence_entities)] += 1
        
        return samples
    
    def validate_bio_sequence(self, labels: List[str]) -> bool:
        """Validate BIO label sequence integrity"""
        for i, label in enumerate(labels):
            # I- must follow B- or I- of same type
            if label.startswith('I-'):
                if i == 0:
                    return False
                
                entity_type = label[2:]
                prev_label = labels[i-1]
                
                if prev_label == 'O':
                    return False
                
                if prev_label.startswith('B-') or prev_label.startswith('I-'):
                    prev_type = prev_label[2:]
                    if prev_type != entity_type:
                        return False
        
        return True
    
    def create_dataset(self) -> pd.DataFrame:
        """Create full BIO-labeled dataset from matches"""
        logger.info("="*80)
        logger.info("CREATING BIO-LABELED DATASET")
        logger.info("="*80)
        logger.info(f"\nReading matches from: {self.matches_file}")
        
        # Read matches
        matches_df = pd.read_csv(self.matches_file)
        self.stats['total_matches'] = len(matches_df)
        logger.info(f"✓ Loaded {len(matches_df):,} entity matches")
        
        # Group matches by unique text
        grouped = matches_df.groupby('notes_text')
        self.stats['unique_texts'] = len(grouped)
        logger.info(f"✓ Found {self.stats['unique_texts']:,} unique texts")
        
        logger.info(f"\nProcessing texts and creating BIO labels...")
        
        all_samples = []
        processed = 0
        
        for text_id_idx, (notes_text, group) in enumerate(grouped):
            processed += 1
            
            if processed % 1000 == 0:
                logger.info(f"  Processed {processed:,} texts... (Sentences: {self.stats['sentences_created']:,})")
            
            # Get all matches for this text
            matches = group.to_dict('records')
            
            # Create BIO-labeled samples
            text_id = f"text_{text_id_idx}"
            samples = self.process_text_with_matches(text_id, notes_text, matches)
            
            all_samples.extend(samples)
        
        # Convert to DataFrame - but need to handle list columns
        logger.info(f"\nConverting to DataFrame format...")
        
        # Create rows with tokens and labels as JSON strings for CSV
        dataset_rows = []
        for sample in all_samples:
            row = {
                'text_id': sample['text_id'],
                'tokens': json.dumps(sample['tokens'], ensure_ascii=False),
                'labels': json.dumps(sample['labels'], ensure_ascii=False),
                'num_entities': sample['num_entities'],
                'entity_types': '|'.join(sample['entity_types']),
                'source_text': sample['source_text']
            }
            dataset_rows.append(row)
        
        dataset_df = pd.DataFrame(dataset_rows)
        
        logger.info(f"\n{'='*80}")
        logger.info("DATASET STATISTICS")
        logger.info(f"{'='*80}")
        logger.info(f"\n📊 Dataset Size:")
        logger.info(f"  Total sentences: {self.stats['sentences_created']:,}")
        logger.info(f"  Total tokens: {self.stats['tokens_total']:,}")
        logger.info(f"  Entity tokens: {self.stats['tokens_entity']:,}")
        logger.info(f"  Entity density: {self.stats['tokens_entity']/max(self.stats['tokens_total'],1)*100:.2f}%")
        
        logger.info(f"\n📈 Entity Distribution:")
        logger.info(f"  Total entities labeled: {self.stats['entities_labeled']:,}")
        logger.info(f"  By type:")
        for entity_type, count in sorted(self.stats['labels_by_type'].items()):
            pct = count / self.stats['entities_labeled'] * 100
            logger.info(f"    {entity_type}: {count:,} ({pct:.1f}%)")
        
        logger.info(f"\n🎯 Sentence Complexity:")
        logger.info(f"  Entities per sentence distribution:")
        for num_ents, count in sorted(self.stats['sentences_by_entity_count'].items()):
            logger.info(f"    {num_ents} entities: {count:,} sentences")
        
        avg_tokens = self.stats['tokens_total'] / max(self.stats['sentences_created'], 1)
        avg_entities = self.stats['entities_labeled'] / max(self.stats['sentences_created'], 1)
        logger.info(f"\n  Average tokens per sentence: {avg_tokens:.1f}")
        logger.info(f"  Average entities per sentence: {avg_entities:.2f}")
        
        logger.info(f"\n⚠️  Skipped:")
        logger.info(f"  Too short: {self.stats['skipped_too_short']:,}")
        logger.info(f"  Too long: {self.stats['skipped_too_long']:,}")
        
        return dataset_df
    
    def save_dataset(self, dataset_df: pd.DataFrame, output_file: str):
        """Save BIO-labeled dataset"""
        logger.info(f"\nSaving dataset to: {output_file}")
        dataset_df.to_csv(output_file, index=False, encoding='utf-8')
        logger.info(f"✓ Saved {len(dataset_df):,} samples")
        
        # Save statistics
        stats_file = "bio_labeling_stats.json"
        with open(stats_file, 'w', encoding='utf-8') as f:
            stats_serializable = {
                k: dict(v) if isinstance(v, Counter) else v
                for k, v in self.stats.items()
            }
            json.dump(stats_serializable, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ Statistics saved to: {stats_file}")


def main():
    matches_file = Path("marc_entity_matches.csv")
    output_file = "marc_ner_bio_labeled.csv"
    
    if not matches_file.exists():
        logger.error(f"Matches file not found: {matches_file}")
        logger.error("Run extract_marc_entities.py first!")
        sys.exit(1)
    
    labeler = BIOLabeler(matches_file)
    
    # Create BIO-labeled dataset
    dataset_df = labeler.create_dataset()
    
    # Save dataset
    if len(dataset_df) > 0:
        labeler.save_dataset(dataset_df, output_file)
        
        # Show sample
        logger.info(f"\n{'='*80}")
        logger.info("SAMPLE BIO-LABELED SENTENCES (First 3)")
        logger.info(f"{'='*80}")
        
        for idx, row in dataset_df.head(3).iterrows():
            tokens = json.loads(row['tokens'])
            labels = json.loads(row['labels'])
            
            logger.info(f"\nSample {idx + 1}:")
            logger.info(f"  Text ID: {row['text_id']}")
            logger.info(f"  Entities: {row['num_entities']}, Types: {row['entity_types']}")
            logger.info(f"  Tokens ({len(tokens)}): {' '.join(tokens[:15])}...")
            logger.info(f"  Labels: {' '.join(labels[:15])}...")
            
            # Show entity highlights
            entities_found = []
            current_entity = []
            current_type = None
            
            for token, label in zip(tokens, labels):
                if label.startswith('B-'):
                    if current_entity:
                        entities_found.append((' '.join(current_entity), current_type))
                    current_entity = [token]
                    current_type = label[2:]
                elif label.startswith('I-') and current_entity:
                    current_entity.append(token)
                elif label == 'O' and current_entity:
                    entities_found.append((' '.join(current_entity), current_type))
                    current_entity = []
                    current_type = None
            
            if current_entity:
                entities_found.append((' '.join(current_entity), current_type))
            
            logger.info(f"  Entities found: {entities_found}")
        
        logger.info(f"\n{'='*80}")
        logger.info("COMPARISON WITH CONLL-2003 BENCHMARKS")
        logger.info(f"{'='*80}")
        
        total_sents = labeler.stats['sentences_created']
        total_entities = labeler.stats['entities_labeled']
        
        logger.info(f"\n📊 Dataset Metrics vs CoNLL Targets:")
        logger.info(f"  Sentences: {total_sents:,} (CoNLL train: 14,041)")
        logger.info(f"  Total entities: {total_entities:,} (CoNLL train: ~23,500)")
        logger.info(f"  Entity density: {labeler.stats['tokens_entity']/max(labeler.stats['tokens_total'],1)*100:.2f}% (CoNLL: 8-11%)")
        logger.info(f"  Entities/sentence: {total_entities/max(total_sents,1):.2f} (CoNLL: 1.7)")
        
        logger.info(f"\n📈 Entity Type Distribution vs CoNLL Targets:")
        for entity_type, count in sorted(labeler.stats['labels_by_type'].items()):
            pct = count / total_entities * 100
            
            # CoNLL targets
            targets = {
                'PERSON': 3000,
                'WORK': 3000,
                'ORGANIZATION': 1000,
                'PLACE': 1000
            }
            target = targets.get(entity_type, 500)
            status = "✓" if count >= target else "⚠️"
            
            logger.info(f"  {status} {entity_type}: {count:,} / {target:,} target ({pct:.1f}%)")
        
        logger.info(f"\n{'='*80}")
        logger.info("NEXT STEPS")
        logger.info(f"{'='*80}")
        logger.info(f"\n1. Review dataset: {output_file}")
        logger.info(f"2. Run validate_marc_dataset.py for quality control")
        logger.info(f"3. Split into train/val/test sets")
        logger.info(f"4. Train HalleluBERT with optimized hyperparameters")
        
    else:
        logger.error("\n❌ No dataset created!")
    
    logger.info(f"\n{'='*80}")
    logger.info("BIO LABELING COMPLETE")
    logger.info(f"{'='*80}")


if __name__ == "__main__":
    main()


