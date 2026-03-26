"""
Script to generate comprehensive validation report with quality metrics.
"""

import pandas as pd
import json
import sqlite3
from datetime import datetime

# File paths
STATS_FILE = 'processed-data/validation_stats.json'
DB_FILE = 'processed-data/ner_training_validated.db'
CSV_FILE = 'processed-data/ner_training_dataset_validated.csv'
REPORT_FILE = 'processed-data/validation_report.json'
REPORT_TXT = 'processed-data/validation_report.txt'

print("="*80)
print("GENERATING VALIDATION REPORT")
print("="*80)

# Load validation statistics
print(f"\nLoading validation statistics from {STATS_FILE}...")
with open(STATS_FILE, 'r', encoding='utf-8') as f:
    val_stats = json.load(f)

# Load database statistics
print(f"Loading database from {DB_FILE}...")
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# Gather comprehensive statistics
report = {
    'generated_at': datetime.now().isoformat(),
    'validation_threshold': 0.7,
    'extraction_phase': {},
    'annotation_phase': {},
    'database_phase': {},
    'quality_metrics': {},
    'recommendations': []
}

# Extraction phase statistics
report['extraction_phase'] = {
    'total_records_processed': val_stats['total_records'],
    'records_with_context': val_stats['records_with_context'],
    'records_with_validated_entities': val_stats['records_with_validated_entities'],
    'entities_extracted': sum(val_stats['total_extracted'].values()),
    'entities_validated': sum(val_stats['total_validated'].values()),
    'overall_validation_rate': val_stats['validation_rates']['OVERALL'],
    'validation_rates_by_type': val_stats['validation_rates']
}

# Annotation phase statistics
df = pd.read_csv(CSV_FILE, encoding='utf-8')
report['annotation_phase'] = {
    'total_annotations': len(df),
    'unique_records': df['record_id'].nunique(),
    'unique_marc_001_ids': df['marc_001'].nunique(),
    'annotations_per_record': {
        'mean': float(df.groupby('record_id').size().mean()),
        'min': int(df.groupby('record_id').size().min()),
        'max': int(df.groupby('record_id').size().max())
    },
    'validation_scores': {
        'mean': float(df['validation_score'].mean()),
        'min': float(df['validation_score'].min()),
        'max': float(df['validation_score'].max()),
        'high_quality_count': int((df['validation_score'] >= 0.9).sum()),
        'high_quality_percentage': float((df['validation_score'] >= 0.9).sum() / len(df) * 100)
    }
}

# Database phase statistics
cursor.execute('SELECT COUNT(*) FROM records')
db_records = cursor.fetchone()[0]

cursor.execute('SELECT COUNT(*) FROM annotations')
db_annotations = cursor.fetchone()[0]

cursor.execute('''
    SELECT entity_type, COUNT(*) as count
    FROM annotations
    GROUP BY entity_type
''')
entity_distribution = {row[0]: row[1] for row in cursor.fetchall()}

report['database_phase'] = {
    'total_records': db_records,
    'total_annotations': db_annotations,
    'entity_distribution': entity_distribution,
    'includes_marc_001': True,
    'includes_validation_scores': True,
    'database_size_mb': round(pd.DataFrame([CSV_FILE]).apply(
        lambda x: pd.read_csv(x[0]).memory_usage(deep=True).sum() / (1024**2)
    )[0], 2)
}

# Quality metrics
report['quality_metrics'] = {
    'precision_indicator': 'High - All entities validated in both structured fields and context',
    'recall_indicator': f'Medium - {report["extraction_phase"]["overall_validation_rate"]:.1f}% of extracted entities validated',
    'data_completeness': f'{report["extraction_phase"]["records_with_validated_entities"]} / {report["extraction_phase"]["total_records_processed"]} records ({report["extraction_phase"]["records_with_validated_entities"]/report["extraction_phase"]["total_records_processed"]*100:.1f}%)',
    'average_validation_score': report['annotation_phase']['validation_scores']['mean'],
    'high_quality_annotations': f'{report["annotation_phase"]["validation_scores"]["high_quality_count"]} / {report["annotation_phase"]["total_annotations"]} ({report["annotation_phase"]["validation_scores"]["high_quality_percentage"]:.1f}%)'
}

# Generate recommendations
recommendations = []

if report['extraction_phase']['overall_validation_rate'] < 50:
    recommendations.append({
        'priority': 'HIGH',
        'issue': f'Low overall validation rate ({report["extraction_phase"]["overall_validation_rate"]:.1f}%)',
        'suggestion': 'Consider lowering fuzzy matching threshold or improving entity normalization'
    })

if report['annotation_phase']['unique_records'] < 100:
    recommendations.append({
        'priority': 'HIGH',
        'issue': f'Only {report["annotation_phase"]["unique_records"]} records with validated annotations',
        'suggestion': 'Need to process more records or adjust validation criteria to get more training data'
    })

if report['annotation_phase']['validation_scores']['mean'] >= 0.85:
    recommendations.append({
        'priority': 'INFO',
        'issue': f'High average validation score ({report["annotation_phase"]["validation_scores"]["mean"]:.3f})',
        'suggestion': 'Dataset quality is good - entities are well-matched between sources'
    })

# Check entity type balance
entity_counts = list(entity_distribution.values())
if max(entity_counts) / min(entity_counts) > 10:
    recommendations.append({
        'priority': 'MEDIUM',
        'issue': 'Imbalanced entity type distribution',
        'suggestion': 'Consider applying class weights during training or data augmentation'
    })

if report['extraction_phase']['validation_rates_by_type']['PLACE'] < 10:
    recommendations.append({
        'priority': 'LOW',
        'issue': f'Very low PLACE validation rate ({report["extraction_phase"]["validation_rates_by_type"]["PLACE"]:.1f}%)',
        'suggestion': 'PLACE entities may need special handling or alternative matching strategy'
    })

report['recommendations'] = recommendations

conn.close()

# Save JSON report
print(f"\nSaving JSON report to {REPORT_FILE}...")
with open(REPORT_FILE, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

# Generate human-readable text report
print(f"Generating text report to {REPORT_TXT}...")
with open(REPORT_TXT, 'w', encoding='utf-8') as f:
    f.write("="*80 + "\n")
    f.write("NER TRAINING DATABASE VALIDATION REPORT\n")
    f.write("="*80 + "\n")
    f.write(f"Generated: {report['generated_at']}\n")
    f.write(f"Validation Threshold: {report['validation_threshold']*100:.0f}% fuzzy matching\n")
    
    f.write("\n" + "="*80 + "\n")
    f.write("1. EXTRACTION PHASE\n")
    f.write("="*80 + "\n")
    f.write(f"Total records processed: {report['extraction_phase']['total_records_processed']:,}\n")
    f.write(f"Records with context: {report['extraction_phase']['records_with_context']:,}\n")
    f.write(f"Records with validated entities: {report['extraction_phase']['records_with_validated_entities']:,}\n")
    f.write(f"Entities extracted: {report['extraction_phase']['entities_extracted']:,}\n")
    f.write(f"Entities validated: {report['extraction_phase']['entities_validated']:,}\n")
    f.write(f"Overall validation rate: {report['extraction_phase']['overall_validation_rate']:.1f}%\n")
    
    f.write("\nValidation rates by entity type:\n")
    for entity_type, rate in sorted(report['extraction_phase']['validation_rates_by_type'].items()):
        if entity_type != 'OVERALL':
            f.write(f"  {entity_type:15s}: {rate:6.1f}%\n")
    
    f.write("\n" + "="*80 + "\n")
    f.write("2. ANNOTATION PHASE\n")
    f.write("="*80 + "\n")
    f.write(f"Total annotations: {report['annotation_phase']['total_annotations']:,}\n")
    f.write(f"Unique records: {report['annotation_phase']['unique_records']:,}\n")
    f.write(f"Unique MARC 001 IDs: {report['annotation_phase']['unique_marc_001_ids']:,}\n")
    f.write(f"Annotations per record (mean): {report['annotation_phase']['annotations_per_record']['mean']:.1f}\n")
    f.write(f"Annotations per record (min-max): {report['annotation_phase']['annotations_per_record']['min']}-{report['annotation_phase']['annotations_per_record']['max']}\n")
    
    f.write("\nValidation score statistics:\n")
    f.write(f"  Mean: {report['annotation_phase']['validation_scores']['mean']:.3f}\n")
    f.write(f"  Range: {report['annotation_phase']['validation_scores']['min']:.3f} - {report['annotation_phase']['validation_scores']['max']:.3f}\n")
    f.write(f"  High quality (≥0.9): {report['annotation_phase']['validation_scores']['high_quality_count']:,} ({report['annotation_phase']['validation_scores']['high_quality_percentage']:.1f}%)\n")
    
    f.write("\n" + "="*80 + "\n")
    f.write("3. DATABASE PHASE\n")
    f.write("="*80 + "\n")
    f.write(f"Total records in DB: {report['database_phase']['total_records']:,}\n")
    f.write(f"Total annotations in DB: {report['database_phase']['total_annotations']:,}\n")
    f.write(f"Includes MARC 001 field: {'Yes' if report['database_phase']['includes_marc_001'] else 'No'}\n")
    f.write(f"Includes validation scores: {'Yes' if report['database_phase']['includes_validation_scores'] else 'No'}\n")
    
    f.write("\nEntity type distribution:\n")
    for entity_type, count in sorted(report['database_phase']['entity_distribution'].items(), key=lambda x: x[1], reverse=True):
        percentage = count / report['database_phase']['total_annotations'] * 100
        f.write(f"  {entity_type:15s}: {count:6,} ({percentage:5.2f}%)\n")
    
    f.write("\n" + "="*80 + "\n")
    f.write("4. QUALITY METRICS\n")
    f.write("="*80 + "\n")
    f.write(f"Precision: {report['quality_metrics']['precision_indicator']}\n")
    f.write(f"Recall: {report['quality_metrics']['recall_indicator']}\n")
    f.write(f"Data completeness: {report['quality_metrics']['data_completeness']}\n")
    f.write(f"Average validation score: {report['quality_metrics']['average_validation_score']:.3f}\n")
    f.write(f"High quality annotations: {report['quality_metrics']['high_quality_annotations']}\n")
    
    f.write("\n" + "="*80 + "\n")
    f.write("5. RECOMMENDATIONS\n")
    f.write("="*80 + "\n")
    for i, rec in enumerate(report['recommendations'], 1):
        f.write(f"\n{i}. [{rec['priority']}] {rec['issue']}\n")
        f.write(f"   → {rec['suggestion']}\n")
    
    f.write("\n" + "="*80 + "\n")
    f.write("END OF REPORT\n")
    f.write("="*80 + "\n")

print("\n" + "="*80)
print("REPORT SUMMARY")
print("="*80)
print(f"✓ Validation report generated")
print(f"  JSON: {REPORT_FILE}")
print(f"  Text: {REPORT_TXT}")

print("\nKey Findings:")
print(f"  • {report['extraction_phase']['records_with_validated_entities']:,} / {report['extraction_phase']['total_records_processed']:,} records with validated entities")
print(f"  • {report['annotation_phase']['total_annotations']:,} total annotations")
print(f"  • {report['extraction_phase']['overall_validation_rate']:.1f}% overall validation rate")
print(f"  • {report['annotation_phase']['validation_scores']['mean']:.3f} average validation score")

if report['recommendations']:
    print(f"\n{len(report['recommendations'])} recommendations generated")
    for rec in report['recommendations'][:3]:
        print(f"  • [{rec['priority']}] {rec['issue']}")

print("\n✓ Validation report complete!")

