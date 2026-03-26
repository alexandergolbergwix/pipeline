#!/usr/bin/env python3
"""
Evaluate Baseline Models on 5-Fold Cross-Validation Test Sets - V2
Uses the same methodology as strict_f1_verification.py for accurate comparison.
"""

import json
import os
from transformers import pipeline
import numpy as np

KFOLD_DIR = "processed-data/kfold"
NUM_FOLDS = 5

def extract_entities_with_spans(tokens, tags):
    """Extract entities with their token spans."""
    # Convert numeric tags to labels if needed
    label_map = {0: 'O', 1: 'B-PERSON', 2: 'I-PERSON'}
    if isinstance(tags[0], int):
        tags = [label_map.get(t, 'O') for t in tags]
    
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

def load_fold_data(fold_idx):
    """Load validation data for a specific fold."""
    val_file = os.path.join(KFOLD_DIR, f"fold_{fold_idx}_val.jsonl")
    data = []
    with open(val_file, 'r', encoding='utf-8') as f:
        for line in f:
            data.append(json.loads(line))
    return data

def evaluate_dictabert_ner_strict(fold_data, ner_pipeline):
    """
    Evaluate DictaBERT-NER using strict entity matching.
    Same methodology as strict_f1_verification.py
    """
    exact_match = 0
    total_gold = 0
    total_pred = 0
    
    for sample in fold_data:
        tokens = sample['tokens']
        tags = sample['ner_tags']
        text = ' '.join(tokens)
        
        # Gold entities (PERSON only)
        gold_entities = []
        label_map = {0: 'O', 1: 'B-PERSON', 2: 'I-PERSON'}
        tags_str = [label_map.get(t, 'O') for t in tags]
        
        for ent, start, end in extract_entities_with_spans(tokens, tags_str):
            if 'PERSON' in tags_str[start]:
                gold_entities.append(ent.lower().strip())
        
        total_gold += len(gold_entities)
        
        # Predicted entities
        try:
            results = ner_pipeline(text)
            pred_entities = []
            for r in results:
                if r['entity_group'] == 'PER':
                    pred_entities.append(r['word'].lower().strip())
            total_pred += len(pred_entities)
            
            # Count exact matches
            for gold in gold_entities:
                if gold in pred_entities:
                    exact_match += 1
        except:
            pass
    
    # Calculate F1
    precision = exact_match / total_pred if total_pred > 0 else 0
    recall = exact_match / total_gold if total_gold > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    return f1, precision, recall

def main():
    print("=" * 70)
    print("BASELINE EVALUATION ON 5-FOLD CV (STRICT MATCHING)")
    print("=" * 70)
    print()
    print("Using same methodology as strict_f1_verification.py")
    print()
    
    # Load DictaBERT-NER once
    print("Loading DictaBERT-NER model...")
    ner = pipeline("ner", model="dicta-il/dictabert-ner", aggregation_strategy="simple")
    print("Model loaded.\n")
    
    ner_f1s = []
    
    for fold_idx in range(NUM_FOLDS):
        print(f"\n{'='*50}")
        print(f"FOLD {fold_idx + 1}/{NUM_FOLDS}")
        print(f"{'='*50}")
        
        # Load fold data
        fold_data = load_fold_data(fold_idx)
        print(f"Loaded {len(fold_data)} validation samples")
        
        # Evaluate DictaBERT-NER
        print("Evaluating DictaBERT-NER (strict matching)...")
        f1, precision, recall = evaluate_dictabert_ner_strict(fold_data, ner)
        ner_f1s.append(f1)
        print(f"  Precision: {precision*100:.2f}%")
        print(f"  Recall: {recall*100:.2f}%")
        print(f"  F1: {f1*100:.2f}%")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY: 5-FOLD CROSS-VALIDATION RESULTS")
    print("=" * 70)
    
    print("\nDictaBERT-NER (pre-trained on NEMO):")
    print(f"  Per-fold F1: {[f'{f*100:.2f}%' for f in ner_f1s]}")
    print(f"  Mean F1: {np.mean(ner_f1s)*100:.2f}% ± {np.std(ner_f1s)*100:.2f}%")
    
    print("\n" + "=" * 70)
    print("COMPARISON TABLE (for paper)")
    print("=" * 70)
    print(f"| Model                          | NER F1                |")
    print(f"|--------------------------------|-----------------------|")
    print(f"| Zero-shot DictaBERT            | ~0% (no NER head)     |")
    print(f"| DictaBERT-NER (NEMO)           | {np.mean(ner_f1s)*100:.2f}% ± {np.std(ner_f1s)*100:.2f}% |")
    print(f"| Fine-tuned Joint Model (ours)  | 85.70% ± 0.51%        |")
    print("=" * 70)
    
    # Save results
    results = {
        "ner_f1s": ner_f1s,
        "ner_mean": float(np.mean(ner_f1s)),
        "ner_std": float(np.std(ner_f1s)),
        "methodology": "strict_entity_matching",
        "note": "Same methodology as strict_f1_verification.py"
    }
    
    with open("baseline_5fold_results_v2.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print("\nResults saved to baseline_5fold_results_v2.json")

if __name__ == "__main__":
    main()

