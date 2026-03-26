"""
Test the trained HalleluBERT NER model.
"""

from transformers import pipeline, AutoTokenizer, AutoModelForTokenClassification
import torch

MODEL_DIR = "models/hallelubert-ner-hebrew-manuscripts"

print("="*80)
print("TESTING HALLELUBERT NER MODEL")
print("="*80)

# Load model
print(f"\nLoading model from {MODEL_DIR}...")
try:
    model = AutoModelForTokenClassification.from_pretrained(MODEL_DIR)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
    print("✓ Model loaded successfully")
    print(f"  Parameters: {model.num_parameters():,}")
    print(f"  Labels: {model.num_labels}")
except Exception as e:
    print(f"✗ Error loading model: {e}")
    exit(1)

# Create NER pipeline
print("\nCreating NER pipeline...")
ner = pipeline(
    "ner",
    model=model,
    tokenizer=tokenizer,
    aggregation_strategy="simple",
    device=-1  # CPU
)
print("✓ Pipeline created")

# Test examples
test_texts = [
    "ספר תהלים נכתב על ידי דוד המלך בירושלים",
    "רמב\"ם כתב את משנה תורה בקהיר במאה ה-12",
    "כתב יד של ספר בראשית מהמאה ה-15 נמצא בפירנצה",
    "הגדה של פסח עם פירוש רש\"י מיוחס לרבי משה בן יעקב",
]

print("\n" + "="*80)
print("TEST RESULTS")
print("="*80)

for i, text in enumerate(test_texts, 1):
    print(f"\n{i}. Text: {text}")
    print("   Entities:")
    
    try:
        results = ner(text)
        
        if results:
            for entity in results:
                print(f"     - {entity['word']:30s} | {entity['entity_group']:12s} | Score: {entity['score']:.3f}")
        else:
            print("     (No entities detected)")
            
    except Exception as e:
        print(f"     Error: {e}")

print("\n" + "="*80)
print("TESTING COMPLETE")
print("="*80)
print("\nNote: Model achieved F1=0.38 overall")
print("  - Best for WORK entities (F1=0.42)")
print("  - Moderate for PERSON entities (F1=0.32)")
print("  - Limited performance on DATE/PLACE/ROLE due to small training data")

