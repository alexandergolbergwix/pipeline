"""
Improved training script for HalleluBERT NER with balanced data.
Changes from previous version:
- Uses HalleluBERT_base (110M params) instead of _large (356M params)
- Balanced dataset with 150K annotations
- Class weights to handle remaining imbalances
- 15-20 epochs with early stopping
- 80/10/10 train/val/test split
- Increased learning rate for base model
"""

import json
import torch
import os

# Force CPU-only training to avoid MPS memory issues
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
torch.backends.mps.enable = False

from transformers import (
    AutoTokenizer,
    AutoModelForTokenClassification,
    TrainingArguments,
    Trainer,
    DataCollatorForTokenClassification,
    EarlyStoppingCallback
)
from datasets import Dataset
from sklearn.model_selection import train_test_split
from seqeval.metrics import classification_report, f1_score, precision_score, recall_score
import numpy as np
import os
import gc
from peft import LoraConfig, get_peft_model, TaskType

print("="*80)
print("HALLELUBERT_BASE NER TRAINING (IMPROVED)")
print("="*80)

# Configuration
MODEL_NAME = "HalleluBERT/HalleluBERT_base"  # Changed from _large to _base
DATA_FILE = 'processed-data/hallelubert_training_data_balanced.json'
OUTPUT_DIR = 'models/hallelubert-base-ner-improved'
MAX_LENGTH = 512  # RoBERTa limit

# Class weights (from balance_dataset.py output)
CLASS_WEIGHTS = {
    0: 1.00,   # O
    1: 1.00,   # B-PERSON
    2: 1.00,   # I-PERSON
    3: 1.67,   # B-PLACE
    4: 1.67,   # I-PLACE
    5: 1.67,   # B-DATE
    6: 1.67,   # I-DATE
    7: 1.00,   # B-WORK
    8: 1.00,   # I-WORK
    9: 2.50,   # B-ROLE
    10: 2.50,  # I-ROLE
    11: 2.50,  # B-ORGANIZATION
    12: 2.50,  # I-ORGANIZATION
}

# Load data
print(f"\nLoading training data from {DATA_FILE}...")
with open(DATA_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"Loaded {len(data):,} examples")

# Create label mappings
all_labels = set()
for example in data:
    all_labels.update(example['labels'])

label_list = sorted(list(all_labels))
label2id = {label: idx for idx, label in enumerate(label_list)}
id2label = {idx: label for label, idx in label2id.items()}

print(f"\nLabel mappings ({len(label_list)} labels):")
for label, idx in sorted(label2id.items(), key=lambda x: x[1]):
    print(f"  {idx:2d}: {label}")

# Convert labels to IDs
print("\nConverting labels to IDs...")
for example in data:
    example['labels'] = [label2id[label] for label in example['labels']]

# Split data: 80% train, 10% val, 10% test
print("\nSplitting data (80% train, 10% val, 10% test)...")
train_data, temp_data = train_test_split(data, test_size=0.2, random_state=42)
val_data, test_data = train_test_split(temp_data, test_size=0.5, random_state=42)

print(f"Train examples: {len(train_data):,}")
print(f"Validation examples: {len(val_data):,}")
print(f"Test examples: {len(test_data):,}")

# Load tokenizer
print(f"\nLoading tokenizer from {MODEL_NAME}...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

# Truncate long sequences
def truncate_example(tokens, labels, max_len=MAX_LENGTH):
    """Truncate tokens and labels to max length"""
    return tokens[:max_len], labels[:max_len]

# Create Hugging Face datasets with truncation
print("\nCreating datasets...")

train_truncated = [truncate_example(ex['tokens'], ex['labels']) for ex in train_data]
val_truncated = [truncate_example(ex['tokens'], ex['labels']) for ex in val_data]
test_truncated = [truncate_example(ex['tokens'], ex['labels']) for ex in test_data]

train_dataset = Dataset.from_dict({
    'input_ids': [tokenizer.convert_tokens_to_ids(tokens) for tokens, _ in train_truncated],
    'labels': [labels for _, labels in train_truncated],
    'attention_mask': [[1] * len(tokens) for tokens, _ in train_truncated]
})

val_dataset = Dataset.from_dict({
    'input_ids': [tokenizer.convert_tokens_to_ids(tokens) for tokens, _ in val_truncated],
    'labels': [labels for _, labels in val_truncated],
    'attention_mask': [[1] * len(tokens) for tokens, _ in val_truncated]
})

test_dataset = Dataset.from_dict({
    'input_ids': [tokenizer.convert_tokens_to_ids(tokens) for tokens, _ in test_truncated],
    'labels': [labels for _, labels in test_truncated],
    'attention_mask': [[1] * len(tokens) for tokens, _ in test_truncated]
})

print(f"Train dataset: {len(train_dataset):,} examples")
print(f"Val dataset: {len(val_dataset):,} examples")
print(f"Test dataset: {len(test_dataset):,} examples")

# Load model
print(f"\nLoading model: {MODEL_NAME}...")
print("(This is the BASE model with 110M parameters, better for our dataset size)")
model = AutoModelForTokenClassification.from_pretrained(
    MODEL_NAME,
    num_labels=len(label_list),
    id2label=id2label,
    label2id=label2id
)

# Enable gradient checkpointing to save memory
if hasattr(model, 'gradient_checkpointing_enable'):
    model.gradient_checkpointing_enable()
    print("✓ Gradient checkpointing enabled")

# Configure LoRA for memory-efficient fine-tuning
print("\nConfiguring LoRA (Low-Rank Adaptation)...")
lora_config = LoraConfig(
    task_type=TaskType.TOKEN_CLS,
    r=8,  # Low rank (smaller = less memory)
    lora_alpha=16,  # Scaling factor
    target_modules=["query", "value"],  # Which attention layers to adapt
    lora_dropout=0.1,
    bias="none",
)

# Wrap model with LoRA
model = get_peft_model(model, lora_config)
print("✓ LoRA configuration applied")
model.print_trainable_parameters()

# Move to GPU if available
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")
model.to(device)

# Data collator
data_collator = DataCollatorForTokenClassification(
    tokenizer=tokenizer,
    padding=True,
    max_length=MAX_LENGTH,
    pad_to_multiple_of=None
)

# Custom trainer with class weights and memory management
class WeightedTrainer(Trainer):
    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        logits = outputs.logits
        
        # Apply class weights - ensure weights are on same device as logits
        class_weights_tensor = torch.tensor(
            [CLASS_WEIGHTS.get(i, 1.0) for i in range(len(label_list))],
            device=logits.device
        )
        loss_fct = torch.nn.CrossEntropyLoss(weight=class_weights_tensor)
        
        # Flatten for loss calculation
        loss = loss_fct(logits.view(-1, len(label_list)), labels.view(-1))
        
        return (loss, outputs) if return_outputs else loss
    
    def training_step(self, model, inputs, num_items_in_batch=None):
        """Override training_step to add memory cleanup"""
        loss = super().training_step(model, inputs, num_items_in_batch)
        
        # Clear cache and collect garbage every 10 steps to prevent memory accumulation
        if self.state.global_step % 10 == 0:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()
        
        return loss

# Metrics
def compute_metrics(eval_pred):
    predictions, labels = eval_pred
    predictions = np.argmax(predictions, axis=2)
    
    # Remove padding (-100 labels)
    true_labels = [[id2label[l] for l in label if l != -100] for label in labels]
    true_predictions = [
        [id2label[p] for (p, l) in zip(prediction, label) if l != -100]
        for prediction, label in zip(predictions, labels)
    ]
    
    # Calculate metrics
    f1 = f1_score(true_labels, true_predictions)
    precision = precision_score(true_labels, true_predictions)
    recall = recall_score(true_labels, true_predictions)
    
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1
    }

# Training arguments
print("\nSetting up training configuration...")
training_args = TrainingArguments(
    output_dir=OUTPUT_DIR,
    num_train_epochs=20,  # Increased from 5 to 20
    per_device_train_batch_size=12,  # Optimized for 12GB memory limit with LoRA
    per_device_eval_batch_size=24,  # Large batch for fast evaluation
    gradient_accumulation_steps=1,  # No accumulation needed with large batch
    learning_rate=3e-5,  # Increased from 2e-5 (recommended for base model)
    weight_decay=0.01,
    warmup_ratio=0.1,
    eval_strategy="epoch",
    save_strategy="epoch",
    save_total_limit=2,  # Reduced from 3 to save disk space
    load_best_model_at_end=True,
    metric_for_best_model="f1",
    greater_is_better=True,
    logging_steps=50,
    logging_first_step=True,
    report_to="none",  # Disable tensorboard
    fp16=False,  # Disable fp16 to save memory
    dataloader_num_workers=0,  # Avoid multiprocessing issues
    seed=42,
    gradient_checkpointing=True,  # Enable gradient checkpointing to save memory
)

print("\nTraining configuration:")
print(f"  Model: {MODEL_NAME} (BASE - 110M params)")
print(f"  Epochs: {training_args.num_train_epochs}")
print(f"  Batch size: {training_args.per_device_train_batch_size}")
print(f"  Learning rate: {training_args.learning_rate}")
print(f"  Early stopping: patience=5")
print(f"  Class weights: Applied (minorities weighted up to 2.5x)")
print(f"  Device: {device}")

# Initialize trainer
print("\nInitializing trainer with early stopping...")
trainer = WeightedTrainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=val_dataset,
    data_collator=data_collator,
    compute_metrics=compute_metrics,
    callbacks=[EarlyStoppingCallback(early_stopping_patience=5)]  # Stop if no improvement for 5 epochs
)

# Train
print("\n" + "="*80)
print("STARTING TRAINING")
print("="*80)
print("This may take 6-12 hours. Training will stop early if validation F1 stops improving.")
print("Progress will be saved every epoch.\n")

try:
    trainer.train()
    print("\n✓ Training completed successfully!")
except KeyboardInterrupt:
    print("\n⚠ Training interrupted by user")
    print("Saving current state...")
    trainer.save_model(OUTPUT_DIR)
except Exception as e:
    print(f"\n✗ Training failed with error: {e}")
    raise

# Save final model
print(f"\nSaving model to {OUTPUT_DIR}...")
trainer.save_model(OUTPUT_DIR)
tokenizer.save_pretrained(OUTPUT_DIR)

# Evaluate on test set
print("\n" + "="*80)
print("EVALUATING ON TEST SET")
print("="*80)

test_results = trainer.predict(test_dataset)
test_metrics = test_results.metrics

print("\nTest Set Results:")
print(f"  Precision: {test_metrics.get('test_precision', 0):.4f}")
print(f"  Recall:    {test_metrics.get('test_recall', 0):.4f}")
print(f"  F1 Score:  {test_metrics.get('test_f1', 0):.4f}")

# Detailed evaluation
predictions = np.argmax(test_results.predictions, axis=2)
labels = test_results.label_ids

true_labels = [[id2label[l] for l in label if l != -100] for label in labels]
true_predictions = [
    [id2label[p] for (p, l) in zip(prediction, label) if l != -100]
    for prediction, label in zip(predictions, labels)
]

print("\n" + "="*80)
print("DETAILED CLASSIFICATION REPORT")
print("="*80)
print(classification_report(true_labels, true_predictions))

# Save results
results_file = f"{OUTPUT_DIR}/test_results.txt"
with open(results_file, 'w') as f:
    f.write("="*80 + "\n")
    f.write("HALLELUBERT_BASE NER - TEST RESULTS\n")
    f.write("="*80 + "\n\n")
    f.write(f"Model: {MODEL_NAME}\n")
    f.write(f"Dataset: Balanced (150K annotations, 9.7K records)\n")
    f.write(f"Test examples: {len(test_dataset)}\n\n")
    f.write(f"Overall Metrics:\n")
    f.write(f"  Precision: {test_metrics.get('test_precision', 0):.4f}\n")
    f.write(f"  Recall:    {test_metrics.get('test_recall', 0):.4f}\n")
    f.write(f"  F1 Score:  {test_metrics.get('test_f1', 0):.4f}\n\n")
    f.write("Detailed Report:\n")
    f.write(classification_report(true_labels, true_predictions))

print(f"\n✓ Results saved to: {results_file}")
print("\n" + "="*80)
print("TRAINING COMPLETE!")
print("="*80)
print(f"Model saved to: {OUTPUT_DIR}")
print(f"Test F1 Score: {test_metrics.get('test_f1', 0):.4f}")
print("\nNext step: Run compare_models.py to benchmark against DictaBERT")
