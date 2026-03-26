"""
Script to balance entity distribution for better NER training.
Combines real and synthetic data, applies oversampling to minority classes.
"""

import pandas as pd
import numpy as np
from collections import Counter

print("="*80)
print("BALANCING NER TRAINING DATASET")
print("="*80)

# Load real annotations
print("\nLoading real validated annotations...")
df_real = pd.read_csv('processed-data/ner_training_dataset_validated.csv')
print(f"Real annotations: {len(df_real):,}")
print(f"Real records: {df_real['record_id'].nunique():,}")

# Load synthetic annotations
print("\nLoading synthetic annotations...")
df_synthetic = pd.read_csv('processed-data/synthetic_annotations.csv')
print(f"Synthetic annotations: {len(df_synthetic):,}")
print(f"Synthetic records: {df_synthetic['record_id'].nunique():,}")

# Display current distribution
print("\n" + "="*80)
print("CURRENT DISTRIBUTION (Real Data)")
print("="*80)
real_dist = df_real['entity_type'].value_counts()
for entity_type, count in real_dist.items():
    pct = 100 * count / len(df_real)
    print(f"{entity_type:15s}: {count:6,} ({pct:5.1f}%)")

print("\n" + "="*80)
print("SYNTHETIC DISTRIBUTION")
print("="*80)
synth_dist = df_synthetic['entity_type'].value_counts()
for entity_type, count in synth_dist.items():
    pct = 100 * count / len(df_synthetic)
    print(f"{entity_type:15s}: {count:6,} ({pct:5.1f}%)")

# Target distribution (from plan)
target_distribution = {
    'WORK': 0.25,
    'PERSON': 0.25,
    'DATE': 0.15,
    'PLACE': 0.15,
    'ROLE': 0.10,
    'ORGANIZATION': 0.10
}

print("\n" + "="*80)
print("TARGET DISTRIBUTION")
print("="*80)
for entity_type, pct in target_distribution.items():
    print(f"{entity_type:15s}: {pct*100:5.1f}%")

# Combine datasets
print("\n" + "="*80)
print("COMBINING DATASETS")
print("="*80)

# Standardize column names
# Real data has 'text', synthetic has 'context'
if 'text' in df_real.columns and 'context' not in df_real.columns:
    df_real['context'] = df_real['text']
if 'context' in df_synthetic.columns and 'text' not in df_synthetic.columns:
    df_synthetic['text'] = df_synthetic['context']

# Ensure consistent columns
required_cols = ['record_id', 'marc_001', 'entity_text', 
                 'entity_type', 'start_pos', 'end_pos', 'validation_score']

# Add is_synthetic column if missing
if 'is_synthetic' not in df_real.columns:
    df_real['is_synthetic'] = False

# Align columns - ensure all required columns exist
for col in required_cols:
    if col not in df_synthetic.columns:
        if col == 'marc_001':
            df_synthetic['marc_001'] = df_synthetic['record_id']
        else:
            df_synthetic[col] = None
    if col not in df_real.columns:
        df_real[col] = None

# Select only required columns
df_combined = pd.concat([
    df_real[required_cols + ['is_synthetic']],
    df_synthetic[required_cols + ['is_synthetic']]
], ignore_index=True)

print(f"Combined annotations: {len(df_combined):,}")
print(f"Combined unique records: {df_combined['record_id'].nunique():,}")

print("\nCombined distribution:")
combined_dist = df_combined['entity_type'].value_counts()
for entity_type, count in combined_dist.items():
    pct = 100 * count / len(df_combined)
    print(f"{entity_type:15s}: {count:6,} ({pct:5.1f}%)")

# Calculate oversampling factors
print("\n" + "="*80)
print("CALCULATING OVERSAMPLING FACTORS")
print("="*80)

# Determine target total count (aim for ~150k annotations)
target_total = 150000

# Calculate how many of each type we need
target_counts = {k: int(v * target_total) for k, v in target_distribution.items()}

print("Target counts:")
for entity_type, count in target_counts.items():
    print(f"{entity_type:15s}: {count:6,}")

# Apply oversampling
print("\n" + "="*80)
print("APPLYING OVERSAMPLING")
print("="*80)

balanced_dfs = []

for entity_type in target_distribution.keys():
    df_type = df_combined[df_combined['entity_type'] == entity_type].copy()
    current_count = len(df_type)
    target_count = target_counts[entity_type]
    
    if current_count == 0:
        print(f"{entity_type:15s}: No examples, skipping")
        continue
    
    if current_count < target_count:
        # Oversample
        oversample_factor = target_count / current_count
        n_additional = target_count - current_count
        df_oversampled = df_type.sample(n=n_additional, replace=True, random_state=42)
        df_balanced = pd.concat([df_type, df_oversampled], ignore_index=True)
        print(f"{entity_type:15s}: {current_count:6,} -> {len(df_balanced):6,} (x{oversample_factor:.1f})")
    else:
        # Undersample
        df_balanced = df_type.sample(n=target_count, replace=False, random_state=42)
        print(f"{entity_type:15s}: {current_count:6,} -> {len(df_balanced):6,} (sampled)")
    
    balanced_dfs.append(df_balanced)

# Combine balanced data
df_final = pd.concat(balanced_dfs, ignore_index=True)

# Shuffle
df_final = df_final.sample(frac=1, random_state=42).reset_index(drop=True)

print("\n" + "="*80)
print("FINAL BALANCED DISTRIBUTION")
print("="*80)
print(f"Total annotations: {len(df_final):,}")
print(f"Unique records: {df_final['record_id'].nunique():,}")

final_dist = df_final['entity_type'].value_counts()
for entity_type in target_distribution.keys():
    count = final_dist.get(entity_type, 0)
    pct = 100 * count / len(df_final)
    target_pct = target_distribution[entity_type] * 100
    print(f"{entity_type:15s}: {count:6,} ({pct:5.1f}% | target: {target_pct:4.1f}%)")

print(f"\nSynthetic annotations: {df_final['is_synthetic'].sum():,} ({100*df_final['is_synthetic'].sum()/len(df_final):.1f}%)")
print(f"Real annotations: {(~df_final['is_synthetic']).sum():,} ({100*(~df_final['is_synthetic']).sum()/len(df_final):.1f}%)")

# Save balanced dataset
output_file = 'processed-data/ner_training_dataset_balanced.csv'
df_final.to_csv(output_file, index=False)
print(f"\n✓ Balanced dataset saved to: {output_file}")

# Calculate class weights for training
print("\n" + "="*80)
print("SUGGESTED CLASS WEIGHTS FOR TRAINING")
print("="*80)
print("Use these in the training script to further balance learning:")

# Get actual distribution
actual_counts = df_final['entity_type'].value_counts()
total = len(df_final)

# Calculate inverse frequency weights
max_count = actual_counts.max()
class_weights = {}

entity_to_id = {
    'O': 0,
    'B-PERSON': 1, 'I-PERSON': 2,
    'B-PLACE': 3, 'I-PLACE': 4,
    'B-DATE': 5, 'I-DATE': 6,
    'B-WORK': 7, 'I-WORK': 8,
    'B-ROLE': 9, 'I-ROLE': 10,
    'B-ORGANIZATION': 11, 'I-ORGANIZATION': 12
}

# O tag weight (default)
class_weights[0] = 1.0

# Calculate weights for each entity type
for entity_type, count in actual_counts.items():
    weight = max_count / count
    # B- and I- tags get similar weights
    b_tag = f'B-{entity_type}'
    i_tag = f'I-{entity_type}'
    if b_tag in entity_to_id:
        class_weights[entity_to_id[b_tag]] = weight
        class_weights[entity_to_id[i_tag]] = weight

print("\nclass_weights = {")
for label_id in sorted(class_weights.keys()):
    print(f"    {label_id}: {class_weights[label_id]:.2f},")
print("}")

# Show sample
print("\n" + "="*80)
print("SAMPLE BALANCED EXAMPLES (first 10)")
print("="*80)
for i, row in df_final.head(10).iterrows():
    source = "SYNTH" if row['is_synthetic'] else "REAL "
    print(f"{i+1:2d}. [{source}] {row['entity_type']:12s}: {row['entity_text'][:40]}")
