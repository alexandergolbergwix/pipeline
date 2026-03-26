"""
Script to process National Library of Israel MARC entities data.
Filters out specific entity types and finds top 100 entities with longest context.
"""

import pandas as pd
import numpy as np

# File paths
INPUT_FILE = 'raw-data/full-data.csv'
FILTERED_OUTPUT_FILE = 'processed-data/filtered_data.csv'
TOP_100_OUTPUT_FILE = 'processed-data/top_100_entities.csv'

print("Loading raw data...")
df_full = pd.read_csv(INPUT_FILE, low_memory=False)
print(f"Loaded {len(df_full):,} records")

# Apply filter: exclude records where 906$a contains GNZ, KET, or BND
print("\nApplying filter to exclude GNZ, KET, BND...")
filtered_df = df_full[~df_full['906$a'].str.contains('GNZ|KET|BND', na=False)].copy()
print(f"After filtering: {len(filtered_df):,} records ({len(df_full) - len(filtered_df):,} excluded)")

# Create context column by concatenating 957$a, 500$a, and 561$a
print("\nCreating context from columns 957$a, 500$a, 561$a...")
context_columns = ['957$a', '500$a', '561$a']

# Handle missing values and concatenate
filtered_df['context'] = filtered_df[context_columns].fillna('').agg(' '.join, axis=1)

# Calculate context length
filtered_df['context_length'] = filtered_df['context'].str.len()

# Save filtered dataframe
print(f"\nSaving filtered data to {FILTERED_OUTPUT_FILE}...")
import os
os.makedirs('processed-data', exist_ok=True)
filtered_df.to_csv(FILTERED_OUTPUT_FILE, index=False)
print(f"Saved {len(filtered_df):,} filtered records")

# Sort by context length and get top 100
print("\nFinding top 100 entities with longest context...")
top_100 = filtered_df.nlargest(100, 'context_length')

# Display statistics
print(f"\nTop 100 Statistics:")
print(f"Average context length: {top_100['context_length'].mean():.0f} characters")
print(f"Maximum context length: {top_100['context_length'].max()} characters")
print(f"Minimum context length: {top_100['context_length'].min()} characters")

# Save top 100 results
print(f"\nSaving top 100 to {TOP_100_OUTPUT_FILE}...")
top_100.to_csv(TOP_100_OUTPUT_FILE, index=False)

# Display sample of top 5
print("\nTop 5 entities by context length:")
print(top_100[['context_length']].head().to_string())

print("\n✓ Processing complete!")

