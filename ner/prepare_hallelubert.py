"""
Prepare balanced NER training data for HalleluBERT.
Converts span-based annotations to BIO token-level format.
"""

import pandas as pd
import json
from collections import defaultdict

print("="*80)
print("PREPARING BALANCED DATA FOR HALLELUBERT TRAINING")
print("="*80)

# Load balanced dataset
INPUT_FILE = 'processed-data/ner_training_dataset_balanced.csv'
OUTPUT_FILE = 'processed-data/hallelubert_training_data_balanced.json'

print(f"\nLoading balanced data from {INPUT_FILE}...")
df = pd.read_csv(INPUT_FILE)
print(f"Loaded {len(df):,} annotations")
print(f"Unique records: {df['record_id'].nunique():,}")

# Get text content - need to load from original files
print("\nLoading full text content...")

# Load top_10000_entities.csv to get full text
entities_df = pd.read_csv('processed-data/top_10000_entities.csv', low_memory=False)
print(f"Loaded {len(entities_df):,} entity records")

# Create record_id to context mapping
record_to_context = {}
for _, row in entities_df.iterrows():
    if pd.notna(row.get('context')):
        if 'record_id' in row and pd.notna(row['record_id']):
            record_id = row['record_id']
        elif 'File' in row and pd.notna(row['File']):
            record_id = row['File']
        else:
            continue
        record_to_context[record_id] = row['context']

print(f"Mapped {len(record_to_context):,} records to contexts")

# For synthetic records, use the context from the annotation data itself
for record_id in df[df['is_synthetic'] == True]['record_id'].unique():
    if record_id not in record_to_context:
        # Get context from first annotation of this synthetic record
        synth_rows = df[df['record_id'] == record_id]
        if len(synth_rows) > 0 and 'text' in synth_rows.columns:
            first_text = synth_rows.iloc[0]['text']
            if pd.notna(first_text):
                record_to_context[record_id] = first_text
        elif len(synth_rows) > 0 and 'context' in synth_rows.columns:
            first_context = synth_rows.iloc[0]['context']
            if pd.notna(first_context):
                record_to_context[record_id] = first_context

print(f"After adding synthetic contexts: {len(record_to_context):,} total")

# Group annotations by record
print("\nGrouping annotations by record...")
record_annotations = defaultdict(list)

for _, row in df.iterrows():
    record_id = row['record_id']
    if record_id in record_to_context:
        record_annotations[record_id].append({
            'entity_text': row['entity_text'],
            'entity_type': row['entity_type'],
            'start_pos': int(row['start_pos']) if pd.notna(row['start_pos']) else 0,
            'end_pos': int(row['end_pos']) if pd.notna(row['end_pos']) else 0,
        })

print(f"Grouped annotations for {len(record_annotations):,} records")

# Simple tokenization (split by whitespace and keep punctuation)
def simple_tokenize(text):
    """Simple Hebrew tokenization"""
    tokens = []
    current_token = ""
    
    for char in text:
        if char.isspace():
            if current_token:
                tokens.append(current_token)
                current_token = ""
        else:
            current_token += char
    
    if current_token:
        tokens.append(current_token)
    
    return tokens

def create_bio_labels(text, annotations, tokens):
    """Create BIO labels for tokens"""
    # Initialize all labels as 'O'
    labels = ['O'] * len(tokens)
    
    # Find token positions in text
    token_positions = []
    char_pos = 0
    for token in tokens:
        start = text.find(token, char_pos)
        if start == -1:
            # Token not found, approximate position
            token_positions.append((char_pos, char_pos + len(token)))
            char_pos += len(token) + 1
        else:
            end = start + len(token)
            token_positions.append((start, end))
            char_pos = end
    
    # Assign labels based on annotations
    for ann in annotations:
        ann_start = ann['start_pos']
        ann_end = ann['end_pos']
        entity_type = ann['entity_type']
        
        # Find tokens that overlap with this annotation
        is_first_token = True
        for i, (tok_start, tok_end) in enumerate(token_positions):
            # Check if token overlaps with annotation
            if tok_start < ann_end and tok_end > ann_start:
                if is_first_token:
                    labels[i] = f'B-{entity_type}'
                    is_first_token = False
                else:
                    labels[i] = f'I-{entity_type}'
    
    return labels

# Convert to BIO format
print("\nConverting to BIO format...")
training_examples = []
skipped = 0

for record_id, annotations in record_annotations.items():
    context = record_to_context.get(record_id)
    if not context or pd.isna(context):
        skipped += 1
        continue
    
    # Tokenize
    tokens = simple_tokenize(context)
    
    if len(tokens) == 0:
        skipped += 1
        continue
    
    # Create BIO labels
    labels = create_bio_labels(context, annotations, tokens)
    
    # Create training example
    training_examples.append({
        'record_id': record_id,
        'tokens': tokens,
        'labels': labels,
        'n_entities': len([l for l in labels if l.startswith('B-')])
    })

print(f"Created {len(training_examples):,} training examples")
print(f"Skipped {skipped:,} records (no context)")

# Statistics
print("\n" + "="*80)
print("BIO FORMAT STATISTICS")
print("="*80)
print(f"Total examples: {len(training_examples):,}")

# Count tokens and labels
total_tokens = sum(len(ex['tokens']) for ex in training_examples)
total_entities = sum(ex['n_entities'] for ex in training_examples)

print(f"Total tokens: {total_tokens:,}")
print(f"Total entities (B- tags): {total_entities:,}")
print(f"Average tokens per example: {total_tokens / len(training_examples):.1f}")
print(f"Average entities per example: {total_entities / len(training_examples):.1f}")

# Count label distribution
label_counts = defaultdict(int)
for ex in training_examples:
    for label in ex['labels']:
        label_counts[label] += 1

print("\nLabel distribution:")
for label in sorted(label_counts.keys()):
    count = label_counts[label]
    pct = 100 * count / total_tokens
    print(f"  {label:20s}: {count:8,} ({pct:5.2f}%)")

# Save to JSON
print(f"\nSaving to {OUTPUT_FILE}...")
with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(training_examples, f, ensure_ascii=False, indent=2)

print(f"\n✓ Training data prepared successfully!")
print(f"  File: {OUTPUT_FILE}")
print(f"  Examples: {len(training_examples):,}")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRAINING EXAMPLES (first 3)")
print("="*80)
for i, ex in enumerate(training_examples[:3], 1):
    print(f"\nExample {i}:")
    print(f"  Record ID: {ex['record_id']}")
    print(f"  Tokens: {len(ex['tokens'])}")
    print(f"  Entities: {ex['n_entities']}")
    
    # Show first 20 tokens with labels
    print(f"  First 20 tokens:")
    for j in range(min(20, len(ex['tokens']))):
        token = ex['tokens'][j]
        label = ex['labels'][j]
        if label != 'O':
            print(f"    [{label:15s}] {token}")
        else:
            print(f"    {' ':17s} {token}")
