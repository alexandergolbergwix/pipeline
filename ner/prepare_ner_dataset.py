"""
Script to prepare NER training dataset by extracting top 10,000 entities by context length.
Updated to expand dataset for improved model performance.
"""

import pandas as pd
import os

# File paths
INPUT_FILE = 'processed-data/filtered_data.csv'
OUTPUT_FILE = 'processed-data/top_10000_entities.csv'

print("="*80)
print("NER DATASET PREPARATION - Top 10,000 Entities Extraction")
print("="*80)

print(f"\nLoading filtered data from {INPUT_FILE}...")
df = pd.read_csv(INPUT_FILE, low_memory=False)
print(f"Loaded {len(df):,} records")

# Remove duplicates using '001' (MARC control number) as unique identifier
print("\nRemoving duplicate records...")
if '001' in df.columns:
    # Keep the row with longest context for each unique MARC 001
    df_unique = df.sort_values('context_length', ascending=False).drop_duplicates(subset='001', keep='first')
    print(f"Unique records by MARC 001: {len(df_unique):,}")
    # Add unique record_id column based on 001
    df_unique = df_unique.copy()
    df_unique['record_id'] = 'record_' + df_unique['001'].astype(str)
else:
    print("Warning: '001' column not found, using File column")
    df_unique = df.drop_duplicates(subset='File', keep='first')
    df_unique = df_unique.copy()
    df_unique['record_id'] = df_unique['File']

# Sort by context_length descending and take top 1,000
print("Sorting by context_length (descending)...")
df_sorted = df_unique.sort_values('context_length', ascending=False)

print("Extracting top 10,000 entities...")
top_10000 = df_sorted.head(10000).copy()

# Statistics
print("\n" + "="*80)
print("STATISTICS")
print("="*80)
print(f"Total entities selected: {len(top_10000):,}")
print(f"Unique MARC 001 IDs: {top_10000['001'].nunique() if '001' in top_10000.columns else 'N/A'}")
print(f"Unique record_ids: {top_10000['record_id'].nunique() if 'record_id' in top_10000.columns else 'N/A'}")
print(f"Average context length: {top_10000['context_length'].mean():.0f} characters")
print(f"Maximum context length: {top_10000['context_length'].max():,} characters")
print(f"Minimum context length: {top_10000['context_length'].min():,} characters")
print(f"Median context length: {top_10000['context_length'].median():.0f} characters")

# Check for required columns
required_cols = ['File', 'context', 'context_length', '100$a', '700$a', '751$a', 
                 '700$d', '260$c', '264$c', '245$a', '730$a', '740$a', '700$e', '710$a']
available_cols = [col for col in required_cols if col in top_10000.columns]
print(f"\nAvailable columns for entity extraction: {len(available_cols)}/{len(required_cols)}")

# Save to file
print(f"\nSaving to {OUTPUT_FILE}...")
os.makedirs('processed-data', exist_ok=True)
top_10000.to_csv(OUTPUT_FILE, index=False)

print("\n✓ Top 10,000 entities saved successfully!")
print(f"  File: {OUTPUT_FILE}")
print(f"  Size: {os.path.getsize(OUTPUT_FILE) / (1024*1024):.2f} MB")

