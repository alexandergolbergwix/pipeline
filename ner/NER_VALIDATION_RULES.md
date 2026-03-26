# NER Validation Rules for HalleluBERT Fine-Tuning

## Document Purpose
This document defines the entity annotation rules and dataset validation requirements for fine-tuning the HalleluBERT model for Named Entity Recognition (NER) on Hebrew manuscript metadata from the National Library of Israel.

---

## Part 1: Entity Annotation Rules

### 1.1 Entity Type Definitions

Based on the Hebrew Manuscripts Ontology for Linked Open Data (LOD) representation:

#### **PERSON**
- **Definition**: Names of individuals, authors, scribes, scholars, and historical figures
- **MARC Sources**: `100$a`, `700$a`, `600$a`
- **Format**: Hebrew names (e.g., "משה בן מימון", "רש״י", "יהודה הלוי")
- **Rules**:
  - Full names (e.g., "אברהם בן עזרא")
  - Names with patronyms (e.g., "יצחק בן שמואל")
  - Names with titles (e.g., "רבי עקיבא", "הרמב״ם")
  - Single names when referring to known individuals
- **Examples**:
  - ✅ "יוסף בן כספי"
  - ✅ "רבנו יונה"
  - ✅ "משה חזן"
  - ❌ "הכהן" (role, not name - use ROLE instead)

#### **PLACE**
- **Definition**: Geographic locations, cities, regions, countries
- **MARC Sources**: `751$a`, `260$a`, `264$a`
- **Format**: Hebrew place names
- **Rules**:
  - Cities (e.g., "ירושלם", "צפת", "בבל")
  - Regions (e.g., "ספרד", "אשכנז", "צרפת")
  - Countries and kingdoms
  - Historical place names
- **Examples**:
  - ✅ "ארץ ישראל"
  - ✅ "מצרים"
  - ✅ "אנגליה"
  - ❌ "בית המקדש" (if it's a work title - use WORK)

#### **DATE**
- **Definition**: Temporal expressions including years, centuries, and date ranges
- **MARC Sources**: `260$c`, `264$c`, `700$d`, `008` (positions 7-10, 11-14)
- **Format**: Hebrew or Gregorian dates
- **Rules**:
  - Hebrew calendar dates (e.g., "תש״ל", "תרפ״ט")
  - Gregorian years (e.g., "1886", "2023")
  - Century references (e.g., "המאה ה-12", "القرن العاشر")
  - Date ranges (e.g., "1200-1300")
  - Partial dates (e.g., "באוקטובר 1886")
- **Examples**:
  - ✅ "16 באוקטובר 1886"
  - ✅ "תשל״ד"
  - ✅ "המאה ה-10"
  - ✅ "1200-1300"
  - ❌ "עתיק" (vague temporal reference)

#### **WORK**
- **Definition**: Titles of books, manuscripts, religious texts, prayers, and compositions
- **MARC Sources**: `245$a`, `246$a`, `730$a`, `740$a`
- **Format**: Hebrew work titles
- **Rules**:
  - Religious texts (e.g., "תלמוד", "משנה תורה")
  - Prayer book sections (e.g., "סדר תפילות", "מחזור")
  - Manuscripts (e.g., "כתר מלכות")
  - Biblical books
  - Commentaries
- **Examples**:
  - ✅ "משנה תורה"
  - ✅ "תלמוד בבלי"
  - ✅ "סידור רש״י"
  - ✅ "שיר היחוד"
  - ❌ "ספר" (generic term)

#### **ROLE**
- **Definition**: Professional roles, titles, or positions
- **MARC Sources**: `700$e`, contextual mentions
- **Format**: Hebrew role descriptors
- **Rules**:
  - Religious roles (e.g., "רב", "חזן", "כהן")
  - Professional titles (e.g., "סופר", "מעתיק")
  - Academic positions (e.g., "ראש ישיבה")
- **Examples**:
  - ✅ "רב"
  - ✅ "מעתיק"
  - ✅ "חזן"
  - ✅ "הפרנס"
  - ❌ "אדם" (not a role)

#### **ORGANIZATION**
- **Definition**: Institutions, communities, religious groups, schools of thought
- **MARC Sources**: `610$a`, `710$a`, contextual mentions
- **Format**: Hebrew organization names
- **Rules**:
  - Religious communities (e.g., "בית ישראל")
  - Schools of thought (e.g., "מנהג אשכנז")
  - Institutions (e.g., "ישיבה")
  - Congregations
- **Examples**:
  - ✅ "בית ישראל"
  - ✅ "מנהג צרפת"
  - ✅ "קהל"
  - ❌ "עם" (too generic)

### 1.2 Entity Annotation Format

#### Character-Level Span Annotation
- **start_pos**: Character position where entity begins (0-indexed)
- **end_pos**: Character position where entity ends (exclusive)
- **entity_text**: Exact text as it appears in the source
- **entity_type**: One of: PERSON, PLACE, DATE, WORK, ROLE, ORGANIZATION

#### Example Annotation
```json
{
  "record_id": "record_990001964450205171",
  "marc_001": "990001964450205171",
  "text": "רבינו משה בן מימון חיבר את משנה תורה בשנת תש״ל בצפת",
  "entity_text": "משה בן מימון",
  "entity_type": "PERSON",
  "start_pos": 7,
  "end_pos": 19,
  "source_field": "100$a",
  "validation_score": 0.95,
  "confidence": "validated"
}
```

### 1.3 Entity Extraction Rules

1. **Fuzzy Matching Threshold**: 60% similarity (using Levenshtein distance)
   - Accounts for spelling variations: "משה בן יצחק" ≈ "משה יצחק"
   - Allows for abbreviation matching: "רש״י" ≈ "רבי שלמה יצחקי"

2. **Validation Requirement**: Entity MUST appear in BOTH:
   - Structured MARC field (e.g., `100$a`, `700$a`)
   - Context/narrative field (concatenation of `957$a`, `500$a`, `561$a`)

3. **Multi-Value Fields**: Split by pipe delimiter `|`

4. **Text Cleaning**:
   - Remove extra whitespace
   - Strip quotes and special characters
   - Normalize Hebrew punctuation

---

## Part 2: Dataset Validation Rules for HalleluBERT Fine-Tuning

### 2.1 Dataset Structure Requirements

#### ✅ **REQUIRED COLUMNS**
```csv
record_id,marc_001,text,entity_text,entity_type,start_pos,end_pos,source_field,validation_score,confidence
```

| Column | Type | Description | Validation Rule |
|--------|------|-------------|----------------|
| `record_id` | string | Unique identifier from MARC 001 | NOT NULL, UNIQUE per record |
| `marc_001` | string | MARC control number | NOT NULL, matches MARC 001 field |
| `text` | string | Context text (narrative notes) | NOT NULL, length > 10 chars |
| `entity_text` | string | Extracted entity | NOT NULL, length > 0 |
| `entity_type` | enum | Entity category | IN (PERSON, PLACE, DATE, WORK, ROLE, ORGANIZATION) |
| `start_pos` | integer | Start character position | >= 0, < len(text) |
| `end_pos` | integer | End character position | > start_pos, <= len(text) |
| `source_field` | string | MARC field source | NOT NULL |
| `validation_score` | float | Fuzzy match score | >= 0.6, <= 1.0 |
| `confidence` | string | Validation status | IN (validated, synthetic, manual) |

### 2.2 Data Quality Rules

#### Rule 1: **Span Accuracy**
```python
text[start_pos:end_pos] == entity_text
```
- The substring from `start_pos` to `end_pos` MUST exactly match `entity_text`
- ❌ FAIL if positions are incorrect
- ✅ PASS if exact match

#### Rule 2: **No Overlapping Spans**
```python
for entity1, entity2 in all_entity_pairs:
    assert not (entity1.start_pos < entity2.end_pos and 
                entity2.start_pos < entity1.end_pos)
```
- Two entities in the same text CANNOT overlap
- Exception: Nested entities (not currently supported)

#### Rule 3: **Entity Validation Score**
```python
validation_score >= 0.6
```
- All validated entities MUST have >= 60% fuzzy match
- Ensures entity appears in BOTH structured field AND context

#### Rule 4: **Minimum Entities Per Record**
```python
# No minimum, but records with 0 validated entities are EXCLUDED
validated_entities_count > 0
```
- Records with no validated entities are removed from training
- Prioritize records with MORE validated entities

#### Rule 5: **Text Language**
- Text MUST be primarily in **Hebrew**
- May contain:
  - Hebrew dates (e.g., "תש״ל")
  - Gregorian years (e.g., "1886")
  - Latin transliterations in MARC fields
  - Aramaic liturgical terms

#### Rule 6: **Entity Type Distribution**
- Balanced representation of all 6 entity types
- Target distribution (after balancing):
  ```
  PERSON:       ~25%
  WORK:         ~20%
  PLACE:        ~15%
  DATE:         ~15%
  ROLE:         ~15%
  ORGANIZATION: ~10%
  ```

### 2.3 Dataset Size Requirements

| Metric | Requirement | Actual |
|--------|-------------|--------|
| Total Records | >= 1,000 unique | ✅ 10,000 |
| Total Annotations | >= 5,000 | ✅ 79,419+ |
| Validated Entities | 100% | ✅ 100% |
| Synthetic Examples | <= 50% | ✅ ~47% |
| Train/Val/Test Split | 80/10/10 | ✅ 80/10/10 |

### 2.4 BIO Format Requirements (HalleluBERT)

After CSV validation, data is converted to BIO token-level format:

#### Token-Level Labels
```
B-PERSON  : Beginning of person entity
I-PERSON  : Inside person entity
B-PLACE   : Beginning of place entity
I-PLACE   : Inside place entity
B-DATE    : Beginning of date entity
I-DATE    : Inside date entity
B-WORK    : Beginning of work entity
I-WORK    : Inside work entity
B-ROLE    : Beginning of role entity
I-ROLE    : Inside role entity
B-ORG     : Beginning of organization entity
I-ORG     : Inside organization entity
O         : Outside any entity
```

#### Example BIO Sequence
```
Text:   רבינו  משה  בן  מימון  חיבר  את  משנה  תורה  בצפת
Labels: B-PERSON I-PERSON I-PERSON I-PERSON O O B-WORK I-WORK B-PLACE
```

### 2.5 Model-Specific Constraints

#### HalleluBERT_base Requirements
1. **Maximum Sequence Length**: 512 tokens
   - Longer texts are truncated
   - Truncation may lose entities at text end

2. **Tokenization**: Uses HalleluBERT tokenizer
   - Hebrew-specific subword tokenization
   - Maintains character-to-token alignment

3. **Special Tokens**:
   - `<s>`: Start of sequence
   - `</s>`: End of sequence
   - Labels for special tokens set to `-100` (ignored in loss)

4. **Batch Size**: Optimized for available memory
   - Training: 12 samples per batch
   - Evaluation: 24 samples per batch

---

## Part 3: Validation Checklist

### Pre-Training Validation

- [ ] All required columns present
- [ ] No NULL values in required fields
- [ ] All `entity_type` values are valid (6 types)
- [ ] All `validation_score` >= 0.6
- [ ] All `start_pos` < `end_pos`
- [ ] All spans match: `text[start:end] == entity_text`
- [ ] No overlapping entities within same record
- [ ] >= 1,000 unique records (by `record_id`)
- [ ] Entity type distribution is balanced
- [ ] Hebrew text encoding is UTF-8
- [ ] MARC 001 field matches `record_id`

### Post-Processing Validation

- [ ] BIO labels assigned to all tokens
- [ ] Special tokens labeled as `-100`
- [ ] Sequence length <= 512 tokens
- [ ] Train/val/test splits are disjoint
- [ ] No data leakage between splits
- [ ] All entity boundaries aligned with token boundaries

### Model Training Validation

- [ ] LoRA configuration applied (memory efficiency)
- [ ] Class weights computed for imbalanced classes
- [ ] Gradient checkpointing enabled
- [ ] Early stopping configured (patience=5)
- [ ] Evaluation metrics: Precision, Recall, F1 per entity type
- [ ] Model saves best checkpoint based on F1 score

---

## Part 4: Common Validation Errors and Fixes

### Error 1: Span Mismatch
**Problem**: `text[start:end] != entity_text`
```python
# Fix: Recalculate positions
start_pos = text.find(entity_text)
end_pos = start_pos + len(entity_text)
```

### Error 2: Overlapping Entities
**Problem**: Two entities overlap in same text
```python
# Fix: Keep entity with higher validation_score
if entity1.validation_score > entity2.validation_score:
    keep entity1
```

### Error 3: Invalid Entity Type
**Problem**: `entity_type = "LOCATION"` (should be PLACE)
```python
# Fix: Map to valid types
type_mapping = {
    "LOCATION": "PLACE",
    "LOC": "PLACE",
    "PER": "PERSON",
    "ORG": "ORGANIZATION"
}
```

### Error 4: Low Validation Score
**Problem**: `validation_score = 0.45` (< 0.6 threshold)
```python
# Fix: Exclude from training or manually validate
if validation_score < 0.6:
    exclude_from_dataset()
```

### Error 5: Missing Context
**Problem**: `text = None` or empty
```python
# Fix: Exclude record entirely
if not text or len(text.strip()) < 10:
    exclude_record()
```

---

## Part 5: Quality Assurance Metrics

### Annotation Quality Metrics

| Metric | Formula | Target | Actual |
|--------|---------|--------|--------|
| **Validation Rate** | (validated_entities / total_entities) | >= 95% | ✅ 100% |
| **Avg Match Score** | mean(validation_score) | >= 0.75 | ✅ 0.82 |
| **Entity Density** | entities / record | >= 5 | ✅ 7.9 |
| **Coverage** | records_with_entities / total_records | >= 90% | ✅ 100% |

### Model Performance Targets

| Entity Type | Precision | Recall | F1 |
|-------------|-----------|--------|-----|
| PERSON | >= 0.85 | >= 0.80 | >= 0.82 |
| PLACE | >= 0.80 | >= 0.75 | >= 0.77 |
| DATE | >= 0.85 | >= 0.80 | >= 0.82 |
| WORK | >= 0.75 | >= 0.70 | >= 0.72 |
| ROLE | >= 0.70 | >= 0.65 | >= 0.67 |
| ORGANIZATION | >= 0.70 | >= 0.65 | >= 0.67 |
| **Overall** | **>= 0.80** | **>= 0.75** | **>= 0.77** |

---

## Part 6: References

### Data Sources
- **National Library of Israel**: MARC21 Bibliographic Format
- **Hebrew Manuscripts Ontology**: Custom LOD representation
- **MARC Fields**: See section 1.1 for field mapping

### Tools and Models
- **HalleluBERT_base**: 110M parameter Hebrew RoBERTa model
- **Levenshtein Distance**: Fuzzy string matching (threshold: 60%)
- **LoRA**: Low-Rank Adaptation for efficient fine-tuning

### Related Documents
- `TRAINING_STATUS.md`: Training progress and results
- `IMPROVEMENT_SUMMARY.md`: Dataset enhancements and model improvements
- `IMPLEMENTATION_COMPLETE.md`: Full implementation report

---

**Document Version**: 1.0  
**Last Updated**: 2025-11-06  
**Author**: NER Training Pipeline  
**Status**: Active

