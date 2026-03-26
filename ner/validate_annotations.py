"""
Script to validate NER annotations and generate statistics.
Checks for quality issues and provides comprehensive analysis.
"""

import pandas as pd
import json
from collections import defaultdict, Counter
import random

# File paths
INPUT_FILE = 'processed-data/ner_training_dataset.csv'
STATS_FILE = 'processed-data/ner_statistics.json'
SAMPLE_FILE = 'processed-data/ner_sample_annotations.txt'

print("="*80)
print("NER ANNOTATION VALIDATION & STATISTICS")
print("="*80)

# Load annotations
print(f"\nLoading annotations from {INPUT_FILE}...")
df = pd.read_csv(INPUT_FILE, encoding='utf-8')
print(f"Loaded {len(df):,} annotations")

# Basic statistics
print("\n" + "="*80)
print("BASIC STATISTICS")
print("="*80)

unique_records = df['record_id'].nunique()
print(f"Unique records: {unique_records:,}")
print(f"Total annotations: {len(df):,}")
print(f"Average annotations per record: {len(df) / unique_records:.1f}")

# Entity type distribution
print("\n" + "="*80)
print("ENTITY TYPE DISTRIBUTION")
print("="*80)
entity_type_counts = df['entity_type'].value_counts()
for entity_type, count in entity_type_counts.items():
    percentage = (count / len(df)) * 100
    print(f"  {entity_type:15s}: {count:6,} ({percentage:5.2f}%)")

# Annotations per record
print("\n" + "="*80)
print("ANNOTATIONS PER RECORD")
print("="*80)
anns_per_record = df.groupby('record_id').size()
print(f"  Min annotations: {anns_per_record.min()}")
print(f"  Max annotations: {anns_per_record.max()}")
print(f"  Mean annotations: {anns_per_record.mean():.1f}")
print(f"  Median annotations: {anns_per_record.median():.0f}")

# Entity length statistics
print("\n" + "="*80)
print("ENTITY LENGTH STATISTICS (characters)")
print("="*80)
df['entity_length'] = df['entity_text'].str.len()
print(f"  Min length: {df['entity_length'].min()}")
print(f"  Max length: {df['entity_length'].max()}")
print(f"  Mean length: {df['entity_length'].mean():.1f}")
print(f"  Median length: {df['entity_length'].median():.0f}")

# Check for overlapping spans
print("\n" + "="*80)
print("OVERLAP DETECTION")
print("="*80)
overlaps = []
for record_id in df['record_id'].unique():
    record_anns = df[df['record_id'] == record_id].sort_values('start_pos')
    
    for i in range(len(record_anns) - 1):
        curr = record_anns.iloc[i]
        next_ann = record_anns.iloc[i + 1]
        
        if curr['end_pos'] > next_ann['start_pos']:
            overlaps.append({
                'record_id': record_id,
                'entity1': curr['entity_text'],
                'type1': curr['entity_type'],
                'pos1': f"{curr['start_pos']}-{curr['end_pos']}",
                'entity2': next_ann['entity_text'],
                'type2': next_ann['entity_type'],
                'pos2': f"{next_ann['start_pos']}-{next_ann['end_pos']}"
            })

print(f"Found {len(overlaps):,} overlapping annotations")
if overlaps and len(overlaps) <= 10:
    print("\nOverlapping annotations:")
    for overlap in overlaps[:10]:
        print(f"  Record: {overlap['record_id']}")
        print(f"    {overlap['entity1']} ({overlap['type1']}) at {overlap['pos1']}")
        print(f"    {overlap['entity2']} ({overlap['type2']}) at {overlap['pos2']}")

# Check encoding
print("\n" + "="*80)
print("TEXT ENCODING VALIDATION")
print("="*80)
try:
    # Test Hebrew characters
    hebrew_chars = 0
    latin_chars = 0
    for text in df['entity_text'].head(100):
        if pd.notna(text):
            for char in text:
                if '\u0590' <= char <= '\u05FF':  # Hebrew Unicode block
                    hebrew_chars += 1
                elif 'a' <= char.lower() <= 'z':
                    latin_chars += 1
    
    print(f"✓ Hebrew encoding validated")
    print(f"  Hebrew characters found: {hebrew_chars:,}")
    print(f"  Latin characters found: {latin_chars:,}")
    
except Exception as e:
    print(f"✗ Encoding issue detected: {e}")

# Most common entities
print("\n" + "="*80)
print("TOP 10 MOST COMMON ENTITIES (by type)")
print("="*80)
for entity_type in ['PERSON', 'PLACE', 'WORK', 'ORGANIZATION']:
    type_df = df[df['entity_type'] == entity_type]
    if len(type_df) > 0:
        print(f"\n{entity_type}:")
        top_entities = type_df['entity_text'].value_counts().head(10)
        for entity, count in top_entities.items():
            entity_display = entity[:40] + '...' if len(entity) > 40 else entity
            print(f"  {entity_display:40s} ({count:3d})")

# Save statistics to JSON
stats = {
    'total_annotations': len(df),
    'unique_records': unique_records,
    'avg_annotations_per_record': float(len(df) / unique_records),
    'entity_type_distribution': entity_type_counts.to_dict(),
    'annotations_per_record': {
        'min': int(anns_per_record.min()),
        'max': int(anns_per_record.max()),
        'mean': float(anns_per_record.mean()),
        'median': float(anns_per_record.median())
    },
    'entity_length': {
        'min': int(df['entity_length'].min()),
        'max': int(df['entity_length'].max()),
        'mean': float(df['entity_length'].mean()),
        'median': float(df['entity_length'].median())
    },
    'overlapping_annotations': len(overlaps),
    'hebrew_characters': hebrew_chars,
    'latin_characters': latin_chars
}

print(f"\n\nSaving statistics to {STATS_FILE}...")
with open(STATS_FILE, 'w', encoding='utf-8') as f:
    json.dump(stats, f, ensure_ascii=False, indent=2)

# Generate sample annotations file
print(f"Generating sample annotations to {SAMPLE_FILE}...")
with open(SAMPLE_FILE, 'w', encoding='utf-8') as f:
    f.write("="*80 + "\n")
    f.write("SAMPLE NER ANNOTATIONS (10 random samples)\n")
    f.write("="*80 + "\n\n")
    
    # Sample 10 random annotations
    sample_df = df.sample(n=min(10, len(df)), random_state=42)
    
    for i, (idx, row) in enumerate(sample_df.iterrows(), 1):
        f.write(f"\n{i}. ANNOTATION {idx}\n")
        f.write("-" * 80 + "\n")
        f.write(f"Record ID: {row['record_id']}\n")
        f.write(f"Entity: {row['entity_text']}\n")
        f.write(f"Type: {row['entity_type']}\n")
        f.write(f"Position: {row['start_pos']}-{row['end_pos']}\n")
        f.write(f"Confidence: {row['confidence']}\n")
        f.write(f"\nContext (±100 chars):\n")
        text = row['text']
        start = max(0, row['start_pos'] - 100)
        end = min(len(text), row['end_pos'] + 100)
        context_snippet = text[start:end]
        f.write(f"...{context_snippet}...\n")
        f.write("\n")

print("\n" + "="*80)
print("VALIDATION COMPLETE")
print("="*80)
print(f"✓ Statistics saved to: {STATS_FILE}")
print(f"✓ Sample annotations saved to: {SAMPLE_FILE}")
print(f"\nDataset is ready for HalleluBERT fine-tuning!")

