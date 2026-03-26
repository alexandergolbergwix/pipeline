#!/usr/bin/env python3
"""
Careful evaluation on NEMO - matching the published evaluation methodology.
"""

import json
from transformers import AutoTokenizer, AutoModelForTokenClassification
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
                    current_tokens.append(parts[0])
                    current_tags.append(parts[-1])
    
    if current_tokens:
        sentences.append((current_tokens, current_tags))
    
    return sentences

def extract_entities_bmes(tokens, tags):
    """Extract entities from BMES tags - return (text, type, start, end)."""
    entities = []
    i = 0
    while i < len(tags):
        tag = tags[i]
        if tag.startswith('S-'):
            entity_type = tag[2:]
            entities.append((tokens[i], entity_type, i, i))
        elif tag.startswith('B-'):
            entity_type = tag[2:]
            start = i
            entity_tokens = [tokens[i]]
            i += 1
            while i < len(tags) and (tags[i].startswith('M-') or tags[i].startswith('E-')):
                entity_tokens.append(tokens[i])
                if tags[i].startswith('E-'):
                    break
                i += 1
            entities.append((' '.join(entity_tokens), entity_type, start, i))
        i += 1
    return entities

print("=" * 80)
print("CAREFUL NEMO EVALUATION")
print("=" * 80)

# Load NEMO test set
nemo_test = load_nemo_bmes('NEMO-Corpus/data/spmrl/gold/token-single_gold_test.bmes')
print(f"Loaded {len(nemo_test)} sentences")

# Load model
print("\nLoading DictaBERT NER...")
tokenizer = AutoTokenizer.from_pretrained("dicta-il/dictabert-ner")
model = AutoModelForTokenClassification.from_pretrained("dicta-il/dictabert-ner")
model.eval()

# Get label mapping
id2label = model.config.id2label
label2id = model.config.label2id
print(f"Labels: {list(id2label.values())[:10]}...")

# Token-level evaluation (as commonly done)
total_correct = 0
total_predicted = 0
total_gold = 0

# Entity-level evaluation
entity_tp = 0
entity_fp = 0
entity_fn = 0

for i, (tokens, gold_tags) in enumerate(nemo_test):
    text = ' '.join(tokens)
    
    # Tokenize
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    
    with torch.no_grad():
        outputs = model(**inputs)
        predictions = torch.argmax(outputs.logits, dim=2)
    
    pred_labels = [id2label[p.item()] for p in predictions[0]]
    
    # Get word-level predictions (handle subwords)
    word_ids = inputs.word_ids()
    word_predictions = []
    prev_word_id = None
    for idx, word_id in enumerate(word_ids):
        if word_id is None:
            continue
        if word_id != prev_word_id:
            word_predictions.append(pred_labels[idx])
        prev_word_id = word_id
    
    # Align predictions with gold (handle length mismatch)
    min_len = min(len(tokens), len(word_predictions))
    
    # Token-level metrics
    for j in range(min_len):
        gold_tag = gold_tags[j]
        pred_tag = word_predictions[j]
        
        # Normalize tags for comparison
        if gold_tag != 'O':
            total_gold += 1
        if pred_tag != 'O':
            total_predicted += 1
        if gold_tag != 'O' and pred_tag != 'O':
            # Check if same entity type (ignore B/I/S/M/E prefix)
            gold_type = gold_tag.split('-')[-1] if '-' in gold_tag else gold_tag
            pred_type = pred_tag.split('-')[-1] if '-' in pred_tag else pred_tag
            if gold_type == pred_type:
                total_correct += 1
    
    # Entity-level metrics
    gold_entities = set()
    pred_entities = set()
    
    for ent in extract_entities_bmes(tokens[:min_len], gold_tags[:min_len]):
        gold_entities.add((ent[0], ent[1]))
    
    for ent in extract_entities_bmes(tokens[:min_len], word_predictions[:min_len]):
        pred_entities.add((ent[0], ent[1]))
    
    entity_tp += len(gold_entities & pred_entities)
    entity_fp += len(pred_entities - gold_entities)
    entity_fn += len(gold_entities - pred_entities)
    
    if i % 100 == 0:
        print(f"Processed {i}/{len(nemo_test)}")

# Calculate metrics
print("\n" + "=" * 80)
print("RESULTS")
print("=" * 80)

# Token-level
token_precision = total_correct / total_predicted if total_predicted > 0 else 0
token_recall = total_correct / total_gold if total_gold > 0 else 0
token_f1 = 2 * token_precision * token_recall / (token_precision + token_recall) if (token_precision + token_recall) > 0 else 0

print(f"\nTOKEN-LEVEL (how papers often report):")
print(f"   Precision: {token_precision*100:.2f}%")
print(f"   Recall: {token_recall*100:.2f}%")
print(f"   F1: {token_f1*100:.2f}%")

# Entity-level
entity_precision = entity_tp / (entity_tp + entity_fp) if (entity_tp + entity_fp) > 0 else 0
entity_recall = entity_tp / (entity_tp + entity_fn) if (entity_tp + entity_fn) > 0 else 0
entity_f1 = 2 * entity_precision * entity_recall / (entity_precision + entity_recall) if (entity_precision + entity_recall) > 0 else 0

print(f"\nENTITY-LEVEL (strict matching):")
print(f"   Precision: {entity_precision*100:.2f}%")
print(f"   Recall: {entity_recall*100:.2f}%")
print(f"   F1: {entity_f1*100:.2f}%")

print(f"\n📋 PAPER CLAIM: DictaBERT achieves 87% F1 on modern Hebrew benchmarks")
print(f"📊 TOKEN-LEVEL F1: {token_f1*100:.2f}%")
print(f"📊 ENTITY-LEVEL F1: {entity_f1*100:.2f}%")

