"""
Script to create validated annotations from fuzzy-matched entities.
Generates span-based annotations with validation scores.
"""

import pandas as pd
import json
from collections import defaultdict

# File paths
VALIDATED_ENTITIES = 'processed-data/entity_mappings_validated.json'
DATA_FILE = 'processed-data/top_10000_entities.csv'
OUTPUT_FILE = 'processed-data/ner_training_dataset_validated.csv'

print("="*80)
print("CREATING VALIDATED ANNOTATIONS")
print("="*80)

# Load validated entities
print(f"\nLoading validated entities from {VALIDATED_ENTITIES}...")
with open(VALIDATED_ENTITIES, 'r', encoding='utf-8') as f:
    validated_entities = json.load(f)
print(f"Loaded {len(validated_entities):,} records with validated entities")

# Load data
print(f"\nLoading source data from {DATA_FILE}...")
df = pd.read_csv(DATA_FILE, low_memory=False)
print(f"Loaded {len(df):,} records")

# Create annotations
annotations = []
stats = defaultdict(int)

print("\nCreating span-based annotations...")
for idx, row in df.iterrows():
    if idx % 100 == 0:
        print(f"  Processing {idx}/{len(df)}...")
    
    # Use record_id if available, otherwise fall back to File
    if 'record_id' in row and pd.notna(row['record_id']):
        record_id = row['record_id']
    elif 'File' in row and pd.notna(row['File']):
        record_id = row['File']
    else:
        record_id = f"record_{idx}"
    
    if record_id not in validated_entities:
        continue
    
    context = row.get('context', '')
    if pd.isna(context) or context == '':
        continue
    
    context = str(context)
    record_data = validated_entities[record_id]
    marc_001 = record_data.get('marc_001', record_id)
    
    # Process each entity type
    for entity_type, entities in record_data.get('entities', {}).items():
        for entity_info in entities:
            entity_text = entity_info['text']
            validation_score = entity_info['validation_score']
            positions = entity_info['positions']
            
            # Create annotation for each position
            for start_pos, end_pos in positions:
                # Verify position is valid
                if start_pos < len(context) and end_pos <= len(context):
                    # Extract actual text from context
                    actual_text = context[start_pos:end_pos]
                    
                    annotations.append({
                        'record_id': record_id,
                        'marc_001': marc_001,
                        'text': context,
                        'entity_text': actual_text,
                        'entity_type': entity_type,
                        'start_pos': start_pos,
                        'end_pos': end_pos,
                        'source_field': 'validated',
                        'validation_score': validation_score,
                        'confidence': 'validated'
                    })
                    
                    stats[entity_type] += 1
                    stats['total'] += 1

print(f"  Processing {len(df)}/{len(df)}... Done!")

# Check for overlapping spans
print("\nChecking for overlapping annotations...")
overlaps = []
df_temp = pd.DataFrame(annotations)

for record_id in df_temp['record_id'].unique():
    record_anns = df_temp[df_temp['record_id'] == record_id].sort_values('start_pos')
    
    for i in range(len(record_anns) - 1):
        curr = record_anns.iloc[i]
        next_ann = record_anns.iloc[i + 1]
        
        if curr['end_pos'] > next_ann['start_pos']:
            overlaps.append((record_id, curr['start_pos'], next_ann['start_pos']))

print(f"Found {len(overlaps):,} overlapping spans")

# Remove duplicates
print("\nRemoving duplicate annotations...")
seen = set()
unique_annotations = []

for ann in annotations:
    key = (ann['record_id'], ann['start_pos'], ann['end_pos'], ann['entity_type'])
    if key not in seen:
        seen.add(key)
        unique_annotations.append(ann)

duplicates = len(annotations) - len(unique_annotations)
print(f"Removed {duplicates:,} duplicates")

# Create DataFrame
print(f"\nCreating dataset with {len(unique_annotations):,} annotations...")
df_annotations = pd.DataFrame(unique_annotations)

# Save to CSV
print(f"Saving to {OUTPUT_FILE}...")
df_annotations.to_csv(OUTPUT_FILE, index=False, encoding='utf-8')

# Display statistics
print("\n" + "="*80)
print("ANNOTATION STATISTICS")
print("="*80)

unique_records = df_annotations['record_id'].nunique()
print(f"Unique records: {unique_records:,}")
print(f"Total annotations: {len(df_annotations):,}")
print(f"Average annotations per record: {len(df_annotations) / unique_records:.1f}")

print("\nAnnotations by entity type:")
for entity_type in ['PERSON', 'PLACE', 'DATE', 'WORK', 'ROLE', 'ORGANIZATION']:
    count = stats.get(entity_type, 0)
    percentage = (count / stats['total'] * 100) if stats['total'] > 0 else 0
    print(f"  {entity_type:15s}: {count:6,} ({percentage:5.2f}%)")

# Validation scores distribution
print("\nValidation score statistics:")
print(f"  Mean: {df_annotations['validation_score'].mean():.3f}")
print(f"  Min: {df_annotations['validation_score'].min():.3f}")
print(f"  Max: {df_annotations['validation_score'].max():.3f}")
print(f"  Scores >= 0.9: {(df_annotations['validation_score'] >= 0.9).sum():,} ({(df_annotations['validation_score'] >= 0.9).sum() / len(df_annotations) * 100:.1f}%)")
print(f"  Scores 0.7-0.9: {((df_annotations['validation_score'] >= 0.7) & (df_annotations['validation_score'] < 0.9)).sum():,}")

# Sample annotations
print("\n" + "="*80)
print("SAMPLE ANNOTATIONS (first 5)")
print("="*80)
for i, row in df_annotations.head(5).iterrows():
    print(f"\n{i+1}. Entity: {row['entity_text']}")
    print(f"   Type: {row['entity_type']}")
    print(f"   MARC 001: {row['marc_001']}")
    print(f"   Position: {row['start_pos']}-{row['end_pos']}")
    print(f"   Validation: {row['validation_score']:.3f}")
    snippet = row['text'][max(0, row['start_pos']-30):row['end_pos']+30]
    print(f"   Context: ...{snippet}...")

print("\n✓ Validated annotation creation complete!")
print(f"  Output: {OUTPUT_FILE}")
print(f"  Records: {unique_records:,}")
print(f"  Annotations: {len(df_annotations):,}")

