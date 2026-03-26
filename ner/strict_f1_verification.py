#!/usr/bin/env python3
"""
Strict F1 verification - exact entity matching only.
"""

import json
from transformers import pipeline

def load_test_data(path, max_samples=None):
    samples = []
    with open(path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if max_samples and i >= max_samples:
                break
            samples.append(json.loads(line))
    return samples

def extract_entities_with_spans(tokens, tags):
    """Extract entities with their token spans."""
    entities = []
    current_tokens = []
    start_idx = None
    
    for i, (token, tag) in enumerate(zip(tokens, tags)):
        if tag.startswith('B-'):
            if current_tokens:
                entities.append((' '.join(current_tokens), start_idx, i-1))
            current_tokens = [token]
            start_idx = i
        elif tag.startswith('I-') and current_tokens:
            current_tokens.append(token)
        else:
            if current_tokens:
                entities.append((' '.join(current_tokens), start_idx, i-1))
            current_tokens = []
            start_idx = None
    
    if current_tokens:
        entities.append((' '.join(current_tokens), start_idx, len(tokens)-1))
    
    return entities

print("=" * 80)
print("STRICT F1 VERIFICATION")
print("=" * 80)

# Load test data
test_data = load_test_data('processed-data/marc_ner_test.jsonl', max_samples=500)
print(f"Loaded {len(test_data)} samples")

# Initialize DictaBERT NER
print("\nLoading DictaBERT NER...")
ner = pipeline("ner", model="dicta-il/dictabert-ner", aggregation_strategy="none")

# Metrics
exact_match = 0
partial_match = 0
total_gold = 0
total_pred = 0
false_positives = 0
false_negatives = 0

print("\nEvaluating...")

for i, sample in enumerate(test_data):
    tokens = sample['tokens']
    tags = sample['ner_tags']
    text = ' '.join(tokens)
    
    # Gold entities (PERSON only)
    gold_entities = []
    for ent, start, end in extract_entities_with_spans(tokens, tags):
        if 'PERSON' in tags[start]:
            gold_entities.append(ent)
    
    total_gold += len(gold_entities)
    
    # Predicted entities
    try:
        results = ner(text)
        # Aggregate tokens into entities
        pred_entities = []
        current_entity = []
        for r in results:
            if r['entity'].startswith('B-PER'):
                if current_entity:
                    pred_entities.append(' '.join(current_entity))
                current_entity = [r['word'].replace('##', '')]
            elif r['entity'].startswith('I-PER') and current_entity:
                current_entity.append(r['word'].replace('##', ''))
            else:
                if current_entity:
                    pred_entities.append(' '.join(current_entity))
                current_entity = []
        if current_entity:
            pred_entities.append(' '.join(current_entity))
        
    except:
        pred_entities = []
    
    total_pred += len(pred_entities)
    
    # Count matches - STRICT: exact string match only
    for gold in gold_entities:
        gold_clean = gold.replace('"', '').replace("'", '').strip()
        found_exact = False
        found_partial = False
        
        for pred in pred_entities:
            pred_clean = pred.replace('"', '').replace("'", '').strip()
            if gold_clean == pred_clean:
                found_exact = True
                break
            elif gold_clean in pred_clean or pred_clean in gold_clean:
                found_partial = True
        
        if found_exact:
            exact_match += 1
        elif found_partial:
            partial_match += 1
        else:
            false_negatives += 1
    
    if i % 100 == 0:
        print(f"  Processed {i}/{len(test_data)}")

# Calculate metrics
print("\n" + "=" * 80)
print("RESULTS")
print("=" * 80)

print(f"\nTotal gold entities: {total_gold}")
print(f"Total predicted entities: {total_pred}")
print(f"\nExact matches: {exact_match}")
print(f"Partial matches: {partial_match}")
print(f"Missed (false negatives): {false_negatives}")

# Strict F1 (exact match only)
precision_strict = exact_match / total_pred if total_pred > 0 else 0
recall_strict = exact_match / total_gold if total_gold > 0 else 0
f1_strict = 2 * precision_strict * recall_strict / (precision_strict + recall_strict) if (precision_strict + recall_strict) > 0 else 0

# Lenient F1 (partial match counts)
exact_plus_partial = exact_match + partial_match
precision_lenient = exact_plus_partial / total_pred if total_pred > 0 else 0
recall_lenient = exact_plus_partial / total_gold if total_gold > 0 else 0
f1_lenient = 2 * precision_lenient * recall_lenient / (precision_lenient + recall_lenient) if (precision_lenient + recall_lenient) > 0 else 0

print(f"\n📊 STRICT EVALUATION (exact match only):")
print(f"   Precision: {precision_strict*100:.2f}%")
print(f"   Recall: {recall_strict*100:.2f}%")
print(f"   F1: {f1_strict*100:.2f}%")

print(f"\n📊 LENIENT EVALUATION (partial match counts):")
print(f"   Precision: {precision_lenient*100:.2f}%")
print(f"   Recall: {recall_lenient*100:.2f}%")
print(f"   F1: {f1_lenient*100:.2f}%")

print(f"\n📋 PAPER CLAIMS (5-fold CV):")
print(f"   DictaBERT (general NER): 30.15% ± 0.38% F1")
print(f"   Fine-tuned model: 85.70% ± 0.51% F1")
print(f"   Improvement: +55.55% F1")

print(f"\n⚠️  VERIFICATION:")
if 28 < f1_strict*100 < 35:
    print(f"   Strict F1 ({f1_strict*100:.2f}%) close to paper claim (30.15%)")
else:
    print(f"   Strict F1 ({f1_strict*100:.2f}%) - note: this is on marc_ner_test.jsonl, paper uses 5-fold CV (30.15%)")

