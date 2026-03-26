#!/usr/bin/env python3
"""
Verify F1 scores claimed in the paper.
"""

import json
from transformers import pipeline
from collections import Counter

def load_test_data(path, max_samples=None):
    samples = []
    with open(path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if max_samples and i >= max_samples:
                break
            data = json.loads(line)
            samples.append(data)
    return samples

def extract_entities_from_bio(tokens, tags):
    """Extract entities from BIO tags."""
    entities = []
    current_entity = []
    
    for i, (token, tag) in enumerate(zip(tokens, tags)):
        if tag.startswith('B-'):
            if current_entity:
                entities.append(' '.join(current_entity))
            current_entity = [token]
        elif tag.startswith('I-') and current_entity:
            current_entity.append(token)
        else:
            if current_entity:
                entities.append(' '.join(current_entity))
            current_entity = []
    
    if current_entity:
        entities.append(' '.join(current_entity))
    
    return set(entities)

def calculate_f1(gold_entities_list, pred_entities_list):
    """Calculate entity-level precision, recall, F1."""
    total_gold = 0
    total_pred = 0
    total_correct = 0
    
    for gold, pred in zip(gold_entities_list, pred_entities_list):
        total_gold += len(gold)
        total_pred += len(pred)
        
        # Count exact matches
        for g in gold:
            for p in pred:
                if g == p or g in p or p in g:  # Allow partial match
                    total_correct += 1
                    break
    
    precision = total_correct / total_pred if total_pred > 0 else 0
    recall = total_correct / total_gold if total_gold > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    return precision, recall, f1

print("=" * 80)
print("VERIFYING F1 SCORES FROM PAPER")
print("=" * 80)

# Load test data
print("\n1. Loading test data...")
test_data = load_test_data('processed-data/marc_ner_test.jsonl', max_samples=500)
print(f"   Loaded {len(test_data)} samples")

# Extract gold entities
gold_entities_list = []
texts = []
for sample in test_data:
    tokens = sample['tokens']
    tags = sample['ner_tags']
    gold_entities = extract_entities_from_bio(tokens, tags)
    gold_entities_list.append(gold_entities)
    texts.append(' '.join(tokens))

# Count samples with entities
samples_with_entities = sum(1 for g in gold_entities_list if len(g) > 0)
total_gold_entities = sum(len(g) for g in gold_entities_list)
print(f"   Samples with entities: {samples_with_entities}/{len(test_data)}")
print(f"   Total gold entities: {total_gold_entities}")

# Run DictaBERT NER
print("\n2. Running DictaBERT NER...")
try:
    ner = pipeline("ner", model="dicta-il/dictabert-ner", aggregation_strategy="simple")
    
    pred_entities_list = []
    for i, text in enumerate(texts):
        try:
            results = ner(text)
            # Filter for PER entities only
            pred_entities = set()
            for r in results:
                if r['entity_group'] == 'PER':
                    pred_entities.add(r['word'])
            pred_entities_list.append(pred_entities)
        except:
            pred_entities_list.append(set())
        
        if i % 100 == 0:
            print(f"   Processed {i}/{len(texts)} samples...")
    
    total_pred_entities = sum(len(p) for p in pred_entities_list)
    print(f"   Total predicted entities (PER only): {total_pred_entities}")
    
    # Calculate F1
    precision, recall, f1 = calculate_f1(gold_entities_list, pred_entities_list)
    
    print(f"\n3. DictaBERT NER Results:")
    print(f"   Precision: {precision*100:.2f}%")
    print(f"   Recall: {recall*100:.2f}%")
    print(f"   F1: {f1*100:.2f}%")
    
    print(f"\n   Paper claims: 17.93% F1")
    print(f"   Actual result: {f1*100:.2f}% F1")
    
except Exception as e:
    print(f"   Error: {e}")

# Dataset statistics
print("\n" + "=" * 80)
print("VERIFYING DATASET STATISTICS")
print("=" * 80)

# Check multi-entity percentage
multi_entity_samples = 0
for sample in test_data:
    tags = sample['ner_tags']
    # Count B- tags
    b_count = sum(1 for t in tags if t.startswith('B-'))
    if b_count > 1:
        multi_entity_samples += 1

print(f"\nMulti-entity samples: {multi_entity_samples}/{len(test_data)} = {multi_entity_samples/len(test_data)*100:.1f}%")
print(f"Paper claims: 10.7% multi-person samples")

# Full dataset stats
print("\n" + "=" * 80)
print("FULL TRAINING DATA STATISTICS")
print("=" * 80)

train_data = load_test_data('processed-data/marc_ner_train.jsonl')
print(f"\nTraining samples: {len(train_data)}")
print(f"Paper claims: 7,580 training samples")

multi_train = 0
for sample in train_data:
    tags = sample['ner_tags']
    b_count = sum(1 for t in tags if t.startswith('B-'))
    if b_count > 1:
        multi_train += 1

print(f"Multi-entity in training: {multi_train}/{len(train_data)} = {multi_train/len(train_data)*100:.1f}%")

