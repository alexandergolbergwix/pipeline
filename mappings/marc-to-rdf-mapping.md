# MARC to RDF Mapping for Hebrew Manuscripts

**Author:** Alexander Goldberg  
**Supervisor:** Prof. Gila Prebor  
**Date:** December 2025  
**Version:** 1.0

---

## Introduction

This document defines the systematic mapping between MARC 21 bibliographic records (as used by the National Library of Israel) and the Hebrew Manuscripts RDF ontology based on LRMoo and CIDOC-CRM.

### Namespaces Used

```turtle
@prefix hm: <http://www.ontology.org.il/HebrewManuscripts/2025-08-19#> .
@prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
@prefix cidoc: <http://www.cidoc-crm.org/cidoc-crm/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
```

---

## Mapping Principles

1. **WEMI Model**: MARC records map primarily to F4_Manifestation_Singleton (the physical manuscript)
2. **Works and Expressions**: Must be derived from content analysis (505, 700)
3. **Events**: Production, ownership transfers represented as CIDOC-CRM events
4. **Controlled Vocabularies**: Use existing instances for types, materials, scripts
5. **External Links**: Preserve NLI identifiers for reconciliation

---

## Core Entity Mappings

### Manuscript (F4_Manifestation_Singleton)

| MARC | Subfield | Property | Notes |
|------|----------|----------|-------|
| 001 | - | hm:external_identifier_nli | Control number |
| 035 | $a | cidoc:P1_is_identified_by | Other identifiers |
| 090/099 | $a | rdfs:label | Call number becomes part of label |

**RDF Pattern:**
```turtle
hm:MS_[identifier] a lrmoo:F4_Manifestation_Singleton ,
                     hm:Codicological_Unit ;
    hm:external_identifier_nli "[001]" ;
    rdfs:label "[090] - [245$a]"@en .
```

---

## Field-by-Field Mapping

### 008 - Fixed-Length Data Elements

| Position | Content | Property | Transformation |
|----------|---------|----------|----------------|
| 00-05 | Date entered | - | Metadata only |
| 06 | Date type | hm:date_attribution_source | See date type codes |
| 07-10 | Date 1 | hm:earliest_possible_date | Parse as integer |
| 11-14 | Date 2 | hm:latest_possible_date | Parse as integer |
| 15-17 | Place | → Production Event place | Code lookup |
| 35-37 | Language | cidoc:P72_has_language | ISO 639-2 lookup |

**Date Type Codes (008/06):**
| Code | Meaning | Mapping |
|------|---------|---------|
| s | Single known date | exact date |
| q | Questionable date | approximate |
| m | Multiple dates | range |
| n | Dates unknown | omit or note |

### 040 - Cataloging Source

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | - | Cataloging agency (metadata) |
| $b | - | Language of cataloging |

### 041 - Language Code

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | cidoc:P72_has_language | Primary language(s) |
| $b | cidoc:P72_has_language | Language of summary |
| $h | - | Original language (for translations) |

**Transformation:**
```turtle
?expression cidoc:P72_has_language hm:[LanguageCode] .
```

**Language Code Mapping:**
| MARC Code | Instance |
|-----------|----------|
| heb | hm:Hebrew |
| ara | hm:Arabic |
| arc | hm:Aramaic |
| jrb | hm:JudeoArabic |
| lad | hm:Ladino |
| yid | hm:Yiddish |
| jpr | hm:JudeoPersian |

### 100 - Main Entry (Personal Name)

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | rdfs:label | Name |
| $d | cidoc:P82a/P82b | Dates |
| $e | → Role | Relator term |
| $0 | See external IDs | Authority control |

**Pattern for Author:**
```turtle
hm:Person_[normalized_name] a cidoc:E21_Person ;
    rdfs:label "[100$a]"@he ;
    cidoc:P82a_begin_of_the_begin [birth] ;
    cidoc:P82b_end_of_the_end [death] .

hm:Work_Creation_Event a lrmoo:F27_Work_Creation ;
    lrmoo:R16_created hm:Work_[title] ;
    cidoc:P14_carried_out_by hm:Person_[normalized_name] .
```

### 110 - Main Entry (Corporate Name)

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | rdfs:label | Organization name |
| $0 | External ID | Authority control |

**Pattern:**
```turtle
hm:Group_[normalized_name] a cidoc:E74_Group ;
    rdfs:label "[110$a]"@he .
```

### 245 - Title Statement

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | hm:has_title | Main title |
| $b | hm:has_title | Subtitle (append) |
| $h | cidoc:P2_has_type | General material designation |
| $n | - | Part number |
| $p | - | Part name |

**Pattern:**
```turtle
hm:Work_[normalized_title] a lrmoo:F1_Work ;
    hm:has_title "[245$a]"@he ;
    rdfs:label "[245$a]"@he .
```

### 246 - Variant Title

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | hm:has_title | Additional title |
| $i | - | Display text |

**Pattern:**
```turtle
hm:Work_[id] hm:has_title "[246$a]"@he .
```

### 260/264 - Production Statement

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | → Production Event place | Place name |
| $c | hm:has_date_of_creation | Date |

**Pattern:**
```turtle
hm:MS_[id]_Production_Event a cidoc:E12_Production ;
    cidoc:P7_took_place_at hm:Place_[normalized_place] ;
    cidoc:P4_has_time-span hm:TimeSpan_[date] ;
    lrmoo:R27_materialized hm:MS_[id] .

hm:Place_[normalized_place] a cidoc:E53_Place ;
    rdfs:label "[260$a]" .

hm:TimeSpan_[date] a cidoc:E52_Time-Span ;
    hm:earliest_possible_date [year1] ;
    hm:latest_possible_date [year2] .
```

### 300 - Physical Description

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | hm:has_number_of_folios | Extent (parse number) |
| $b | → Decoration, Illustrations | Other physical details |
| $c | hm:has_height_mm, hm:has_width_mm | Dimensions (parse) |

**Extent Parsing Rules:**
- "248 leaves" → `hm:has_number_of_folios 248`
- "150 ff." → `hm:has_number_of_folios 150`
- "[5], 200 f." → `hm:has_number_of_folios 205` (with note)

**Dimension Parsing:**
- "280 x 200 mm" → `hm:has_height_mm 280 ; hm:has_width_mm 200`
- "28 cm" → `hm:has_height_mm 280` (convert to mm)

### 340 - Physical Medium

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | hm:has_material | Material base |
| $c | → Binding info | Material applied |

**Material Mapping:**
| MARC Value | Instance |
|------------|----------|
| parchment | hm:Parchment |
| vellum | hm:Parchment |
| paper | hm:Paper |
| papyrus | hm:Papyrus |

### 500 - General Note

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | rdfs:comment | Free text note |

**Special Processing:**
- Script mentions → `hm:has_script_type`
- Condition notes → `hm:P44_has_condition`
- Colophon transcriptions → Create Colophon instance

### 505 - Contents Note

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | → Multiple Works/Expressions | Parse contents |
| $g | hm:has_folio_range | Misc info (often folios) |
| $t | → Work title | Title of component |
| $r | → Person + role | Statement of responsibility |

**Pattern for Contents:**
```turtle
# For each work mentioned:
hm:Work_[title] a lrmoo:F1_Work ;
    rdfs:label "[505$t]"@he .

hm:Expression_[title]_in_MS_[id] a lrmoo:F2_Expression ;
    lrmoo:R3_is_realised_in hm:Work_[title] ;
    hm:has_folio_range "[folio_range]" .

hm:MS_[id] lrmoo:R4_embodies hm:Expression_[title]_in_MS_[id] .
```

### 510 - Citation/References Note

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | → Catalog instance | Citation source |
| $c | - | Location within source |

**Pattern:**
```turtle
hm:MS_[id] cidoc:P70i_is_documented_in hm:Catalog_[normalized_name] .

hm:Catalog_[normalized_name] a lrmoo:F3_Manifestation ;
    rdfs:label "[510$a]" ;
    cidoc:P70_documents hm:MS_[id] .
```

### 520 - Summary

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | rdfs:comment | Summary text |

### 524 - Preferred Citation

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | hm:has_preferred_citation | Citation format |

### 540 - Terms Governing Use

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | hm:has_rights_statement | Use restrictions |

### 546 - Language Note

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | rdfs:comment | Language details |

Additional processing to extract script type from text.

### 561 - Ownership and Custodial History

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | hm:ownership_history | Provenance narrative |

**Pattern for structured provenance:**
```turtle
hm:MS_[id]_Ownership_Event_1 a cidoc:E10_Transfer_of_Custody ;
    cidoc:P16_used_specific_object hm:MS_[id] ;
    cidoc:P28_custody_surrendered_by hm:Person_[from] ;
    cidoc:P29_custody_received_by hm:Person_[to] ;
    cidoc:P4_has_time-span hm:TimeSpan_[date] .
```

### 562 - Copy and Version Identification

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | rdfs:comment | Copy identification |
| $b | hm:variation_note | Version identification |

### 563 - Binding Information

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | → Binding class | Binding description |

**Pattern:**
```turtle
hm:MS_[id] hm:has_binding [
    a hm:Binding ;
    rdfs:comment "[563$a]" ;
    cidoc:P2_has_type hm:[BindingType]
] .
```

### 583 - Action Note

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | → Activity type | Action (digitization, conservation) |
| $c | cidoc:P4_has_time-span | Date of action |
| $k | → Actor | Action agent |

**Pattern for Digitization:**
```turtle
hm:MS_[id]_Digitization a cidoc:E31_Document ;
    cidoc:P16_used_specific_object hm:MS_[id] ;
    cidoc:P4_has_time-span hm:TimeSpan_[date] .
```

### 6XX - Subject Access Fields

#### 600 - Subject Added Entry (Personal Name)
```turtle
hm:MS_[id] cidoc:P129_is_about hm:Person_[subject] .
```

#### 610 - Subject Added Entry (Corporate Name)
```turtle
hm:MS_[id] cidoc:P129_is_about hm:Group_[subject] .
```

#### 650 - Subject Added Entry (Topical Term)
```turtle
hm:Work_[id] cidoc:P2_has_type hm:Subject_[topic] .

hm:Subject_[topic] a hm:SubjectType ;
    rdfs:label "[650$a]"@he ;
    hm:external_uri_nli "[650$0]" .
```

#### 651 - Subject Added Entry (Geographic Name)
```turtle
hm:MS_[id] cidoc:P129_is_about hm:Place_[subject] .
```

### 655 - Genre/Form

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | cidoc:P2_has_type | Genre/form term |
| $2 | - | Source vocabulary |

### 700 - Added Entry (Personal Name)

Same structure as 100, with role from $e or $4:

| Role ($e) | Property |
|-----------|----------|
| author | cidoc:P14_carried_out_by (on Work Creation) |
| scribe | cidoc:P14_carried_out_by (on Production Event) |
| copyist | cidoc:P14_carried_out_by (on Production Event) |
| illuminator | cidoc:P14_carried_out_by (on Decoration Event) |
| commentator | → separate Work with has_commentary_on |
| translator | → Expression with is_translation_of |
| former owner | cidoc:P28_custody_surrendered_by |

### 710 - Added Entry (Corporate Name)

Pattern same as 110, with role processing.

### 752 - Hierarchical Place Name

| Subfield | Property | Notes |
|----------|----------|-------|
| $a | → Place hierarchy | Country |
| $b | → Place hierarchy | State/Province |
| $c | → Place hierarchy | County |
| $d | → Place hierarchy | City |

**Pattern:**
```turtle
hm:Place_[city] a cidoc:E53_Place ;
    rdfs:label "[752$d]" ;
    cidoc:P89_falls_within hm:Place_[country] .
```

### 773 - Host Item Entry

| Subfield | Property | Notes |
|----------|----------|-------|
| $w | → Parent manuscript | Control number |
| $g | hm:has_folio_range | Related parts |

For composite manuscripts:
```turtle
hm:MS_[parent] lrmoo:R5_has_component hm:MS_[component] .
```

### 856 - Electronic Location

| Subfield | Property | Notes |
|----------|----------|-------|
| $u | hm:has_digital_representation_url | URL |
| $y | - | Link text |
| $z | - | Public note |

---

## Colophon Extraction

When 500 notes contain colophon transcriptions:

```turtle
hm:MS_[id]_Colophon a hm:Colophon ;
    hm:colophon_text "[transcription]" ;
    hm:mentions_scribe hm:Person_[scribe] ;
    hm:mentions_date hm:TimeSpan_[date] ;
    hm:mentions_place hm:Place_[place] .

hm:MS_[id] hm:has_colophon hm:MS_[id]_Colophon .
```

---

## Script Type Identification

Extract from 500, 546, or specialized fields:

| Text Pattern | Script Type Instance |
|--------------|---------------------|
| "Ashkenazic", "אשכנזי" | hm:AshkenaziScript |
| "Sephardic", "ספרדי" | hm:SepharadicScript |
| "Italian", "איטלקי" | hm:ItalianScript |
| "Byzantine", "ביזנטי" | hm:ByzantineScript |
| "Yemenite", "תימני" | hm:YemeniteScript |
| "Oriental", "מזרחי" | hm:OrientalScript |
| "Persian", "פרסי" | hm:PersianScript |

| Text Pattern | Mode Instance |
|--------------|---------------|
| "square", "מרובע" | hm:SquareScript |
| "semi-cursive", "בינוני" | hm:SemicursiveScript |
| "cursive", "רהוט" | hm:CursiveScript |

---

## Transformation Rules Summary

1. **Identifier Normalization**: Remove spaces, convert to ASCII for URIs
2. **Date Parsing**: Extract years, handle Hebrew dates, uncertainty markers
3. **Name Normalization**: Consistent form for Person/Place URIs
4. **Language Mapping**: ISO codes to controlled vocabulary instances
5. **Content Parsing**: Split 505 fields into multiple Work/Expression entities
6. **Provenance Chain**: Parse 561 into sequence of Transfer events
7. **Authority Linking**: Match $0 subfields to NLI/VIAF/Wikidata

---

## Example Transformation

### Input MARC Record (simplified)
```
001 990001234560205171
008 200115s1407    it            000 0 heb d
100 1 $a משה בן יצחק אבן תבון $d פעיל 1407
245 10 $a גינת אגוז
260    $a קנדיאה $c [שבט קס"ז]
300    $a 151 דפים $c 280 x 200 מ"מ
340    $a קלף ונייר
500    $a כתב ספרדי בינוני
510 4  $a ריכלר, כתבי יד עבריים בספריית הוותיקן
561    $a יעקב מואטי; ספריית הוותיקן
856 40 $u https://digi.vatlib.it/mss/detail/203104
```

### Output RDF
```turtle
hm:MS_Barberini_82 a lrmoo:F4_Manifestation_Singleton ,
                     hm:Codicological_Unit ;
    hm:external_identifier_nli "990001234560205171" ;
    rdfs:label "כתב יד ברברין 82 – גינת אגוז"@he ;
    hm:has_number_of_folios 151 ;
    hm:has_height_mm 280 ;
    hm:has_width_mm 200 ;
    hm:has_material hm:Parchment, hm:Paper ;
    hm:has_script_type hm:SepharadicScript ;
    hm:has_script_mode hm:SemicursiveScript ;
    hm:has_digital_representation_url "https://digi.vatlib.it/mss/detail/203104"^^xsd:anyURI ;
    cidoc:P70i_is_documented_in hm:Richler_Vatican_Catalog ;
    hm:ownership_history "יעקב מואטי; ספריית הוותיקן" .

hm:Ginat_Egoz_Work a lrmoo:F1_Work ;
    hm:has_title "גינת אגוז"@he ;
    rdfs:label "Ginat Egoz"@en, "גינת אגוז"@he .

hm:Ginat_Egoz_Expression_MS_Barberini_82 a lrmoo:F2_Expression ;
    lrmoo:R3_is_realised_in hm:Ginat_Egoz_Work .

hm:MS_Barberini_82 lrmoo:R4_embodies hm:Ginat_Egoz_Expression_MS_Barberini_82 .

hm:MS_Barberini_82_Production_Event a cidoc:E12_Production ;
    cidoc:P14_carried_out_by hm:Moshe_ben_Yitzhak_Ibn_Tibbon ;
    cidoc:P7_took_place_at hm:Candia_Herakleion ;
    cidoc:P4_has_time-span hm:Year_1407_Shvat ;
    lrmoo:R27_materialized hm:MS_Barberini_82 .

hm:Moshe_ben_Yitzhak_Ibn_Tibbon a cidoc:E21_Person ;
    rdfs:label "משה בן יצחק אבן תבון"@he .

hm:Year_1407_Shvat a cidoc:E52_Time-Span ;
    hm:earliest_possible_date 1407 ;
    hm:latest_possible_date 1407 ;
    rdfs:label "שבט קס\"ז (1407)"@he .
```

---

## Implementation Notes

1. **Iterative Refinement**: Mapping rules will evolve as pilot data is processed
2. **Exception Handling**: Document edge cases and special processing
3. **Validation**: SHACL shapes will enforce mapping correctness
4. **Provenance**: Track which MARC fields contributed to each triple




