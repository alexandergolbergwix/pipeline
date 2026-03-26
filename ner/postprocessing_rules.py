"""
Error-Driven Post-Processing Rules for NER and Classification
Implements three key improvements based on error analysis:
1. Patronymic Completion: Fix partial names missing patronymics
2. Latin Name Handling: Better processing of mixed Hebrew/Latin text
3. Role Disambiguation: Context-based rules for OWNER vs AUTHOR confusion

Expected improvement: +1-2% combined (recall and accuracy)
"""

import re
from typing import List, Tuple, Dict
import unicodedata


class PostProcessingRules:
    """Post-processing rules to fix common errors"""
    
    def __init__(self):
        # Patronymic markers
        self.patronymic_markers = ['בן', 'בת', 'אבן']
        
        # Role disambiguation keywords
        self.owner_keywords = [
            'בעלים של', 'קנה', 'ירש', 'רכש', 'השיג',
            'נקנה', 'נרכש', 'ברשותו', 'קניינו'
        ]
        self.author_keywords = [
            'מחבר', 'חיבר', 'כתב', 'חובר', 'מחברת',
            'כותב', 'מאת', 'מעשה', 'יצירת'
        ]
        self.transcriber_keywords = [
            'מעתיק', 'העתיק', 'העתקה', 'העתקת',
            'כתב יד', 'כתיבת', 'נכתב'
        ]
        
    def complete_patronymic(self, text: str, entity_start: int, entity_end: int,
                           tokens: List[str] = None) -> Tuple[int, int]:
        """
        Fix partial names that end with patronymic markers
        
        Args:
            text: Full text
            entity_start: Start character index
            entity_end: End character index
            tokens: Optional list of tokens for token-based expansion
            
        Returns:
            (new_start, new_end): Updated entity boundaries
        """
        entity_text = text[entity_start:entity_end]
        
        # Check if entity ends with patronymic marker
        ends_with_patronymic = any(
            entity_text.strip().endswith(marker)
            for marker in self.patronymic_markers
        )
        
        if not ends_with_patronymic:
            return entity_start, entity_end
        
        # Expand to include following name
        # Look for next 1-2 Hebrew words after the marker
        remaining_text = text[entity_end:entity_end+50]
        
        # Match Hebrew words (1-2 words)
        hebrew_pattern = r'^[\s]+([\u0590-\u05FF]+)(?:[\s]+([\u0590-\u05FF]+))?'
        match = re.match(hebrew_pattern, remaining_text)
        
        if match:
            # Extend entity to include matched text
            extension = match.group(0).strip()
            new_end = entity_end + len(match.group(0))
            
            print(f"  [Patronymic Completion] Extended: '{entity_text}' → '{text[entity_start:new_end]}'")
            return entity_start, new_end
        
        return entity_start, entity_end
    
    def detect_latin_script(self, text: str) -> List[Tuple[int, int]]:
        """
        Detect Latin script segments in Hebrew text
        
        Args:
            text: Input text
            
        Returns:
            List of (start, end) positions of Latin segments
        """
        latin_segments = []
        current_start = None
        
        for i, char in enumerate(text):
            is_latin = (
                ('A' <= char <= 'Z') or 
                ('a' <= char <= 'z') or
                char in "'-."
            )
            
            if is_latin and current_start is None:
                current_start = i
            elif not is_latin and current_start is not None:
                if i - current_start > 2:  # Minimum 3 characters
                    latin_segments.append((current_start, i))
                current_start = None
        
        # Handle case where text ends with Latin
        if current_start is not None:
            latin_segments.append((current_start, len(text)))
        
        return latin_segments
    
    def handle_latin_names(self, text: str, entities: List[Dict]) -> List[Dict]:
        """
        Improve handling of Latin names (e.g., censor names)
        
        Args:
            text: Full text
            entities: List of detected entities with 'start', 'end', 'text', 'label'
            
        Returns:
            Updated entities list with corrected Latin name boundaries
        """
        latin_segments = self.detect_latin_script(text)
        
        if not latin_segments:
            return entities
        
        updated_entities = []
        
        for entity in entities:
            entity_start = entity['start']
            entity_end = entity['end']
            
            # Check if entity overlaps with Latin segment
            for lat_start, lat_end in latin_segments:
                overlap = (
                    (entity_start <= lat_start < entity_end) or
                    (lat_start <= entity_start < lat_end)
                )
                
                if overlap:
                    # Expand entity to cover full Latin segment
                    new_start = min(entity_start, lat_start)
                    new_end = max(entity_end, lat_end)
                    
                    # Trim whitespace
                    while new_start < new_end and text[new_start].isspace():
                        new_start += 1
                    while new_end > new_start and text[new_end-1].isspace():
                        new_end -= 1
                    
                    entity = {
                        'start': new_start,
                        'end': new_end,
                        'text': text[new_start:new_end],
                        'label': entity['label']
                    }
                    print(f"  [Latin Name] Adjusted: '{text[new_start:new_end]}'")
                    break
            
            updated_entities.append(entity)
        
        return updated_entities
    
    def disambiguate_role(self, text: str, entity_text: str, 
                         predicted_role: str, confidence: float = 0.0) -> str:
        """
        Apply context-based rules to disambiguate roles
        Particularly useful for OWNER vs AUTHOR confusion
        
        Args:
            text: Full context text
            entity_text: The person name
            predicted_role: Model's prediction
            confidence: Model's confidence (if available)
            
        Returns:
            Final role (possibly overridden by rules)
        """
        text_lower = text.lower()
        
        # Count keyword matches
        owner_score = sum(1 for kw in self.owner_keywords if kw in text_lower)
        author_score = sum(1 for kw in self.author_keywords if kw in text_lower)
        transcriber_score = sum(1 for kw in self.transcriber_keywords if kw in text_lower)
        
        # Rule 1: Strong owner indicators
        if owner_score > 0 and 'בעלים' in text_lower:
            # Check for "בעלים של" which is strong ownership signal
            if 'בעלים של' in text_lower:
                if predicted_role != 'OWNER':
                    print(f"  [Role Disambiguation] {predicted_role} → OWNER (strong ownership signal)")
                return 'OWNER'
        
        # Rule 2: Temporal markers indicate ownership
        temporal_markers = ['קנה', 'ירש', 'נקנה', 'רכש']
        if any(marker in text_lower for marker in temporal_markers):
            if predicted_role in ['AUTHOR', 'TRANSCRIBER']:
                print(f"  [Role Disambiguation] {predicted_role} → OWNER (temporal marker)")
                return 'OWNER'
        
        # Rule 3: Author near ownership mention
        if 'מחבר' in text_lower and 'בעלים' in text_lower:
            # If both appear, context determines which is closer to entity
            entity_pos = text_lower.find(entity_text.lower())
            if entity_pos != -1:
                author_dist = abs(text_lower.find('מחבר') - entity_pos)
                owner_dist = abs(text_lower.find('בעלים') - entity_pos)
                
                if author_dist < owner_dist and predicted_role == 'OWNER':
                    print(f"  [Role Disambiguation] OWNER → AUTHOR (closer to author keyword)")
                    return 'AUTHOR'
                elif owner_dist < author_dist and predicted_role == 'AUTHOR':
                    print(f"  [Role Disambiguation] AUTHOR → OWNER (closer to owner keyword)")
                    return 'OWNER'
        
        # Rule 4: High confidence in model, but contradicting keywords
        if confidence > 0.8:
            return predicted_role
        
        # Rule 5: Vote by keyword counts (only if low confidence)
        if confidence < 0.5:
            max_score = max(owner_score, author_score, transcriber_score)
            if max_score > 0:
                if owner_score == max_score:
                    new_role = 'OWNER'
                elif author_score == max_score:
                    new_role = 'AUTHOR'
                else:
                    new_role = 'TRANSCRIBER'
                
                if new_role != predicted_role:
                    print(f"  [Role Disambiguation] {predicted_role} → {new_role} (keyword voting)")
                    return new_role
        
        return predicted_role
    
    def apply_all_rules(self, text: str, entities: List[Dict], 
                       roles: List[str] = None, 
                       confidences: List[float] = None) -> Tuple[List[Dict], List[str]]:
        """
        Apply all post-processing rules
        
        Args:
            text: Full text
            entities: List of entities with 'start', 'end', 'text', 'label'
            roles: Optional list of predicted roles
            confidences: Optional list of confidence scores
            
        Returns:
            (updated_entities, updated_roles)
        """
        print("\n" + "="*60)
        print("Applying Post-Processing Rules")
        print("="*60)
        
        # Step 1: Complete patronymics
        print("\n[1] Patronymic Completion:")
        updated_entities = []
        for i, entity in enumerate(entities):
            new_start, new_end = self.complete_patronymic(
                text, entity['start'], entity['end']
            )
            updated_entity = {
                'start': new_start,
                'end': new_end,
                'text': text[new_start:new_end],
                'label': entity['label']
            }
            updated_entities.append(updated_entity)
        
        # Step 2: Handle Latin names
        print("\n[2] Latin Name Handling:")
        updated_entities = self.handle_latin_names(text, updated_entities)
        
        # Step 3: Disambiguate roles
        print("\n[3] Role Disambiguation:")
        updated_roles = roles if roles else [None] * len(updated_entities)
        if roles:
            for i, (entity, role) in enumerate(zip(updated_entities, roles)):
                conf = confidences[i] if confidences else 0.0
                updated_roles[i] = self.disambiguate_role(
                    text, entity['text'], role, conf
                )
        
        print("\n" + "="*60)
        print(f"Processed {len(updated_entities)} entities")
        print("="*60 + "\n")
        
        return updated_entities, updated_roles


def demo_postprocessing():
    """Demonstrate post-processing on example cases"""
    
    processor = PostProcessingRules()
    
    # Example 1: Patronymic completion
    print("\n" + "="*80)
    print("EXAMPLE 1: Patronymic Completion")
    print("="*80)
    text1 = "הספר נכתב על ידי משה בן יעקב הכהן בשנת תר\"ח"
    entities1 = [
        {'start': 23, 'end': 30, 'text': 'משה בן', 'label': 'PERSON'}
    ]
    
    updated_ent1, _ = processor.apply_all_rules(text1, entities1)
    print(f"Original: {entities1[0]['text']}")
    print(f"Updated:  {updated_ent1[0]['text']}")
    
    # Example 2: Latin name
    print("\n" + "="*80)
    print("EXAMPLE 2: Latin Name Handling")
    print("="*80)
    text2 = "הספר עבר דרך הצנזור Luigi Rossini בשנת 1750"
    entities2 = [
        {'start': 26, 'end': 31, 'text': 'Luigi', 'label': 'PERSON'}
    ]
    
    updated_ent2, _ = processor.apply_all_rules(text2, entities2)
    print(f"Original: {entities2[0]['text']}")
    print(f"Updated:  {updated_ent2[0]['text']}")
    
    # Example 3: Role disambiguation
    print("\n" + "="*80)
    print("EXAMPLE 3: Role Disambiguation (OWNER vs AUTHOR)")
    print("="*80)
    text3 = "הספר הזה שייך לבעלים של דוד בן שלמה שקנה אותו בשנת תק\"ח"
    entities3 = [
        {'start': 27, 'end': 40, 'text': 'דוד בן שלמה', 'label': 'PERSON'}
    ]
    roles3 = ['AUTHOR']  # Wrong prediction
    
    _, updated_roles3 = processor.apply_all_rules(text3, entities3, roles3)
    print(f"Original role: {roles3[0]}")
    print(f"Updated role:  {updated_roles3[0]}")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("POST-PROCESSING RULES DEMONSTRATION")
    print("="*80)
    demo_postprocessing()

