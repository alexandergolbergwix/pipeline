"""
Production Inference Pipeline
Unified interface for NER and role classification with post-processing
"""

import torch
from transformers import AutoTokenizer, AutoModelForTokenClassification, AutoModelForSequenceClassification
from postprocessing_rules import PostProcessingRules
import argparse
from typing import List, Dict, Tuple
import json


class ProductionNERPipeline:
    """
    Production-ready pipeline for entity extraction and role classification
    
    Features:
    - NER for entity extraction
    - Classification for role prediction
    - Post-processing rule application
    - Uncertainty estimation
    - Batch processing support
    """
    
    def __init__(self, 
                 ner_model_path: str,
                 classifier_model_path: str,
                 use_postprocessing: bool = True,
                 device: str = 'auto'):
        
        if device == 'auto':
            self.device = torch.device('mps' if torch.backends.mps.is_available() else 
                                      'cuda' if torch.cuda.is_available() else 'cpu')
        else:
            self.device = torch.device(device)
        
        print(f"Loading models on device: {self.device}")
        
        # Load NER model
        print(f"Loading NER model: {ner_model_path}")
        self.ner_tokenizer = AutoTokenizer.from_pretrained(ner_model_path)
        self.ner_model = AutoModelForTokenClassification.from_pretrained(ner_model_path)
        self.ner_model.to(self.device)
        self.ner_model.eval()
        
        # Label mappings for NER
        self.ner_id2label = {0: 'O', 1: 'B-PERSON', 2: 'I-PERSON'}
        self.ner_label2id = {'O': 0, 'B-PERSON': 1, 'I-PERSON': 2}
        
        # Load classification model
        print(f"Loading classification model: {classifier_model_path}")
        self.classifier_tokenizer = AutoTokenizer.from_pretrained(classifier_model_path)
        self.classifier_model = AutoModelForSequenceClassification.from_pretrained(classifier_model_path)
        self.classifier_model.to(self.device)
        self.classifier_model.eval()
        
        # Label mappings for classification
        self.class_id2label = {
            0: 'AUTHOR',
            1: 'TRANSCRIBER',
            2: 'OWNER',
            3: 'CENSOR',
            4: 'TRANSLATOR',
            5: 'COMMENTATOR'
        }
        self.class_label2id = {v: k for k, v in self.class_id2label.items()}
        
        # Post-processing
        self.use_postprocessing = use_postprocessing
        if use_postprocessing:
            self.postprocessor = PostProcessingRules()
            print("Post-processing rules enabled")
        
        print("Pipeline ready!\n")
    
    def extract_entities(self, text: str) -> List[Dict]:
        """
        Extract person entities from text
        
        Returns:
            List of entities with text, start, end positions
        """
        # Tokenize
        tokens = text.split()
        encoding = self.ner_tokenizer(
            tokens,
            is_split_into_words=True,
            return_tensors='pt',
            padding=True,
            truncation=True,
            max_length=256
        )
        
        input_ids = encoding['input_ids'].to(self.device)
        attention_mask = encoding['attention_mask'].to(self.device)
        
        # Predict
        with torch.no_grad():
            outputs = self.ner_model(input_ids=input_ids, attention_mask=attention_mask)
            predictions = torch.argmax(outputs.logits, dim=-1)
        
        # Align predictions with original tokens
        word_ids = encoding.word_ids(batch_index=0)
        aligned_predictions = []
        previous_word_id = None
        
        for i, word_id in enumerate(word_ids):
            if word_id is None:
                continue
            if word_id != previous_word_id:
                aligned_predictions.append(predictions[0][i].item())
            previous_word_id = word_id
        
        # Extract entities
        entities = []
        current_entity = []
        current_start = 0
        
        for i, (token, pred) in enumerate(zip(tokens, aligned_predictions)):
            label = self.ner_id2label.get(pred, 'O')
            
            if label.startswith('B-'):
                if current_entity:
                    entity_text = ' '.join(current_entity)
                    entities.append({
                        'text': entity_text,
                        'start': current_start,
                        'end': current_start + len(entity_text),
                        'label': 'PERSON'
                    })
                current_entity = [token]
                current_start = text.find(token, current_start)
            
            elif label.startswith('I-'):
                current_entity.append(token)
            
            else:
                if current_entity:
                    entity_text = ' '.join(current_entity)
                    entities.append({
                        'text': entity_text,
                        'start': current_start,
                        'end': current_start + len(entity_text),
                        'label': 'PERSON'
                    })
                current_entity = []
        
        # Handle last entity
        if current_entity:
            entity_text = ' '.join(current_entity)
            entities.append({
                'text': entity_text,
                'start': current_start,
                'end': current_start + len(entity_text),
                'label': 'PERSON'
            })
        
        return entities
    
    def classify_role(self, person: str, text: str) -> Tuple[str, float]:
        """
        Classify the role of a person in the text
        
        Returns:
            (role, confidence)
        """
        # Two-input format
        input_text = f"[PERSON: {person}] {text}"
        
        encoding = self.classifier_tokenizer(
            input_text,
            return_tensors='pt',
            padding=True,
            truncation=True,
            max_length=128
        )
        
        input_ids = encoding['input_ids'].to(self.device)
        attention_mask = encoding['attention_mask'].to(self.device)
        
        # Predict
        with torch.no_grad():
            outputs = self.classifier_model(input_ids=input_ids, attention_mask=attention_mask)
            probs = torch.softmax(outputs.logits, dim=-1)
            pred = torch.argmax(probs, dim=-1)
            confidence = probs[0][pred].item()
        
        role = self.class_id2label[pred.item()]
        
        return role, confidence
    
    def process_text(self, text: str, apply_postprocessing: bool = None) -> List[Dict]:
        """
        Complete pipeline: extract entities and classify roles
        
        Args:
            text: Input text
            apply_postprocessing: Override instance setting
            
        Returns:
            List of entities with roles and metadata
        """
        if apply_postprocessing is None:
            apply_postprocessing = self.use_postprocessing
        
        # Extract entities
        entities = self.extract_entities(text)
        
        if not entities:
            return []
        
        # Apply post-processing if enabled
        if apply_postprocessing:
            roles_placeholder = ['UNKNOWN'] * len(entities)
            entities, _ = self.postprocessor.apply_all_rules(text, entities, roles_placeholder)
        
        # Classify roles
        results = []
        for entity in entities:
            role, confidence = self.classify_role(entity['text'], text)
            
            # Apply role disambiguation if post-processing enabled
            if apply_postprocessing:
                role = self.postprocessor.disambiguate_role(
                    text, 
                    entity['text'], 
                    role, 
                    confidence
                )
            
            results.append({
                'person': entity['text'],
                'role': role,
                'confidence': confidence,
                'start': entity['start'],
                'end': entity['end'],
                'postprocessed': apply_postprocessing
            })
        
        return results
    
    def process_batch(self, texts: List[str]) -> List[List[Dict]]:
        """Process multiple texts"""
        return [self.process_text(text) for text in texts]


class JointNERPipeline:
    """Inference pipeline using alexgoldberg/hebrew-manuscript-joint-ner-v2.

    The model is a raw nn.Module (JointModel from train_joint_entity_role_model_kfold.py)
    with a shared DictaBERT encoder plus separate NER and role-classification heads.
    Weights are stored as a state-dict checkpoint (pytorch_model.bin).
    forward() returns (ner_logits, class_logits) — a tuple, not a HF output object.
    """

    NER_ID2LABEL: Dict[int, str] = {0: 'O', 1: 'B-PERSON', 2: 'I-PERSON'}
    ROLE_ID2LABEL: Dict[int, str] = {
        0: 'AUTHOR', 1: 'TRANSCRIBER', 2: 'OWNER',
        3: 'CENSOR', 4: 'TRANSLATOR', 5: 'COMMENTATOR',
    }
    _BASE_MODEL = "dicta-il/dictabert"
    _NUM_NER_LABELS = 3
    _NUM_CLASS_LABELS = 6
    _DROPOUT = 0.3

    def __init__(self, model_path: str, device: str = 'auto') -> None:
        import sys as _sys
        import torch
        from pathlib import Path as _Path
        from transformers import AutoTokenizer

        # Ensure ner/ is on sys.path so JointModel can be imported
        _ner_dir = str(_Path(__file__).parent)
        if _ner_dir not in _sys.path:
            _sys.path.insert(0, _ner_dir)
        from train_joint_entity_role_model_kfold import JointModel

        if device == 'auto':
            _dev = (
                'mps' if torch.backends.mps.is_available() else
                'cuda' if torch.cuda.is_available() else 'cpu'
            )
            self.device = torch.device(_dev)
        else:
            self.device = torch.device(device)

        print(f"Loading tokenizer from {self._BASE_MODEL}")
        self.tokenizer = AutoTokenizer.from_pretrained(self._BASE_MODEL)

        print(f"Initialising JointModel ({self._BASE_MODEL})")
        self.model = JointModel(
            bert_model_name=self._BASE_MODEL,
            num_ner_labels=self._NUM_NER_LABELS,
            num_class_labels=self._NUM_CLASS_LABELS,
            dropout=self._DROPOUT,
        )

        weights_path = self._resolve_weights(model_path)
        print(f"Loading weights from {weights_path}")
        checkpoint = torch.load(weights_path, map_location=self.device, weights_only=False)
        state_dict = checkpoint.get('model_state_dict', checkpoint)
        self.model.load_state_dict(state_dict)

        self.model.to(self.device)
        self.model.eval()
        print(f"Joint NER pipeline ready on {self.device}.")

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_weights(model_path: str) -> str:
        """Return a local path to the model weights file.

        Accepts a local directory, a local .pt/.bin file, or a HuggingFace
        repo ID (downloads pytorch_model.bin via huggingface_hub).
        """
        from pathlib import Path
        local = Path(model_path)

        if local.is_dir():
            for name in ('pytorch_model.bin', 'best_model.pt', 'fold_4_model.pt'):
                candidate = local / name
                if candidate.exists():
                    return str(candidate)
            raise FileNotFoundError(f"No model weights found in {model_path}")

        if local.is_file():
            return str(local)

        # Treat as a HuggingFace repo ID
        from huggingface_hub import hf_hub_download
        print(f"Downloading pytorch_model.bin from HuggingFace repo {model_path}")
        return hf_hub_download(repo_id=model_path, filename="pytorch_model.bin")

    # ── inference ────────────────────────────────────────────────────

    def process_text(self, text: str) -> List[Dict]:
        """Extract PERSON entities with roles from *text*.

        Returns dicts with keys: person, role, confidence, start, end.
        The role is predicted once per text and assigned to all detected entities.
        """
        import torch

        tokens = text.split()
        if not tokens:
            return []

        encoding = self.tokenizer(
            tokens,
            is_split_into_words=True,
            return_tensors='pt',
            truncation=True,
            max_length=256,
        )
        input_ids = encoding['input_ids'].to(self.device)
        attention_mask = encoding['attention_mask'].to(self.device)

        with torch.no_grad():
            ner_logits, class_logits = self.model(
                input_ids=input_ids, attention_mask=attention_mask
            )

        ner_preds = torch.argmax(ner_logits[0], dim=-1).cpu().tolist()
        role_probs = torch.softmax(class_logits[0], dim=-1).cpu()
        role_idx = int(torch.argmax(role_probs).item())
        role = self.ROLE_ID2LABEL[role_idx]
        confidence = float(role_probs[role_idx].item())

        # Align subword predictions to word-level (keep first subtoken per word)
        word_ids = encoding.word_ids(batch_index=0)
        aligned: List[int] = []
        prev_word_id = None
        for i, word_id in enumerate(word_ids):
            if word_id is None:
                continue
            if word_id != prev_word_id:
                aligned.append(ner_preds[i])
            prev_word_id = word_id

        # Build entity spans from BIO tags
        entities: List[Dict] = []
        current: List[str] = []
        current_start = 0
        search_from = 0

        for token, pred in zip(tokens, aligned):
            label = self.NER_ID2LABEL.get(pred, 'O')
            if label == 'B-PERSON':
                if current:
                    entity_text = ' '.join(current)
                    entities.append({
                        'person': entity_text,
                        'role': role,
                        'confidence': confidence,
                        'start': current_start,
                        'end': current_start + len(entity_text),
                    })
                    search_from = current_start + len(entity_text)
                current = [token]
                current_start = text.find(token, search_from)
            elif label == 'I-PERSON' and current:
                current.append(token)
            else:
                if current:
                    entity_text = ' '.join(current)
                    entities.append({
                        'person': entity_text,
                        'role': role,
                        'confidence': confidence,
                        'start': current_start,
                        'end': current_start + len(entity_text),
                    })
                    search_from = current_start + len(entity_text)
                    current = []

        if current:
            entity_text = ' '.join(current)
            entities.append({
                'person': entity_text,
                'role': role,
                'confidence': confidence,
                'start': current_start,
                'end': current_start + len(entity_text),
            })

        return entities

    def process_batch(self, texts: List[str]) -> List[List[Dict]]:
        return [self.process_text(text) for text in texts]


def main():
    parser = argparse.ArgumentParser(description='Production NER Inference Pipeline')
    parser.add_argument('--ner-model', type=str, 
                       default='hallelubert_multi_entity/checkpoint-6822',
                       help='Path to NER model')
    parser.add_argument('--classifier-model', type=str,
                       default='hallelubert_role_classifier/checkpoint-680',
                       help='Path to classification model')
    parser.add_argument('--use-postprocessing', action='store_true',
                       help='Apply post-processing rules')
    parser.add_argument('--input', type=str,
                       help='Input text or path to file')
    parser.add_argument('--batch', type=str,
                       help='Path to JSONL file with texts to process')
    parser.add_argument('--output', type=str,
                       help='Output file for results')
    
    args = parser.parse_args()
    
    # Initialize pipeline
    pipeline = ProductionNERPipeline(
        ner_model_path=args.ner_model,
        classifier_model_path=args.classifier_model,
        use_postprocessing=args.use_postprocessing
    )
    
    # Process input
    if args.input:
        print("="*60)
        print("Processing single text:")
        print("="*60)
        print(f"Input: {args.input}\n")
        
        results = pipeline.process_text(args.input)
        
        print("Results:")
        for i, result in enumerate(results, 1):
            print(f"\n{i}. Person: {result['person']}")
            print(f"   Role: {result['role']}")
            print(f"   Confidence: {result['confidence']:.4f}")
            print(f"   Position: {result['start']}-{result['end']}")
        
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"\nResults saved to: {args.output}")
    
    elif args.batch:
        print("="*60)
        print("Processing batch:")
        print("="*60)
        print(f"Input file: {args.batch}\n")
        
        with open(args.batch, 'r', encoding='utf-8') as f:
            texts = [json.loads(line).get('text', '') for line in f]
        
        results = pipeline.process_batch(texts)
        
        print(f"Processed {len(results)} texts")
        print(f"Total entities found: {sum(len(r) for r in results)}")
        
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                for result in results:
                    f.write(json.dumps(result, ensure_ascii=False) + '\n')
            print(f"Results saved to: {args.output}")
    
    else:
        print("No input provided. Use --input for single text or --batch for multiple texts")
        print("\nExample usage:")
        print("  python inference_pipeline.py --input 'הספר נכתב על ידי משה בן יעקב'")
        print("  python inference_pipeline.py --batch test_data.jsonl --output results.jsonl")


if __name__ == "__main__":
    main()

