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

