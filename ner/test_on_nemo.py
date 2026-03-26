#!/usr/bin/env python3
"""
Test DictaBERT NER and our fine-tuned model on the NEMO benchmark.
"""

import json
from transformers import pipeline, AutoTokenizer, AutoModelForTokenClassification
import torch

def load_nemo_bmes(filepath):
    """Load NEMO BMES format file."""
    sentences = []
    current_tokens = []
    current_tags = []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line == '':
                if current_tokens:
                    sentences.append((current_tokens, current_tags))
                    current_tokens = []
                    current_tags = []
            else:
                parts = line.split()
                if len(parts) >= 2:
                    token = parts[0]
                    tag = parts[-1]
                    current_tokens.append(token)
                    current_tags.append(tag)
    
    if current_tokens:
        sentences.append((current_tokens, current_tags))
    
    return sentences

def bmes_to_bio(tags):
    """Convert BMES tags to BIO format."""
    bio_tags = []
    for tag in tags:
        if tag == 'O':
            bio_tags.append('O')
        elif tag.startswith('S-'):
            bio_tags.append('B-' + tag[2:])
        elif tag.startswith('B-'):
            bio_tags.append('B-' + tag[2:])
        elif tag.startswith('M-'):
            bio_tags.append('I-' + tag[2:])
        elif tag.startswith('E-'):
            bio_tags.append('I-' + tag[2:])
        else:
            bio_tags.append(tag)
    return bio_tags

def extract_entities(tokens, tags):
    """Extract entities from BIO/BMES tags."""
    entities = []
    current_entity = []
    current_type = None
    
    # Convert BMES to BIO if needed
    if any(t.startswith('S-') or t.startswith('M-') or t.startswith('E-') for t in tags):
        tags = bmes_to_bio(tags)
    
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

def calculate_f1(gold_entities_list, pred_entities_list, entity_type=None):
    """Calculate entity-level F1 with strict matching."""
    tp = 0
    fp = 0
    fn = 0
    
    for gold_ents, pred_ents in zip(gold_entities_list, pred_entities_list):
        # Filter by entity type if specified
        if entity_type:
            gold_set = set((e[0], e[1]) for e in gold_ents if e[1] == entity_type)
            pred_set = set((e[0], e[1]) for e in pred_ents if e[1] == entity_type)
        else:
            gold_set = set((e[0], e[1]) for e in gold_ents)
            pred_set = set((e[0], e[1]) for e in pred_ents)
        
        tp += len(gold_set & pred_set)
        fp += len(pred_set - gold_set)
        fn += len(gold_set - pred_set)
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    return precision, recall, f1

print("=" * 80)
print("NEMO BENCHMARK EVALUATION")
print("=" * 80)

# Load NEMO test set
print("\n1. Loading NEMO test set...")
nemo_test = load_nemo_bmes('NEMO-Corpus/data/spmrl/gold/token-single_gold_test.bmes')
print(f"   Loaded {len(nemo_test)} sentences")

# Count entities
total_entities = 0
entity_types = {}
for tokens, tags in nemo_test:
    entities = extract_entities(tokens, tags)
    total_entities += len(entities)
    for _, etype in entities:
        entity_types[etype] = entity_types.get(etype, 0) + 1

print(f"   Total entities: {total_entities}")
print(f"   Entity types: {entity_types}")

# Load DictaBERT NER
print("\n2. Loading DictaBERT NER...")
ner = pipeline("ner", model="dicta-il/dictabert-ner", aggregation_strategy="simple")
print("   ✓ Model loaded")

# Evaluate DictaBERT
print("\n3. Evaluating DictaBERT on NEMO...")
gold_entities_list = []
pred_entities_list = []

for i, (tokens, tags) in enumerate(nemo_test):
    text = ' '.join(tokens)
    gold_ents = extract_entities(tokens, tags)
    gold_entities_list.append(gold_ents)
    
    try:
        results = ner(text)
        pred_ents = [(r['word'], r['entity_group']) for r in results]
        pred_entities_list.append(pred_ents)
    except:
        pred_entities_list.append([])
    
    if i % 100 == 0:
        print(f"   Processed {i}/{len(nemo_test)} sentences...")

# Calculate overall F1
precision, recall, f1 = calculate_f1(gold_entities_list, pred_entities_list)

print("\n" + "=" * 80)
print("RESULTS: DictaBERT on NEMO")
print("=" * 80)
print(f"\nOverall (all entity types):")
print(f"   Precision: {precision*100:.2f}%")
print(f"   Recall: {recall*100:.2f}%")
print(f"   F1: {f1*100:.2f}%")

# Per-entity-type F1
print(f"\nPer-entity-type F1:")
for etype in entity_types.keys():
    p, r, f = calculate_f1(gold_entities_list, pred_entities_list, entity_type=etype)
    print(f"   {etype}: P={p*100:.1f}% R={r*100:.1f}% F1={f*100:.2f}%")

print(f"\n📋 PAPER CLAIM: DictaBERT achieves 87% F1 on modern Hebrew benchmarks")
print(f"📊 MEASURED: {f1*100:.2f}% F1 on NEMO test set")

