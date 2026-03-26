"""
Script to extract and validate entities with 70% fuzzy matching.
Only keeps entities that appear in BOTH structured fields AND context.
"""

import pandas as pd
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
try:
    from Levenshtein import ratio as lev_ratio
    USE_LEVENSHTEIN = True
except ImportError:
    USE_LEVENSHTEIN = False
    print("Warning: python-Levenshtein not available, using difflib (slower)")

# File paths
INPUT_FILE = 'processed-data/top_10000_entities.csv'
OUTPUT_FILE = 'processed-data/entity_mappings_validated.json'
STATS_FILE = 'processed-data/validation_stats.json'

# Fuzzy matching threshold (lowered from 0.7 to 0.6 for more coverage)
MATCH_THRESHOLD = 0.6

def fuzzy_match_score(a, b):
    """Calculate similarity score between two strings (0.0 to 1.0)."""
    if not a or not b:
        return 0.0
    
    a_norm = a.lower().strip()
    b_norm = b.lower().strip()
    
    if a_norm == b_norm:
        return 1.0
    
    if USE_LEVENSHTEIN:
        return lev_ratio(a_norm, b_norm)
    else:
        return SequenceMatcher(None, a_norm, b_norm).ratio()

def find_best_match_in_context(entity, context, threshold=MATCH_THRESHOLD):
    """
    Find best match for entity in context using fuzzy matching.
    Returns (match_score, start_pos, end_pos) or None if no match above threshold.
    """
    if not context or not entity:
        return None
    
    entity_len = len(entity)
    context_lower = context.lower()
    best_score = 0
    best_pos = None
    
    # Try exact match first
    pos = context_lower.find(entity.lower())
    if pos != -1:
        return (1.0, pos, pos + entity_len)
    
    # Sliding window fuzzy match
    words = context.split()
    for i in range(len(words)):
        for j in range(i + 1, min(i + 10, len(words) + 1)):  # Check up to 10 words
            candidate = ' '.join(words[i:j])
            score = fuzzy_match_score(entity, candidate)
            
            if score >= threshold and score > best_score:
                best_score = score
                # Find position in original context
                start = context.find(candidate)
                if start != -1:
                    best_pos = (best_score, start, start + len(candidate))
    
    return best_pos

def clean_entity_text(text):
    """Clean entity text by removing extra whitespace and special characters."""
    if pd.isna(text) or text == '':
        return None
    text = str(text).strip()
    text = text.strip('"').strip("'")
    text = re.sub(r'\s+', ' ', text)
    return text if text else None

def split_multivalue_field(text, delimiter='|'):
    """Split multi-value fields separated by delimiter."""
    if pd.isna(text) or text == '':
        return []
    values = str(text).split(delimiter)
    return [clean_entity_text(v) for v in values if clean_entity_text(v)]

print("="*80)
print("VALIDATED ENTITY EXTRACTION WITH FUZZY MATCHING")
print("="*80)
print(f"Match threshold: {MATCH_THRESHOLD*100:.0f}% (lowered from 70% for better coverage)")
print(f"Using: {'Levenshtein' if USE_LEVENSHTEIN else 'difflib'}")

# Load data
print(f"\nLoading data from {INPUT_FILE}...")
df = pd.read_csv(INPUT_FILE, low_memory=False)
print(f"Loaded {len(df):,} records")

# Check for 001 field
if '001' not in df.columns:
    print("Warning: '001' column not found in data!")
    df['001'] = df['File']  # Use File as fallback

# Entity extraction functions
def extract_person_names(row):
    persons = []
    if pd.notna(row.get('100$a')):
        persons.extend(split_multivalue_field(row['100$a']))
    if pd.notna(row.get('700$a')):
        persons.extend(split_multivalue_field(row['700$a']))
    return persons

def extract_places(row):
    places = []
    for field in ['751$a', '260$a', '264$a']:
        if pd.notna(row.get(field)):
            places.extend(split_multivalue_field(row[field]))
    return places

def extract_dates(row):
    dates = []
    for field in ['700$d', '260$c', '264$c', '046$a', '046$b', '046$d']:
        if pd.notna(row.get(field)):
            dates.extend(split_multivalue_field(row[field]))
    return dates

def extract_works(row):
    works = []
    for field in ['245$a', '130$a', '730$a', '740$a']:
        if pd.notna(row.get(field)):
            works.extend(split_multivalue_field(row[field]))
    return works

def extract_roles(row):
    roles = []
    for field in ['700$e', '100$e']:
        if pd.notna(row.get(field)):
            roles.extend(split_multivalue_field(row[field]))
    return roles

def extract_organizations(row):
    orgs = []
    for field in ['710$a', '110$a']:
        if pd.notna(row.get(field)):
            orgs.extend(split_multivalue_field(row[field]))
    return orgs

# Extract and validate entities
print("\nExtracting and validating entities...")
validated_entities = {}
validation_stats = {
    'total_records': len(df),
    'records_with_context': 0,
    'records_with_validated_entities': 0,
    'total_extracted': defaultdict(int),
    'total_validated': defaultdict(int),
    'validation_rates': {}
}

for idx, row in df.iterrows():
    if idx % 100 == 0:
        print(f"  Processing {idx}/{len(df)}...")
    
    # Use record_id if available, otherwise fall back to File or 001
    if 'record_id' in row and pd.notna(row['record_id']):
        record_id = row['record_id']
    elif 'File' in row and pd.notna(row['File']):
        record_id = row['File']
    else:
        record_id = f"record_{idx}"
    
    marc_001 = row.get('001', record_id)
    context = row.get('context', '')
    
    if pd.isna(context) or context == '':
        continue
    
    validation_stats['records_with_context'] += 1
    context = str(context)
    
    # Extract entities by type
    entity_types = {
        'PERSON': extract_person_names(row),
        'PLACE': extract_places(row),
        'DATE': extract_dates(row),
        'WORK': extract_works(row),
        'ROLE': extract_roles(row),
        'ORGANIZATION': extract_organizations(row)
    }
    
    # Validate each entity in context
    record_validated = {
        'marc_001': marc_001,
        'entities': {}
    }
    
    has_validated = False
    
    for entity_type, entities in entity_types.items():
        validated_list = []
        
        for entity in entities:
            if not entity or len(entity) < 2:
                continue
            
            validation_stats['total_extracted'][entity_type] += 1
            
            # Find entity in context with fuzzy matching
            match_result = find_best_match_in_context(entity, context, MATCH_THRESHOLD)
            
            if match_result:
                score, start_pos, end_pos = match_result
                validated_list.append({
                    'text': entity,
                    'positions': [(start_pos, end_pos)],
                    'validation_score': score
                })
                validation_stats['total_validated'][entity_type] += 1
                has_validated = True
        
        if validated_list:
            record_validated['entities'][entity_type] = validated_list
    
    if has_validated:
        validated_entities[record_id] = record_validated
        validation_stats['records_with_validated_entities'] += 1

print(f"  Processing {len(df)}/{len(df)}... Done!")

# Calculate validation rates
print("\n" + "="*80)
print("VALIDATION STATISTICS")
print("="*80)
print(f"Total records: {validation_stats['total_records']:,}")
print(f"Records with context: {validation_stats['records_with_context']:,}")
print(f"Records with validated entities: {validation_stats['records_with_validated_entities']:,}")

print("\nEntity validation by type:")
for entity_type in ['PERSON', 'PLACE', 'DATE', 'WORK', 'ROLE', 'ORGANIZATION']:
    extracted = validation_stats['total_extracted'][entity_type]
    validated = validation_stats['total_validated'][entity_type]
    rate = (validated / extracted * 100) if extracted > 0 else 0
    validation_stats['validation_rates'][entity_type] = rate
    print(f"  {entity_type:15s}: {validated:6,} / {extracted:6,} ({rate:5.1f}%)")

total_extracted = sum(validation_stats['total_extracted'].values())
total_validated = sum(validation_stats['total_validated'].values())
overall_rate = (total_validated / total_extracted * 100) if total_extracted > 0 else 0
validation_stats['validation_rates']['OVERALL'] = overall_rate
print(f"  {'OVERALL':15s}: {total_validated:6,} / {total_extracted:6,} ({overall_rate:5.1f}%)")

# Save validated entities
print(f"\nSaving validated entities to {OUTPUT_FILE}...")
with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(validated_entities, f, ensure_ascii=False, indent=2)

# Save statistics
print(f"Saving statistics to {STATS_FILE}...")
with open(STATS_FILE, 'w', encoding='utf-8') as f:
    json.dump(validation_stats, f, ensure_ascii=False, indent=2)

print("\n✓ Validated entity extraction complete!")
print(f"  Validated entities: {OUTPUT_FILE}")
print(f"  Statistics: {STATS_FILE}")

