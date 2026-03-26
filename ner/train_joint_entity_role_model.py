"""
Joint Entity-Role Extraction Model with Multi-Task Learning
Single model with shared encoder and dual heads for NER and classification
Expected improvement: +0.5-0.7% end-to-end accuracy
"""

import json
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup
from torch.optim import AdamW
from sklearn.metrics import classification_report, accuracy_score
from seqeval.metrics import classification_report as seqeval_report, f1_score as seqeval_f1
from tqdm import tqdm
import numpy as np
import argparse
import os


class JointModel(nn.Module):
    """
    Joint model for simultaneous NER and role classification
    
    Architecture:
    - Shared BERT encoder
    - NER head for token classification
    - Classification head for role prediction
    - Multi-task loss: L = L_NER + λ * L_classification
    """
    
    def __init__(self, bert_model_name: str, num_ner_labels: int, num_class_labels: int, 
                 dropout: float = 0.1):
        super(JointModel, self).__init__()
        
        # Shared encoder
        self.bert = AutoModel.from_pretrained(bert_model_name)
        hidden_size = self.bert.config.hidden_size
        
        # NER head
        self.ner_dropout = nn.Dropout(dropout)
        self.ner_classifier = nn.Linear(hidden_size, num_ner_labels)
        
        # Classification head
        self.class_dropout = nn.Dropout(dropout)
        self.class_classifier = nn.Linear(hidden_size, num_class_labels)
        
        # Optional: Add task-specific layers
        self.ner_intermediate = nn.Linear(hidden_size, hidden_size // 2)
        self.class_intermediate = nn.Linear(hidden_size, hidden_size // 2)
        self.ner_output = nn.Linear(hidden_size // 2, num_ner_labels)
        self.class_output = nn.Linear(hidden_size // 2, num_class_labels)
        
        self.use_intermediate = True
    
    def forward(self, input_ids, attention_mask, 
                ner_labels=None, class_labels=None, lambda_weight=0.5):
        """
        Forward pass with multi-task learning
        
        Args:
            input_ids: (batch, seq_len)
            attention_mask: (batch, seq_len)
            ner_labels: (batch, seq_len) - optional for training
            class_labels: (batch,) - optional for training
            lambda_weight: Weight for classification loss
            
        Returns:
            If training: total_loss, ner_loss, class_loss
            If inference: ner_logits, class_logits
        """
        # Shared encoder
        outputs = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        sequence_output = outputs.last_hidden_state  # (batch, seq_len, hidden)
        pooled_output = outputs.pooler_output  # (batch, hidden)
        
        # NER head
        if self.use_intermediate:
            ner_hidden = torch.relu(self.ner_intermediate(sequence_output))
            ner_hidden = self.ner_dropout(ner_hidden)
            ner_logits = self.ner_output(ner_hidden)
        else:
            ner_hidden = self.ner_dropout(sequence_output)
            ner_logits = self.ner_classifier(ner_hidden)
        
        # Classification head
        if self.use_intermediate:
            class_hidden = torch.relu(self.class_intermediate(pooled_output))
            class_hidden = self.class_dropout(class_hidden)
            class_logits = self.class_output(class_hidden)
        else:
            class_hidden = self.class_dropout(pooled_output)
            class_logits = self.class_classifier(class_hidden)
        
        # Compute losses if labels provided
        if ner_labels is not None and class_labels is not None:
            # NER loss (token-level cross entropy)
            loss_fct_ner = nn.CrossEntropyLoss()
            
            # Only compute loss on non-padded tokens
            active_loss = attention_mask.view(-1) == 1
            active_logits = ner_logits.view(-1, ner_logits.size(-1))
            active_labels = torch.where(
                active_loss,
                ner_labels.view(-1),
                torch.tensor(loss_fct_ner.ignore_index).type_as(ner_labels)
            )
            ner_loss = loss_fct_ner(active_logits, active_labels)
            
            # Classification loss
            loss_fct_class = nn.CrossEntropyLoss()
            class_loss = loss_fct_class(class_logits, class_labels)
            
            # Combined loss
            total_loss = ner_loss + lambda_weight * class_loss
            
            return total_loss, ner_loss, class_loss
        
        else:
            return ner_logits, class_logits


class JointDataset(Dataset):
    """Dataset for joint NER and classification"""
    
    def __init__(self, data_path: str, tokenizer, max_length: int = 256):
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.samples = []
        
        # Load data
        with open(data_path, 'r', encoding='utf-8') as f:
            for line in f:
                sample = json.loads(line)
                self.samples.append(sample)
        
        # Label mappings
        self.ner_label2id = {'O': 0, 'B-PERSON': 1, 'I-PERSON': 2}
        self.ner_id2label = {v: k for k, v in self.ner_label2id.items()}
        
        self.class_label2id = {
            'AUTHOR': 0,
            'TRANSCRIBER': 1,
            'OWNER': 2,
            'CENSOR': 3,
            'TRANSLATOR': 4,
            'COMMENTATOR': 5
        }
        self.class_id2label = {v: k for k, v in self.class_label2id.items()}
    
    def __len__(self):
        return len(self.samples)
    
    def __getitem__(self, idx):
        sample = self.samples[idx]
        tokens = sample['tokens']
        ner_labels = sample.get('labels', sample.get('ner_tags', []))
        
        # Get first entity's role for classification
        roles = sample.get('roles', ['AUTHOR'])
        class_label = roles[0] if roles else 'AUTHOR'
        
        # Tokenize
        encoding = self.tokenizer(
            tokens,
            is_split_into_words=True,
            max_length=self.max_length,
            padding='max_length',
            truncation=True,
            return_tensors='pt'
        )
        
        # Align NER labels
        word_ids = encoding.word_ids()
        aligned_ner_labels = []
        previous_word_id = None
        
        for word_id in word_ids:
            if word_id is None:
                aligned_ner_labels.append(0)
            elif word_id != previous_word_id:
                aligned_ner_labels.append(self.ner_label2id.get(ner_labels[word_id], 0))
            else:
                label = ner_labels[word_id]
                if label.startswith('B-'):
                    aligned_ner_labels.append(self.ner_label2id.get('I-' + label[2:], 0))
                else:
                    aligned_ner_labels.append(self.ner_label2id.get(label, 0))
            previous_word_id = word_id
        
        return {
            'input_ids': encoding['input_ids'].squeeze(),
            'attention_mask': encoding['attention_mask'].squeeze(),
            'ner_labels': torch.tensor(aligned_ner_labels, dtype=torch.long),
            'class_labels': torch.tensor(self.class_label2id.get(class_label, 0), dtype=torch.long)
        }


def train_epoch(model, dataloader, optimizer, scheduler, device, lambda_weight, disable_progress=False):
    """Train for one epoch"""
    model.train()
    total_loss = 0
    total_ner_loss = 0
    total_class_loss = 0
    
    progress_bar = tqdm(dataloader, desc="Training", disable=disable_progress)
    for batch in progress_bar:
        input_ids = batch['input_ids'].to(device)
        attention_mask = batch['attention_mask'].to(device)
        ner_labels = batch['ner_labels'].to(device)
        class_labels = batch['class_labels'].to(device)
        
        optimizer.zero_grad()
        
        # Forward pass
        loss, ner_loss, class_loss = model(
            input_ids, attention_mask, 
            ner_labels, class_labels,
            lambda_weight=lambda_weight
        )
        
        # Backward pass
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()
        
        # Track losses
        total_loss += loss.item()
        total_ner_loss += ner_loss.item()
        total_class_loss += class_loss.item()
        
        progress_bar.set_postfix({
            'loss': loss.item(),
            'ner': ner_loss.item(),
            'class': class_loss.item()
        })
    
    n = len(dataloader)
    return total_loss / n, total_ner_loss / n, total_class_loss / n


def evaluate(model, dataloader, ner_id2label, class_id2label, device, disable_progress=False):
    """Evaluate both NER and classification"""
    model.eval()
    
    all_ner_preds = []
    all_ner_labels = []
    all_class_preds = []
    all_class_labels = []
    
    with torch.no_grad():
        for batch in tqdm(dataloader, desc="Evaluating", disable=disable_progress):
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            ner_labels = batch['ner_labels'].cpu().numpy()
            class_labels = batch['class_labels'].cpu().numpy()
            
            # Get predictions
            ner_logits, class_logits = model(input_ids, attention_mask)
            
            # NER predictions
            ner_preds = torch.argmax(ner_logits, dim=-1).cpu().numpy()
            
            # Classification predictions
            class_preds = torch.argmax(class_logits, dim=-1).cpu().numpy()
            
            # Convert NER to label names (filter padding)
            for pred, label, mask in zip(ner_preds, ner_labels, attention_mask.cpu().numpy()):
                valid_length = mask.sum()
                pred_labels = [ner_id2label[p] for p in pred[:valid_length]]
                true_labels = [ner_id2label[l] for l in label[:valid_length]]
                
                all_ner_preds.append(pred_labels)
                all_ner_labels.append(true_labels)
            
            # Classification labels
            all_class_preds.extend([class_id2label[p] for p in class_preds])
            all_class_labels.extend([class_id2label[l] for l in class_labels])
    
    # Compute NER metrics
    ner_f1 = seqeval_f1(all_ner_labels, all_ner_preds)
    
    # Compute classification metrics
    class_acc = accuracy_score(all_class_labels, all_class_preds)
    
    print(f"\n{'='*60}")
    print(f"NER F1: {ner_f1:.4f}")
    print(f"Classification Accuracy: {class_acc:.4f}")
    print(f"{'='*60}\n")
    
    print("NER Report:")
    print(seqeval_report(all_ner_labels, all_ner_preds))
    
    print("\nClassification Report:")
    print(classification_report(all_class_labels, all_class_preds, digits=4))
    
    return ner_f1, class_acc


def main():
    parser = argparse.ArgumentParser(description='Joint Entity-Role Model Training')
    parser.add_argument('--model-name', type=str,
                       default='dicta-il/dictabert',
                       help='Base BERT model')
    parser.add_argument('--train-data', type=str,
                       default='processed-data/multi_entity_train_filtered.jsonl',
                       help='Training data')
    parser.add_argument('--val-data', type=str,
                       default='processed-data/multi_entity_val_filtered.jsonl',
                       help='Validation data')
    parser.add_argument('--test-data', type=str,
                       default='processed-data/multi_entity_test.jsonl',
                       help='Test data')
    parser.add_argument('--output-dir', type=str,
                       default='joint_entity_role_model',
                       help='Output directory')
    parser.add_argument('--batch-size', type=int, default=8,
                       help='Batch size')
    parser.add_argument('--learning-rate', type=float, default=2e-5,
                       help='Learning rate')
    parser.add_argument('--epochs', type=int, default=10,
                       help='Number of epochs')
    parser.add_argument('--lambda-weight', type=float, default=0.5,
                       help='Weight for classification loss')
    parser.add_argument('--max-length', type=int, default=256,
                       help='Maximum sequence length')
    parser.add_argument('--dropout', type=float, default=0.3,
                       help='Dropout rate for regularization')
    parser.add_argument('--weight-decay', type=float, default=0.01,
                       help='Weight decay (L2 regularization)')
    parser.add_argument('--early-stopping-patience', type=int, default=3,
                       help='Early stopping patience (epochs)')
    parser.add_argument('--disable-progress', action='store_true',
                       help='Disable tqdm progress bars for logging')

    args = parser.parse_args()
    
    print("\n" + "="*60)
    print("Joint Entity-Role Extraction Model")
    print("="*60)
    print(f"Model: {args.model_name}")
    print(f"Lambda weight: {args.lambda_weight}")
    print(f"Learning rate: {args.learning_rate}")
    print(f"Dropout: {args.dropout}")
    print(f"Weight decay: {args.weight_decay}")
    print(f"Early stopping patience: {args.early_stopping_patience}")
    print(f"Batch size: {args.batch_size}")
    print(f"Epochs: {args.epochs}")
    print("="*60 + "\n")
    
    # Device
    device = torch.device('mps' if torch.backends.mps.is_available() else 
                         'cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}\n")
    
    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    
    # Load datasets
    train_dataset = JointDataset(args.train_data, tokenizer, args.max_length)
    val_dataset = JointDataset(args.val_data, tokenizer, args.max_length)
    test_dataset = JointDataset(args.test_data, tokenizer, args.max_length)
    
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size)
    test_loader = DataLoader(test_dataset, batch_size=args.batch_size)
    
    # Initialize model
    num_ner_labels = len(train_dataset.ner_label2id)
    num_class_labels = len(train_dataset.class_label2id)
    
    model = JointModel(args.model_name, num_ner_labels, num_class_labels, dropout=args.dropout)
    model.to(device)
    
    # Optimizer and scheduler with weight decay
    optimizer = AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    total_steps = len(train_loader) * args.epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(0.1 * total_steps),
        num_training_steps=total_steps
    )
    
    # Training loop
    best_combined_score = 0
    patience_counter = 0
    os.makedirs(args.output_dir, exist_ok=True)
    
    for epoch in range(args.epochs):
        print(f"\n{'='*60}")
        print(f"Epoch {epoch + 1}/{args.epochs}")
        print(f"{'='*60}")
        
        # Train
        total_loss, ner_loss, class_loss = train_epoch(
            model, train_loader, optimizer, scheduler, device, args.lambda_weight, args.disable_progress
        )
        print(f"\nTraining - Total: {total_loss:.4f}, NER: {ner_loss:.4f}, Class: {class_loss:.4f}")
        
        # Validate
        print("\nValidation:")
        val_ner_f1, val_class_acc = evaluate(
            model, val_loader, 
            train_dataset.ner_id2label, 
            train_dataset.class_id2label, 
            device,
            args.disable_progress
        )
        
        # Combined score (geometric mean)
        combined_score = (val_ner_f1 * val_class_acc) ** 0.5
        
        # Save best model
        if combined_score > best_combined_score:
            best_combined_score = combined_score
            patience_counter = 0
            print(f"\nNew best combined score: {combined_score:.4f}. Saving model...")
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'best_score': combined_score,
                'ner_f1': val_ner_f1,
                'class_acc': val_class_acc
            }, f"{args.output_dir}/best_model.pt")
        else:
            patience_counter += 1
            print(f"\nNo improvement. Patience: {patience_counter}/{args.early_stopping_patience}")
            
            if patience_counter >= args.early_stopping_patience:
                print(f"\nEarly stopping triggered after {epoch + 1} epochs!")
                break
    
    # Test with best model
    print(f"\n{'='*60}")
    print("Testing with best model")
    print(f"{'='*60}")
    
    checkpoint = torch.load(f"{args.output_dir}/best_model.pt", weights_only=False)
    model.load_state_dict(checkpoint['model_state_dict'])
    
    test_ner_f1, test_class_acc = evaluate(
        model, test_loader,
        train_dataset.ner_id2label,
        train_dataset.class_id2label,
        device,
        args.disable_progress
    )
    
    # Save results
    results = {
        'model': 'Joint Entity-Role Model',
        'base_model': args.model_name,
        'lambda_weight': args.lambda_weight,
        'best_val_ner_f1': float(val_ner_f1),
        'best_val_class_acc': float(val_class_acc),
        'best_combined_score': float(best_combined_score),
        'test_ner_f1': float(test_ner_f1),
        'test_class_acc': float(test_class_acc),
        'test_combined_score': float((test_ner_f1 * test_class_acc) ** 0.5)
    }
    
    with open(f"{args.output_dir}/results.json", 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults saved to {args.output_dir}/results.json")
    print(f"Test NER F1: {test_ner_f1:.4f}")
    print(f"Test Classification Accuracy: {test_class_acc:.4f}")
    print(f"Test Combined Score: {results['test_combined_score']:.4f}")


if __name__ == "__main__":
    main()

