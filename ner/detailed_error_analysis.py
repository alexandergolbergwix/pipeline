#!/usr/bin/env python3
"""
Detailed error analysis comparing DictaBERT vs gold labels.
"""

import json
from collections import Counter

# Load the error analysis results
with open('dictabert_error_analysis.json', 'r', encoding='utf-8') as f:
    results = json.load(f)

print("=" * 80)
print("DETAILED ERROR ANALYSIS")
print("=" * 80)

# Analyze patronymic errors more closely
print("\n📊 PATRONYMIC ERROR ANALYSIS (בן pattern):")
print("-" * 40)

patronymic_actually_found = 0
patronymic_split = 0
patronymic_missed = 0

for entry in results['patronymic_errors']:
    gold = entry['gold_entity']
    preds = entry['dictabert_predictions']
    pred_texts = [p[0] for p in preds if p[1] == 'PER']
    
    # Check if the patronymic was actually found
    gold_clean = gold.replace('"', '').strip()
    found = False
    for pred_text in pred_texts:
        if gold_clean in pred_text or pred_text in gold_clean:
            found = True
            break
    
    if found:
        patronymic_actually_found += 1
    elif any('בן' in pred for pred in pred_texts):
        patronymic_split += 1
    else:
        patronymic_missed += 1

print(f"   • Patronymic correctly found: {patronymic_actually_found}")
print(f"   • Patronymic split into parts: {patronymic_split}")
print(f"   • Patronymic completely missed: {patronymic_missed}")

# Analyze classification errors (PER vs WOA)
print("\n📊 ENTITY TYPE CONFUSION:")
print("-" * 40)

type_confusion = Counter()
for entry in results['missed_entities']:
    gold = entry['gold_entity']
    preds = entry['dictabert_predictions']
    
    for pred_text, pred_type in preds:
        if gold.replace('"', '').strip() in pred_text or pred_text in gold.replace('"', '').strip():
            type_confusion[(f"Gold: PERSON", f"Pred: {pred_type}")] += 1

print("   Gold vs Predicted type:")
for (gold_type, pred_type), count in type_confusion.most_common(10):
    print(f"   • {gold_type} → {pred_type}: {count}")

# Latin names analysis
print("\n📊 LATIN NAME HANDLING:")
print("-" * 40)

latin_examples = []
for entry in results['missed_entities'] + results['partial_matches']:
    gold = entry['gold_entity']
    if any(c.isascii() and c.isalpha() for c in gold):
        latin_examples.append(entry)

print(f"   Latin/mixed script entities: {len(latin_examples)}")
for entry in latin_examples[:5]:
    print(f"   • Gold: {entry['gold_entity']}")
    print(f"     DictaBERT: {entry['dictabert_predictions']}")
    print()

# Real examples for the paper
print("\n" + "=" * 80)
print("REAL EXAMPLES FOR PAPER CLAIMS")
print("=" * 80)

print("\n1. PATRONYMIC SPLITTING - REAL EXAMPLES:")
print("-" * 40)
count = 0
for entry in results['patronymic_errors']:
    gold = entry['gold_entity']
    preds = entry['dictabert_predictions']
    pred_texts = [p[0] for p in preds if p[1] == 'PER']
    
    # Find cases where parts of the name are extracted separately
    if 'בן' in gold:
        gold_parts = gold.split('בן')
        if len(gold_parts) >= 2:
            first = gold_parts[0].strip()
            second = gold_parts[1].strip() if len(gold_parts) > 1 else ""
            
            # Check if first and second parts are separate entities
            first_found = any(first in p for p in pred_texts)
            second_found = any(second in p for p in pred_texts)
            full_found = any(gold.replace('"', '').strip() in p for p in pred_texts)
            
            if first_found and second_found and not full_found:
                count += 1
                if count <= 3:
                    print(f"\nExample {count}:")
                    print(f"   Text: {entry['text'][:80]}...")
                    print(f"   Gold: {gold}")
                    print(f"   DictaBERT extracted separately:")
                    for p in preds:
                        if p[1] == 'PER':
                            print(f"      → {p[0]}")

print(f"\n   Total confirmed patronymic splits: {count}")

print("\n2. ROLE CLASSIFICATION - DICTABERT LIMITATIONS:")
print("-" * 40)
print("   DictaBERT NER uses entity types: PER, ORG, GPE, WOA (work), EVE, FAC")
print("   It does NOT classify roles (author, transcriber, owner, censor)")
print("   This is NOT an error but a task difference - DictaBERT doesn't do role classification")

print("\n3. BOUNDARY ERRORS - REAL EXAMPLES:")
print("-" * 40)
for i, entry in enumerate(results['boundary_errors'][:3]):
    print(f"\nExample {i+1}:")
    print(f"   Text: {entry['text'][:80]}...")
    print(f"   Gold: {entry['gold_entity']}")
    print(f"   DictaBERT: {entry['dictabert_predictions']}")

print("\n4. COMPLETELY MISSED - LATIN NAMES:")
print("-" * 40)
latin_missed = [e for e in results['missed_entities'] 
                if any(c.isascii() and c.isalpha() for c in e['gold_entity'])
                and not e['dictabert_predictions']]
print(f"   Latin names with no DictaBERT output: {len(latin_missed)}")
for entry in latin_missed[:3]:
    print(f"   • Gold: {entry['gold_entity']}")
    print(f"     Text: {entry['text'][:60]}...")

# Summary statistics
print("\n" + "=" * 80)
print("SUMMARY FOR PAPER")
print("=" * 80)

total_samples = 500
total_errors = (len(results['patronymic_errors']) + 
                len(results['boundary_errors']) + 
                len(results['partial_matches']) + 
                len(results['missed_entities']))

print(f"""
Based on {total_samples} test samples:

WHAT DICTABERT DOES WELL:
- Recognizes many Hebrew person names
- Captures patronymic patterns in many cases
- Identifies standard Hebrew entities

WHAT DICTABERT STRUGGLES WITH:
1. Entity Type Classification: Classifies some persons as WOA (works)
   e.g., "אונקלוס" classified as WOA instead of PER
   
2. Latin Names: Hebrew tokenizer doesn't handle Latin names well
   e.g., "Domenico Carretto" → no output
   
3. Domain-specific terminology: Historical role terms not in training
   
4. Our approach adds:
   - Role classification (author, transcriber, owner, censor)
   - Domain-specific training on manuscript catalogs
   - Multi-entity handling in same context

NOTE: The claim about "patronymic splitting" needs verification.
DictaBERT often captures full patronymic names correctly.
The real difference is role classification capability.
""")

