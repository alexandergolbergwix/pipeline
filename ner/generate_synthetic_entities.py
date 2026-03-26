"""
Script to generate synthetic examples for minority entity types.
Focuses on DATE, PLACE, and ORGANIZATION to balance the dataset.
"""

import pandas as pd
import random
import json

# Set random seed for reproducibility
random.seed(42)

print("="*80)
print("GENERATING SYNTHETIC ENTITY EXAMPLES")
print("="*80)

# Hebrew date patterns
date_patterns = [
    # Century patterns
    "במאה ה-{}",
    "בתחילת המאה ה-{}",
    "בסוף המאה ה-{}",
    "באמצע המאה ה-{}",
    "בתקופת המאה ה-{}",
    
    # Year patterns
    "בשנת {}",
    "בשנת ה-{}",
    "שנת {}",
    "משנת {}",
    "עד שנת {}",
    "בין השנים {} ל-{}",
    
    # General time periods
    "בימי קדם",
    "בתקופה העתיקה",
    "בימי הביניים",
    "בתקופה המודרנית",
    "בזמנו של",
    "בימי",
    "בתקופת",
    
    # Hebrew calendar dates
    'בחודש {} שנת {}',
    'ביום {} בחודש {}',
    'בראש השנה',
    'בחנוכה',
    'בפסח',
    'בסוכות',
    'ביום הכפורים',
]

# Century numbers in Hebrew
centuries = ['ראשונה', 'שנייה', 'שלישית', 'רביעית', 'חמישית', 'שישית', 
             'שביעית', 'שמינית', 'תשיעית', 'עשירית', 'אחת עשרה', 'שתים עשרה',
             'שלוש עשרה', 'ארבע עשרה', 'חמש עשרה', 'שש עשרה', 'שבע עשרה',
             'שמונה עשרה', 'תשע עשרה', 'עשרים', 'עשרים ואחת']

# Hebrew years
years_hebrew = ['תש"א', 'תש"ב', 'תש"ג', 'תש"ד', 'תש"ה', 'תש"ו', 'תש"ז', 'תש"ח',
                'תש"ט', 'תש"י', 'תשי"א', 'תשי"ב', 'תשי"ג', 'תשי"ד', 'תשט"ו',
                'תר"ם', 'תרל"ג', 'תרמ"ב', 'תרנ"ח', 'תרס"ג', 'תרע"ו', 'תש"ך']

# Gregorian years
years_gregorian = list(range(1000, 2024))

# Hebrew months
hebrew_months = ['ניסן', 'אייר', 'סיון', 'תמוז', 'אב', 'אלול', 
                 'תשרי', 'חשון', 'כסלו', 'טבת', 'שבט', 'אדר']

# Hebrew place names (cities, regions, countries)
places = [
    # Israel cities and regions
    'ירושלים', 'תל אביב', 'חיפה', 'צפת', 'טבריה', 'באר שבע', 'עכו', 'יפו',
    'הגליל', 'השומרון', 'היהודה', 'הנגב', 'עמק יזרעאל', 'הכרמל',
    
    # Middle East
    'בגדד', 'קהיר', 'דמשק', 'חלב', 'בירות', 'אלכסנדריה', 'בצרה', 'מוסול',
    'שומרון', 'בבל', 'פרס', 'מצרים', 'סוריה', 'לבנון', 'עירק',
    
    # North Africa
    'תוניס', 'מרוקו', 'פאס', 'מכנאס', 'מראכש', 'טריפולי', 'טנג\'יר',
    'אלג\'יר', 'קיירואן', 'ג\'רבה',
    
    # Europe
    'ספרד', 'איטליה', 'צרפת', 'גרמניה', 'אנגליה', 'פולין', 'רוסיה',
    'רומא', 'ונציה', 'פירנצה', 'פראג', 'אמסטרדם', 'ורשה', 'וילנה',
    'קרקוב', 'לובלין', 'פוזנן', 'ברלין', 'לונדון', 'פריז',
    
    # Yemen and East
    'תימן', 'צנעא', 'אדן', 'חצרמות', 'הודו', 'כוכין', 'שבא'
]

# Place patterns
place_patterns = [
    'ב{}',
    'מ{}',
    'ל{}',
    'ע\"י {}',
    'בעיר {}',
    'בארץ {}',
    'בקהילת {}',
    'בישיבת {}',
]

# Hebrew organization names
organizations = [
    # Religious institutions
    'ישיבת {}',
    'בית המדרש ב{}',
    'בית הכנסת ב{}',
    'קהילת {}',
    'הקהילה היהודית ב{}',
    
    # Academic institutions
    'אקדמיה ל{}',
    'בית הספר ל{}',
    
    # Organizations
    'חברת {}',
    'אגודת {}',
    'עדת {}',
    'כנסת {}',
    'מועצת {}',
]

org_topics = [
    'חכמי ספרד', 'רבני אשכנז', 'חכמי מזרח', 'חכמי תימן',
    'תורה', 'תלמוד', 'הלכה', 'קבלה', 'פילוסופיה', 'מוסר',
    'כתבי היד העבריים', 'המחקר התלמודי', 'לימוד התלמוד'
]

# Context templates for synthetic examples
context_templates = [
    'כתב יד זה נכתב {date} ב{place}.',
    'מסמך זה מתוארך ל{date} ומקורו מ{place}.',
    'כתוב על ידי חכם מ{place} {date}.',
    'נמצא בארכיון {org}, מתוארך ל{date}.',
    '{org} שמרה על כתב יד זה מ{date}.',
    'עותק זה הועתק {date} בעיר {place}.',
    'כתב היד נמצא ב{place} ומתוארך ל{date}.',
    'נכתב על ידי סופר מ{place} {date} עבור {org}.',
]

def generate_date_examples(n=5000):
    """Generate synthetic DATE examples"""
    examples = []
    
    for _ in range(n):
        pattern = random.choice(date_patterns)
        
        # Count placeholders in pattern
        placeholder_count = pattern.count('{}')
        
        if placeholder_count == 0:
            date_text = pattern
        elif placeholder_count == 2:
            # Two placeholders (e.g., "בין השנים {} ל-{}" or 'בחודש {} שנת {}')
            if 'השנים' in pattern:
                if random.random() < 0.5:
                    year1 = random.choice(years_hebrew)
                    year2 = random.choice(years_hebrew)
                else:
                    year1 = str(random.choice(years_gregorian))
                    year2 = str(random.choice(years_gregorian))
                date_text = pattern.format(year1, year2)
            elif 'חודש' in pattern:
                month = random.choice(hebrew_months)
                year = random.choice(years_hebrew)
                date_text = pattern.format(month, year)
            else:
                # Default: use two years
                year1 = random.choice(years_hebrew)
                year2 = random.choice(years_hebrew)
                date_text = pattern.format(year1, year2)
        else:
            # One placeholder
            if 'שנת' in pattern or 'שנה' in pattern:
                if random.random() < 0.5:
                    year = random.choice(years_hebrew)
                else:
                    year = str(random.choice(years_gregorian))
                date_text = pattern.format(year)
            elif 'מאה' in pattern:
                century = random.choice(centuries)
                date_text = pattern.format(century)
            elif 'חודש' in pattern:
                month = random.choice(hebrew_months)
                date_text = pattern.format(month)
            else:
                date_text = pattern.format(random.choice(years_hebrew))
        
        examples.append(date_text)
    
    return examples

def generate_place_examples(n=5000):
    """Generate synthetic PLACE examples"""
    examples = []
    
    for _ in range(n):
        place = random.choice(places)
        pattern = random.choice(place_patterns)
        
        if '{}' in pattern:
            place_text = pattern.format(place)
        else:
            place_text = place
        
        examples.append(place_text)
    
    return examples

def generate_org_examples(n=2000):
    """Generate synthetic ORGANIZATION examples"""
    examples = []
    
    for _ in range(n):
        if random.random() < 0.7:
            # Use template with place
            place = random.choice(places)
            template = random.choice(organizations)
            org_text = template.format(place)
        else:
            # Use topic-based org
            topic = random.choice(org_topics)
            template = random.choice([t for t in organizations if '{}' in t])
            org_text = template.format(topic)
        
        examples.append(org_text)
    
    return examples

def create_synthetic_records(date_examples, place_examples, org_examples):
    """Create full synthetic records with context"""
    records = []
    record_id = 900000  # Start from high number to avoid conflicts
    
    # Create records with combinations of entities
    max_records = max(len(date_examples), len(place_examples), len(org_examples))
    
    for i in range(max_records):
        date = date_examples[i % len(date_examples)]
        place = place_examples[i % len(place_examples)]
        org = org_examples[i % len(org_examples)]
        
        # Select template
        template = random.choice(context_templates)
        
        # Fill template with available entities
        if '{date}' in template and '{place}' in template and '{org}' in template:
            context = template.format(date=date, place=place, org=org)
            entities = [
                {'text': date, 'type': 'DATE'},
                {'text': place, 'type': 'PLACE'},
                {'text': org, 'type': 'ORGANIZATION'}
            ]
        elif '{date}' in template and '{place}' in template:
            context = template.format(date=date, place=place)
            entities = [
                {'text': date, 'type': 'DATE'},
                {'text': place, 'type': 'PLACE'}
            ]
        elif '{date}' in template and '{org}' in template:
            context = template.format(date=date, org=org)
            entities = [
                {'text': date, 'type': 'DATE'},
                {'text': org, 'type': 'ORGANIZATION'}
            ]
        elif '{place}' in template and '{org}' in template:
            context = template.format(place=place, org=org)
            entities = [
                {'text': place, 'type': 'PLACE'},
                {'text': org, 'type': 'ORGANIZATION'}
            ]
        else:
            continue
        
        # Create annotation entries for each entity
        for entity in entities:
            start_pos = context.find(entity['text'])
            if start_pos == -1:
                continue
            end_pos = start_pos + len(entity['text'])
            
            records.append({
                'record_id': f'synthetic_{record_id}',
                'marc_001': f'SYN{record_id:06d}',
                'context': context,
                'entity_text': entity['text'],
                'entity_type': entity['type'],
                'start_pos': start_pos,
                'end_pos': end_pos,
                'validation_score': 1.0,  # Synthetic data is perfect
                'is_synthetic': True
            })
        
        record_id += 1
        
        # Stop if we have enough records
        if record_id >= 900000 + 15000:
            break
    
    return records

# Generate examples
print("\nGenerating DATE examples...")
date_examples = generate_date_examples(5000)
print(f"  Generated {len(date_examples):,} DATE examples")

print("\nGenerating PLACE examples...")
place_examples = generate_place_examples(5000)
print(f"  Generated {len(place_examples):,} PLACE examples")

print("\nGenerating ORGANIZATION examples...")
org_examples = generate_org_examples(2000)
print(f"  Generated {len(org_examples):,} ORGANIZATION examples")

print("\nCreating synthetic records with context...")
synthetic_records = create_synthetic_records(date_examples, place_examples, org_examples)
print(f"  Created {len(synthetic_records):,} synthetic annotations")

# Convert to DataFrame
df_synthetic = pd.DataFrame(synthetic_records)

print("\n" + "="*80)
print("SYNTHETIC DATA STATISTICS")
print("="*80)
print(f"Total synthetic annotations: {len(df_synthetic):,}")
print(f"Unique synthetic records: {df_synthetic['record_id'].nunique():,}")
print("\nAnnotations by type:")
for entity_type in df_synthetic['entity_type'].unique():
    count = len(df_synthetic[df_synthetic['entity_type'] == entity_type])
    print(f"  {entity_type:15s}: {count:,}")

# Save synthetic data
output_file = 'processed-data/synthetic_annotations.csv'
df_synthetic.to_csv(output_file, index=False)
print(f"\n✓ Synthetic data saved to: {output_file}")

# Show sample
print("\n" + "="*80)
print("SAMPLE SYNTHETIC EXAMPLES (first 5)")
print("="*80)
for i, row in df_synthetic.head(5).iterrows():
    print(f"\n{i+1}. Entity: {row['entity_text']}")
    print(f"   Type: {row['entity_type']}")
    print(f"   Position: {row['start_pos']}-{row['end_pos']}")
    print(f"   Context: {row['context'][:100]}...")
