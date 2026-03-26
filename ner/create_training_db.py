"""
Script to create validated NER training database with MARC 001 field.
Includes validation scores and quality metrics.
"""

import pandas as pd
import sqlite3
import json
from pathlib import Path

# File paths
CSV_FILE = 'processed-data/ner_training_dataset_validated.csv'
DB_FILE = 'processed-data/ner_training_validated.db'
STATS_FILE = 'processed-data/validation_stats.json'

print("="*80)
print("CREATING VALIDATED NER TRAINING DATABASE")
print("="*80)

# Load the CSV data
print(f"\nLoading validated annotations from {CSV_FILE}...")
df = pd.read_csv(CSV_FILE, encoding='utf-8')
print(f"Loaded {len(df):,} annotations")

# Create SQLite database
print(f"\nCreating SQLite database: {DB_FILE}")
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# Create tables with validation fields
print("Creating database schema...")

# Table 1: Records with validation metrics
cursor.execute('''
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    marc_001 TEXT NOT NULL,
    text TEXT NOT NULL,
    text_length INTEGER,
    validation_score INTEGER,
    validation_rate REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Table 2: Annotations with validation scores
cursor.execute('''
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    marc_001 TEXT NOT NULL,
    entity_text TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    start_pos INTEGER NOT NULL,
    end_pos INTEGER NOT NULL,
    source_field TEXT,
    validation_score REAL NOT NULL,
    confidence TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (record_id) REFERENCES records(record_id)
)
''')

# Table 3: Entity types metadata
cursor.execute('''
CREATE TABLE IF NOT EXISTS entity_types (
    type_name TEXT PRIMARY KEY,
    description TEXT,
    source_fields TEXT,
    count INTEGER,
    avg_validation_score REAL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Create indexes
cursor.execute('CREATE INDEX IF NOT EXISTS idx_record_id ON annotations(record_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_marc_001 ON annotations(marc_001)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_entity_type ON annotations(entity_type)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_validation_score ON annotations(validation_score)')

print("✓ Database schema created")

# Insert unique records with validation metrics
print("\nInserting records...")
unique_records = df.groupby('record_id').agg({
    'marc_001': 'first',
    'text': 'first',
    'entity_text': 'count',
    'validation_score': 'mean'
}).reset_index()
unique_records.columns = ['record_id', 'marc_001', 'text', 'validation_score', 'validation_rate']
unique_records['text_length'] = unique_records['text'].str.len()

for _, row in unique_records.iterrows():
    cursor.execute('''
        INSERT OR REPLACE INTO records (record_id, marc_001, text, text_length, validation_score, validation_rate)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        row['record_id'],
        row['marc_001'],
        row['text'],
        row['text_length'],
        int(row['validation_score']),
        float(row['validation_rate'])
    ))

print(f"✓ Inserted {len(unique_records)} unique records")

# Insert annotations
print("Inserting annotations...")
for _, row in df.iterrows():
    cursor.execute('''
        INSERT INTO annotations (
            record_id, marc_001, entity_text, entity_type, 
            start_pos, end_pos, source_field, validation_score, confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        row['record_id'],
        row['marc_001'],
        row['entity_text'],
        row['entity_type'],
        row['start_pos'],
        row['end_pos'],
        row['source_field'],
        row['validation_score'],
        row['confidence']
    ))

print(f"✓ Inserted {len(df):,} annotations")

# Insert entity type metadata
print("Inserting entity type metadata...")
entity_metadata = {
    'PERSON': {
        'description': 'Names of scribes, authors, mentioned persons',
        'source_fields': '100$a, 700$a'
    },
    'PLACE': {
        'description': 'Geographic locations',
        'source_fields': '751$a, 260$a, 264$a'
    },
    'DATE': {
        'description': 'Dates and time spans',
        'source_fields': '700$d, 260$c, 264$c, 046$a/b/d'
    },
    'WORK': {
        'description': 'Titles of works and texts',
        'source_fields': '245$a, 730$a, 740$a'
    },
    'ROLE': {
        'description': 'Participation roles',
        'source_fields': '700$e, 100$e'
    },
    'ORGANIZATION': {
        'description': 'Institutions and libraries',
        'source_fields': '710$a, 110$a'
    }
}

# Calculate stats from actual data
type_stats = df.groupby('entity_type').agg({
    'entity_text': 'count',
    'validation_score': 'mean'
}).reset_index()

for _, row in type_stats.iterrows():
    entity_type = row['entity_type']
    metadata = entity_metadata.get(entity_type, {})
    cursor.execute('''
        INSERT OR REPLACE INTO entity_types (type_name, description, source_fields, count, avg_validation_score)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        entity_type,
        metadata.get('description', ''),
        metadata.get('source_fields', ''),
        int(row['entity_text']),
        float(row['validation_score'])
    ))

print(f"✓ Inserted {len(type_stats)} entity type definitions")

# Commit changes
conn.commit()

# Verify database
print("\n" + "="*80)
print("DATABASE VERIFICATION")
print("="*80)

cursor.execute('SELECT COUNT(*) FROM records')
record_count = cursor.fetchone()[0]
print(f"Records: {record_count:,}")

cursor.execute('SELECT COUNT(*) FROM annotations')
annotation_count = cursor.fetchone()[0]
print(f"Annotations: {annotation_count:,}")

cursor.execute('SELECT COUNT(*) FROM entity_types')
type_count = cursor.fetchone()[0]
print(f"Entity types: {type_count}")

# Validation statistics
print("\n" + "="*80)
print("VALIDATION METRICS")
print("="*80)

cursor.execute('''
    SELECT 
        AVG(validation_score) as avg_score,
        MIN(validation_score) as min_score,
        MAX(validation_score) as max_score
    FROM annotations
''')
row = cursor.fetchone()
print(f"Validation scores:")
print(f"  Average: {row[0]:.3f}")
print(f"  Minimum: {row[1]:.3f}")
print(f"  Maximum: {row[2]:.3f}")

cursor.execute('''
    SELECT COUNT(*) 
    FROM annotations 
    WHERE validation_score >= 0.9
''')
high_quality = cursor.fetchone()[0]
print(f"  High quality (≥0.9): {high_quality:,} ({high_quality/annotation_count*100:.1f}%)")

# Sample queries
print("\n" + "="*80)
print("SAMPLE QUERIES")
print("="*80)

print("\n1. Entity type distribution:")
cursor.execute('''
    SELECT entity_type, COUNT(*) as count, 
           ROUND(AVG(validation_score), 3) as avg_score,
           ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM annotations), 2) as percentage
    FROM annotations
    GROUP BY entity_type
    ORDER BY count DESC
''')
for row in cursor.fetchall():
    print(f"   {row[0]:15s}: {row[1]:6,} (avg score: {row[2]:.3f}, {row[3]:5.2f}%)")

print("\n2. Top 5 records by annotation count:")
cursor.execute('''
    SELECT r.record_id, r.marc_001, r.validation_score, 
           COUNT(a.id) as num_annotations
    FROM records r
    JOIN annotations a ON r.record_id = a.record_id
    GROUP BY r.record_id
    ORDER BY num_annotations DESC
    LIMIT 5
''')
for row in cursor.fetchall():
    print(f"   MARC 001: {row[1][:30]:30s} - {row[3]:,} annotations (score: {row[2]})")

print("\n3. Sample high-quality entities (validation ≥ 0.95):")
cursor.execute('''
    SELECT entity_text, entity_type, validation_score, marc_001
    FROM annotations
    WHERE validation_score >= 0.95
    ORDER BY validation_score DESC
    LIMIT 5
''')
for row in cursor.fetchall():
    entity_display = row[0][:40] + '...' if len(row[0]) > 40 else row[0]
    print(f"   {entity_display:40s} ({row[1]}, score: {row[2]:.3f})")

# Close connection
conn.close()

print("\n" + "="*80)
print("DATABASE CREATION COMPLETE")
print("="*80)
print(f"✓ Database saved to: {DB_FILE}")
print(f"  Size: {Path(DB_FILE).stat().st_size / (1024*1024):.2f} MB")
print(f"  Records: {record_count:,}")
print(f"  Annotations: {annotation_count:,}")
print(f"\nDatabase includes:")
print("  - MARC 001 field (origin ID)")
print("  - Validation scores (0.7-1.0)")
print("  - Quality metrics per record")
print("  - Indexed for fast queries")

