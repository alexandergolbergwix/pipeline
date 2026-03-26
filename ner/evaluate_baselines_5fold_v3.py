#!/usr/bin/env python3
"""
Evaluate Baseline Models on 5-Fold Cross-Validation Test Sets - V3
Uses proper entity extraction and strict matching.
"""

import json
import os
from transformers import pipeline
import numpy as np
from tqdm import tqdm

KFOLD_DIR = "processed-data/kfold"
NUM_FOLDS = 5

def extract_gold_entities(tokens, tags):
    """Extract gold entities from token/tag sequence."""
    entities = []
    current_entity = []
    for i, (tok, tag) in enumerate(zip(tokens, tags)):
        if tag.startswith('B-'):
            if current_entity:
                entities.append(' '.join(current_entity).lower().strip())
            current_entity = [tok]
        elif tag.startswith('I-') and current_entity:
            current_entity.append(tok)
        else:
            if current_entity:
                entities.append(' '.join(current_entity).lower().strip())
                current_entity = []
    if current_entity:
        entities.append(' '.join(current_entity).lower().strip())
    return entities

def load_fold_data(fold_idx):
    """Load validation data for a specific fold."""
    val_file = os.path.join(KFOLD_DIR, f"fold_{fold_idx}_val.jsonl")
    data = []
    with open(val_file, 'r', encoding='utf-8') as f:
        for line in f:
            data.append(json.loads(line))
    return data

def evaluate_dictabert_ner(fold_data, ner_pipeline, matching='strict'):
    """
    Evaluate DictaBERT-NER.
    matching: 'strict' for exact match, 'lenient' for partial overlap
    """
    exact_match = 0
    partial_match = 0
    total_gold = 0
    total_pred = 0
    
    for sample in tqdm(fold_data, desc="  Evaluating"):
        tokens = sample['tokens']
        tags = sample['ner_tags']
        text = ' '.join(tokens)
        
        gold_entities = extract_gold_entities(tokens, tags)
        total_gold += len(gold_entities)
        
        try:
            results = ner_pipeline(text)
            pred_entities = [r['word'].lower().strip() for r in results if r['entity_group'] == 'PER']
            total_pred += len(pred_entities)
            
            for gold in gold_entities:
                # Strict matching
                if gold in pred_entities:
                    exact_match += 1
                # Partial matching
                else:
                    for pred in pred_entities:
                        if gold in pred or pred in gold:
                            partial_match += 1
                            break
        except:
            pass
    
    # Calculate metrics
    if matching == 'strict':
        matches = exact_match
    else:
        matches = exact_match + partial_match
    
    precision = matches / total_pred if total_pred > 0 else 0
    recall = matches / total_gold if total_gold > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    return f1, precision, recall, {'total_gold': total_gold, 'total_pred': total_pred, 
                                    'exact': exact_match, 'partial': partial_match}

def main():
    print("=" * 70)
    print("BASELINE EVALUATION ON 5-FOLD CV")
    print("=" * 70)
    
    print("\nLoading DictaBERT-NER model...")
    ner = pipeline("ner", model="dicta-il/dictabert-ner", aggregation_strategy="simple")
    print("Model loaded.\n")
    
    strict_f1s = []
    lenient_f1s = []
    
    for fold_idx in range(NUM_FOLDS):
        print(f"\n{'='*50}")
        print(f"FOLD {fold_idx + 1}/{NUM_FOLDS}")
        print(f"{'='*50}")
        
        fold_data = load_fold_data(fold_idx)
        print(f"Loaded {len(fold_data)} validation samples")
        
        # Strict evaluation
        f1_strict, prec_s, rec_s, stats = evaluate_dictabert_ner(fold_data, ner, 'strict')
        strict_f1s.append(f1_strict)
        
        # Lenient (use same stats)
        matches_lenient = stats['exact'] + stats['partial']
        prec_l = matches_lenient / stats['total_pred'] if stats['total_pred'] > 0 else 0
        rec_l = matches_lenient / stats['total_gold'] if stats['total_gold'] > 0 else 0
        f1_lenient = 2 * prec_l * rec_l / (prec_l + rec_l) if (prec_l + rec_l) > 0 else 0
        lenient_f1s.append(f1_lenient)
        
        print(f"  Total gold: {stats['total_gold']}, Total pred: {stats['total_pred']}")
        print(f"  Exact matches: {stats['exact']}, Partial: {stats['partial']}")
        print(f"  STRICT F1: {f1_strict*100:.2f}%")
        print(f"  LENIENT F1: {f1_lenient*100:.2f}%")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY: 5-FOLD CROSS-VALIDATION RESULTS")
    print("=" * 70)
    
    print("\nDictaBERT-NER (pre-trained on NEMO) - STRICT matching:")
    print(f"  Per-fold F1: {[f'{f*100:.2f}%' for f in strict_f1s]}")
    print(f"  Mean F1: {np.mean(strict_f1s)*100:.2f}% ± {np.std(strict_f1s)*100:.2f}%")
    
    print("\nDictaBERT-NER (pre-trained on NEMO) - LENIENT matching:")
    print(f"  Per-fold F1: {[f'{f*100:.2f}%' for f in lenient_f1s]}")
    print(f"  Mean F1: {np.mean(lenient_f1s)*100:.2f}% ± {np.std(lenient_f1s)*100:.2f}%")
    
    print("\n" + "=" * 70)
    print("COMPARISON TABLE (for paper)")
    print("=" * 70)
    print(f"| Model                          | Strict F1             | Lenient F1            |")
    print(f"|--------------------------------|-----------------------|-----------------------|")
    print(f"| DictaBERT-NER (NEMO)           | {np.mean(strict_f1s)*100:.2f}% ± {np.std(strict_f1s)*100:.2f}% | {np.mean(lenient_f1s)*100:.2f}% ± {np.std(lenient_f1s)*100:.2f}% |")
    print(f"| Fine-tuned Joint Model (ours)  | 85.70% ± 0.51%        | -                     |")
    print("=" * 70)
    
    # Save results
    results = {
        "strict_f1s": strict_f1s,
        "lenient_f1s": lenient_f1s,
        "strict_mean": float(np.mean(strict_f1s)),
        "strict_std": float(np.std(strict_f1s)),
        "lenient_mean": float(np.mean(lenient_f1s)),
        "lenient_std": float(np.std(lenient_f1s)),
    }
    
    with open("baseline_5fold_results_v3.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print("\nResults saved to baseline_5fold_results_v3.json")

if __name__ == "__main__":
    main()

