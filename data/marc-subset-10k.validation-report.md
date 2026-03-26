# SHACL Validation Report

**Generated:** 2025-12-20 20:36:20
**Input File:** `/Users/alexandergo/Documents/Doctorat/second-paper/data/marc-subset-10k.csv`
**Output File:** `/Users/alexandergo/Documents/Doctorat/second-paper/data/marc-subset-10k.ttl`

## Summary

❌ **VALIDATION FAILED**

| Category | Count |
|----------|-------|
| Errors | 1 |
| Warnings | 306 |
| Info | 0 |

## Conversion Statistics

| Metric | Value |
|--------|-------|
| Records Processed | 10,000 |
| Total Triples | 469,654 |
| Output Size | 35.79 MB |
| Manuscripts | 10,000 |
| Persons | 16,313 |
| Works | 6,675 |
| Expressions | 10,270 |
| Places | 44 |

## Errors (Must Fix)

### Digital URL must be a valid HTTP(S) URI
**Count:** 1

| Entity | Path | Value |
|--------|------|-------|
| `MS_990001320720205171` | `has_digital_representation_url` | ahttp://gallica.bnf.fr/ark:/12148/btv1b107202145 |

## Warnings (Data Quality)

### Manuscript should embody at least one Expression
**Count:** 306

<details>
<summary>Show affected entities (20 of 306)</summary>

- `MS_990001252000205171`
- `MS_990001251110205171`
- `MS_990001243010205171`
- `MS_990001255940205171`
- `MS_990001259400205171`
- `MS_990001254510205171`
- `MS_990001240300205171`
- `MS_990001239520205171`
- `MS_990001256230205171`
- `MS_990001238430205171`
- `MS_990001259550205171`
- `MS_990001259800205171`
- `MS_990001243700205171`
- `MS_990001259340205171`
- `MS_990001255220205171`
- `MS_990001244020205171`
- `MS_990001252060205171`
- `MS_990001251080205171`
- `MS_990001259180205171`
- `MS_990001253940205171`
- *... and 286 more*

</details>

## Recommendations

### To fix errors:

- Review entities with: Digital URL must be a valid HTTP(S) URI

### To address warnings:

- **Missing Expressions**: Some manuscripts lack linked content. This often indicates incomplete bibliographic data in the source MARC records.
