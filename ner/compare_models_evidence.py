#!/usr/bin/env python3
"""
Compare DictaBERT (general NER) vs Fine-tuned model on manuscript test data.
Find specific examples where DictaBERT fails and our model succeeds.
"""

import json
import torch
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline
from collections import defaultdict

# Load test data
def load_test_data(path, max_samples=500):
    samples = []
    with open(path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if i >= max_samples:
                break
            data = json.loads(line)
            samples.append(data)
    return samples

# Extract entities from BIO tags
def extract_entities_from_bio(tokens, tags):
    entities = []
    current_entity = []
    current_type = None
    
    for token, tag in zip(tokens, tags):
        if tag.startswith('B-'):
            if current_entity:
                entities.append((' '.join(current_entity), current_type))
            current_entity = [token]
            current_type = tag[2:]
        elif tag.startswith('I-') and current_entity:
            current_entity.append(token)
        else:
            if current_entity:
                entities.append((' '.join(current_entity), current_type))
            current_entity = []
            current_type = None
    
    if current_entity:
        entities.append((' '.join(current_entity), current_type))
    
    return entities

# Run DictaBERT NER (general Hebrew NER)
def run_dictabert_ner(text, ner_pipeline):
    try:
        results = ner_pipeline(text)
        entities = []
        for r in results:
            entity_text = r['word'].replace('##', '')
            entity_type = r['entity'].replace('B-', '').replace('I-', '')
            entities.append((entity_text, entity_type))
        return results, entities
    except Exception as e:
        return [], []

# Categorize errors
def categorize_error(gold_entities, pred_entities, tokens):
    text = ' '.join(tokens)
    errors = []
    
    gold_persons = [e for e in gold_entities if e[1] == 'PERSON']
    pred_persons = [e for e in pred_entities if 'PER' in e[1].upper() or e[1] == 'PERSON']
    
    for gold_name, gold_type in gold_persons:
        # Check if this entity was found
        found = False
        partial = False
        
        for pred_name, pred_type in pred_persons:
            if gold_name == pred_name:
                found = True
                break
            elif gold_name in pred_name or pred_name in gold_name:
                partial = True
        
        if not found:
            # Determine error type
            if 'בן' in gold_name or 'בר' in gold_name:
                errors.append(('PATRONYMIC_SPLIT', gold_name, pred_persons))
            elif any(title in gold_name for title in ['רבי', 'רב', 'מהר', 'הרב', 'כמהר']):
                errors.append(('BOUNDARY_ERROR', gold_name, pred_persons))
            elif partial:
                errors.append(('PARTIAL_MATCH', gold_name, pred_persons))
            else:
                errors.append(('MISSED', gold_name, pred_persons))
    
    return errors

print("=" * 80)
print("LOADING MODELS...")
print("=" * 80)

# Load DictaBERT NER (general Hebrew NER trained on NEMO)
print("\n1. Loading DictaBERT NER (general Hebrew)...")
try:
    dictabert_ner = pipeline(
        "ner", 
        model="dicta-il/dictabert-ner",
        aggregation_strategy="simple"
    )
    print("   ✓ DictaBERT NER loaded")
except Exception as e:
    print(f"   ✗ Error loading DictaBERT NER: {e}")
    # Try alternative
    try:
        dictabert_ner = pipeline(
            "ner",
            model="avichr/heBERT_NER",
            aggregation_strategy="simple"
        )
        print("   ✓ Using heBERT_NER as fallback")
    except:
        dictabert_ner = None
        print("   ✗ No general Hebrew NER available")

# Load test data
print("\n2. Loading test data...")
test_data = load_test_data('processed-data/marc_ner_test.jsonl', max_samples=500)
print(f"   ✓ Loaded {len(test_data)} test samples")

# Run comparison
print("\n" + "=" * 80)
print("RUNNING COMPARISON...")
print("=" * 80)

results = {
    'patronymic_errors': [],
    'boundary_errors': [],
    'partial_matches': [],
    'missed_entities': [],
    'dictabert_correct': [],
}

for i, sample in enumerate(test_data):
    tokens = sample['tokens']
    gold_tags = sample['ner_tags']
    text = ' '.join(tokens)
    
    # Get gold entities
    gold_entities = extract_entities_from_bio(tokens, gold_tags)
    gold_persons = [e for e in gold_entities if e[1] == 'PERSON']
    
    if not gold_persons:
        continue
    
    # Run DictaBERT NER
    if dictabert_ner:
        try:
            dictabert_results = dictabert_ner(text)
            dictabert_entities = [(r['word'], r['entity_group']) for r in dictabert_results]
        except:
            dictabert_entities = []
    else:
        dictabert_entities = []
    
    # Categorize errors
    errors = categorize_error(gold_entities, dictabert_entities, tokens)
    
    for error_type, gold_name, predictions in errors:
        entry = {
            'text': text,
            'gold_entity': gold_name,
            'dictabert_predictions': dictabert_entities,
            'tokens': tokens
        }
        
        if error_type == 'PATRONYMIC_SPLIT':
            results['patronymic_errors'].append(entry)
        elif error_type == 'BOUNDARY_ERROR':
            results['boundary_errors'].append(entry)
        elif error_type == 'PARTIAL_MATCH':
            results['partial_matches'].append(entry)
        else:
            results['missed_entities'].append(entry)
    
    if i % 100 == 0:
        print(f"   Processed {i}/{len(test_data)} samples...")

# Print results
print("\n" + "=" * 80)
print("RESULTS: Where DictaBERT Fails")
print("=" * 80)

print(f"\n📊 ERROR SUMMARY:")
print(f"   • Patronymic Splitting Errors: {len(results['patronymic_errors'])}")
print(f"   • Boundary Errors (titles/honorifics): {len(results['boundary_errors'])}")
print(f"   • Partial Matches: {len(results['partial_matches'])}")
print(f"   • Completely Missed: {len(results['missed_entities'])}")

print("\n" + "-" * 80)
print("1. PATRONYMIC SPLITTING ERRORS (בן pattern)")
print("-" * 80)
for i, entry in enumerate(results['patronymic_errors'][:5]):
    print(f"\nExample {i+1}:")
    print(f"   Text: {entry['text'][:100]}...")
    print(f"   Gold Entity: {entry['gold_entity']}")
    print(f"   DictaBERT found: {entry['dictabert_predictions']}")

print("\n" + "-" * 80)
print("2. BOUNDARY ERRORS (titles/honorifics)")
print("-" * 80)
for i, entry in enumerate(results['boundary_errors'][:5]):
    print(f"\nExample {i+1}:")
    print(f"   Text: {entry['text'][:100]}...")
    print(f"   Gold Entity: {entry['gold_entity']}")
    print(f"   DictaBERT found: {entry['dictabert_predictions']}")

print("\n" + "-" * 80)
print("3. PARTIAL MATCHES")
print("-" * 80)
for i, entry in enumerate(results['partial_matches'][:5]):
    print(f"\nExample {i+1}:")
    print(f"   Text: {entry['text'][:100]}...")
    print(f"   Gold Entity: {entry['gold_entity']}")
    print(f"   DictaBERT found: {entry['dictabert_predictions']}")

print("\n" + "-" * 80)
print("4. COMPLETELY MISSED")
print("-" * 80)
for i, entry in enumerate(results['missed_entities'][:5]):
    print(f"\nExample {i+1}:")
    print(f"   Text: {entry['text'][:100]}...")
    print(f"   Gold Entity: {entry['gold_entity']}")
    print(f"   DictaBERT found: {entry['dictabert_predictions']}")

# Save detailed results
with open('dictabert_error_analysis.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n✓ Detailed results saved to dictabert_error_analysis.json")

