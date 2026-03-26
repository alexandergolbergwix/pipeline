"""
Joint Entity-Role Extraction Model with Multi-Task Learning
5-Fold Stratified Cross-Validation Version
"""

import json
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, Subset
from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup
from torch.optim import AdamW
from sklearn.metrics import classification_report, accuracy_score
from sklearn.model_selection import StratifiedKFold
from seqeval.metrics import classification_report as seqeval_report, f1_score as seqeval_f1
from tqdm import tqdm
import numpy as np
import argparse
import os
import copy


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
    
    def __init__(self, samples: list, tokenizer, max_length: int = 256, 
                 ner_label2id=None, class_label2id=None):
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.samples = samples
        
        # Label mappings
        self.ner_label2id = ner_label2id or {'O': 0, 'B-PERSON': 1, 'I-PERSON': 2}
        self.ner_id2label = {v: k for k, v in self.ner_label2id.items()}
        
        self.class_label2id = class_label2id or {
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
    
    def get_stratification_labels(self):
        """Get labels for stratified splitting (based on role classification)"""
        labels = []
        for sample in self.samples:
            roles = sample.get('roles', ['AUTHOR'])
            class_label = roles[0] if roles else 'AUTHOR'
            labels.append(self.class_label2id.get(class_label, 0))
        return np.array(labels)


def load_all_data(data_paths: list):
    """Load all samples from multiple data files"""
    all_samples = []
    for path in data_paths:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    sample = json.loads(line)
                    all_samples.append(sample)
    return all_samples


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


def evaluate(model, dataloader, ner_id2label, class_id2label, device, disable_progress=False, verbose=True):
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
    
    if verbose:
        print(f"\n{'='*60}")
        print(f"NER F1: {ner_f1:.4f}")
        print(f"Classification Accuracy: {class_acc:.4f}")
        print(f"{'='*60}\n")
        
        print("NER Report:")
        print(seqeval_report(all_ner_labels, all_ner_preds))
        
        print("\nClassification Report:")
        print(classification_report(all_class_labels, all_class_preds, digits=4))
    
    return ner_f1, class_acc


def train_fold(model, train_loader, val_loader, optimizer, scheduler, 
               ner_id2label, class_id2label, device, args, fold_num):
    """Train a single fold"""
    best_combined_score = 0
    patience_counter = 0
    best_model_state = None
    best_ner_f1 = 0
    best_class_acc = 0
    
    for epoch in range(args.epochs):
        print(f"\n  Epoch {epoch + 1}/{args.epochs}")
        
        # Train with progress bar
        total_loss, ner_loss, class_loss = train_epoch(
            model, train_loader, optimizer, scheduler, device, 
            args.lambda_weight, disable_progress=False
        )
        print(f"  Training - Total: {total_loss:.4f}, NER: {ner_loss:.4f}, Class: {class_loss:.4f}")
        
        # Validate with progress bar
        val_ner_f1, val_class_acc = evaluate(
            model, val_loader, ner_id2label, class_id2label, device,
            disable_progress=False, verbose=False
        )
        print(f"  Validation - NER F1: {val_ner_f1:.4f}, Class Acc: {val_class_acc:.4f}")
        
        # Combined score (geometric mean)
        combined_score = (val_ner_f1 * val_class_acc) ** 0.5
        
        # Save best model
        if combined_score > best_combined_score:
            best_combined_score = combined_score
            best_ner_f1 = val_ner_f1
            best_class_acc = val_class_acc
            patience_counter = 0
            best_model_state = copy.deepcopy(model.state_dict())
            print(f"  New best combined score: {combined_score:.4f}")
        else:
            patience_counter += 1
            if patience_counter >= args.early_stopping_patience:
                print(f"  Early stopping triggered after {epoch + 1} epochs!")
                break
    
    # Restore best model
    if best_model_state:
        model.load_state_dict(best_model_state)
    
    return best_ner_f1, best_class_acc


def main():
    parser = argparse.ArgumentParser(description='Joint Entity-Role Model Training with 5-Fold CV')
    parser.add_argument('--model-name', type=str,
                       default='dicta-il/dictabert',
                       help='Base BERT model')
    parser.add_argument('--data-files', type=str, nargs='+',
                       default=[
                           'processed-data/multi_entity_train_filtered.jsonl',
                           'processed-data/multi_entity_val_filtered.jsonl',
                           'processed-data/multi_entity_test.jsonl'
                       ],
                       help='Data files to combine for k-fold CV')
    parser.add_argument('--output-dir', type=str,
                       default='joint_entity_role_model_kfold',
                       help='Output directory')
    parser.add_argument('--batch-size', type=int, default=8,
                       help='Batch size')
    parser.add_argument('--learning-rate', type=float, default=2e-5,
                       help='Learning rate')
    parser.add_argument('--epochs', type=int, default=10,
                       help='Number of epochs per fold')
    parser.add_argument('--n-folds', type=int, default=5,
                       help='Number of folds for cross-validation')
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
    parser.add_argument('--seed', type=int, default=42,
                       help='Random seed')

    args = parser.parse_args()
    
    # Set random seed for reproducibility
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    
    print("\n" + "="*60)
    print("Joint Entity-Role Extraction Model")
    print("5-Fold Stratified Cross-Validation")
    print("="*60)
    print(f"Model: {args.model_name}")
    print(f"Lambda weight: {args.lambda_weight}")
    print(f"Learning rate: {args.learning_rate}")
    print(f"Dropout: {args.dropout}")
    print(f"Weight decay: {args.weight_decay}")
    print(f"Early stopping patience: {args.early_stopping_patience}")
    print(f"Batch size: {args.batch_size}")
    print(f"Epochs per fold: {args.epochs}")
    print(f"Number of folds: {args.n_folds}")
    print(f"Random seed: {args.seed}")
    print("="*60 + "\n")
    
    # Device
    device = torch.device('mps' if torch.backends.mps.is_available() else 
                         'cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}\n")
    
    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    
    # Load all data
    print("Loading data...")
    all_samples = load_all_data(args.data_files)
    print(f"Total samples: {len(all_samples)}")
    
    # Create dataset to get label mappings
    ner_label2id = {'O': 0, 'B-PERSON': 1, 'I-PERSON': 2}
    ner_id2label = {v: k for k, v in ner_label2id.items()}
    class_label2id = {
        'AUTHOR': 0,
        'TRANSCRIBER': 1,
        'OWNER': 2,
        'CENSOR': 3,
        'TRANSLATOR': 4,
        'COMMENTATOR': 5
    }
    class_id2label = {v: k for k, v in class_label2id.items()}
    
    # Get stratification labels
    stratification_labels = []
    for sample in all_samples:
        roles = sample.get('roles', ['AUTHOR'])
        class_label = roles[0] if roles else 'AUTHOR'
        stratification_labels.append(class_label2id.get(class_label, 0))
    stratification_labels = np.array(stratification_labels)
    
    # Setup k-fold
    skf = StratifiedKFold(n_splits=args.n_folds, shuffle=True, random_state=args.seed)
    
    # Store results for each fold
    fold_results = []
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Run k-fold cross-validation
    for fold, (train_idx, val_idx) in enumerate(skf.split(all_samples, stratification_labels)):
        print(f"\n{'='*60}")
        print(f"FOLD {fold + 1}/{args.n_folds}")
        print(f"{'='*60}")
        print(f"Train samples: {len(train_idx)}, Validation samples: {len(val_idx)}")
        
        # Create datasets for this fold
        train_samples = [all_samples[i] for i in train_idx]
        val_samples = [all_samples[i] for i in val_idx]
        
        train_dataset = JointDataset(train_samples, tokenizer, args.max_length,
                                     ner_label2id, class_label2id)
        val_dataset = JointDataset(val_samples, tokenizer, args.max_length,
                                   ner_label2id, class_label2id)
        
        train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=args.batch_size)
        
        # Initialize fresh model for each fold
        num_ner_labels = len(ner_label2id)
        num_class_labels = len(class_label2id)
        
        model = JointModel(args.model_name, num_ner_labels, num_class_labels, dropout=args.dropout)
        model.to(device)
        
        # Optimizer and scheduler
        optimizer = AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
        total_steps = len(train_loader) * args.epochs
        scheduler = get_linear_schedule_with_warmup(
            optimizer,
            num_warmup_steps=int(0.1 * total_steps),
            num_training_steps=total_steps
        )
        
        # Train this fold
        ner_f1, class_acc = train_fold(
            model, train_loader, val_loader, optimizer, scheduler,
            ner_id2label, class_id2label, device, args, fold + 1
        )
        
        fold_results.append({
            'fold': fold + 1,
            'ner_f1': ner_f1,
            'class_acc': class_acc,
            'combined_score': (ner_f1 * class_acc) ** 0.5
        })
        
        # Save fold model
        torch.save({
            'fold': fold + 1,
            'model_state_dict': model.state_dict(),
            'ner_f1': ner_f1,
            'class_acc': class_acc
        }, f"{args.output_dir}/fold_{fold + 1}_model.pt")
        
        print(f"\nFold {fold + 1} Results: NER F1 = {ner_f1:.4f}, Class Acc = {class_acc:.4f}")
    
    # Compute summary statistics
    ner_f1_scores = [r['ner_f1'] for r in fold_results]
    class_acc_scores = [r['class_acc'] for r in fold_results]
    
    mean_ner_f1 = np.mean(ner_f1_scores)
    std_ner_f1 = np.std(ner_f1_scores)
    mean_class_acc = np.mean(class_acc_scores)
    std_class_acc = np.std(class_acc_scores)
    
    print("\n" + "="*60)
    print("5-FOLD CROSS-VALIDATION RESULTS")
    print("="*60)
    print(f"\nNER F1 Scores by Fold:")
    for i, f1 in enumerate(ner_f1_scores):
        print(f"  Fold {i+1}: {f1:.4f} ({f1*100:.2f}%)")
    
    print(f"\nClassification Accuracy by Fold:")
    for i, acc in enumerate(class_acc_scores):
        print(f"  Fold {i+1}: {acc:.4f} ({acc*100:.2f}%)")
    
    print(f"\n{'='*60}")
    print(f"SUMMARY STATISTICS")
    print(f"{'='*60}")
    print(f"NER F1:              {mean_ner_f1:.4f} ± {std_ner_f1:.4f} ({mean_ner_f1*100:.2f}% ± {std_ner_f1*100:.2f}%)")
    print(f"Classification Acc:  {mean_class_acc:.4f} ± {std_class_acc:.4f} ({mean_class_acc*100:.2f}% ± {std_class_acc*100:.2f}%)")
    print(f"Best Fold (NER F1):  Fold {np.argmax(ner_f1_scores) + 1}: {max(ner_f1_scores):.4f}")
    print(f"{'='*60}\n")
    
    # Save all results
    summary = {
        'model': 'Joint Entity-Role Model',
        'base_model': args.model_name,
        'n_folds': args.n_folds,
        'total_samples': len(all_samples),
        'lambda_weight': args.lambda_weight,
        'fold_results': fold_results,
        'summary': {
            'mean_ner_f1': float(mean_ner_f1),
            'std_ner_f1': float(std_ner_f1),
            'mean_class_acc': float(mean_class_acc),
            'std_class_acc': float(std_class_acc),
            'best_fold_ner': int(np.argmax(ner_f1_scores) + 1),
            'best_ner_f1': float(max(ner_f1_scores))
        }
    }
    
    with open(f"{args.output_dir}/kfold_results.json", 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"Results saved to {args.output_dir}/kfold_results.json")
    print(f"Individual fold models saved to {args.output_dir}/fold_X_model.pt")


if __name__ == "__main__":
    main()

