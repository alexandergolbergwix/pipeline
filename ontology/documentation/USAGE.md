# Hebrew Manuscripts Ontology — Quick Reference (v1.7)

## Namespace

Prefer the `hm:` prefix for ontology classes and properties (e.g. `hm:has_script_mode`, `hm:MultiVolumeSet`). The ontology IRI is `http://www.ontology.org.il/HebrewManuscripts/2025-12-06#`.

## Removed legacy properties (breaking cleanup)

The following properties were removed; use only the canonical replacements. See `LEGACY_TO_CANONICAL_CHECKLIST.md` and the migration note in the documentation.

| Removed (legacy) | Use instead |
|------------------|-------------|
| `hm:has_Script_Mode` | `hm:has_script_mode` |
| `hm:external_wikidata_id` | `hm:wikidata_id` |
| `hm:links_work_to_tradition` | `hm:has_linked_work` |
| `hm:links_tradition_to_work` | `hm:has_linked_tradition` |
| `hm:anthology_position` (Expression → integer) | `hm:has_anthology_position` → `hm:AnthologyPosition` → `hm:anthology_order` |

## Anthology position (canonical pattern)

Use the object-property pattern for ordering expressions within an anthology:

```turtle
:expr1 a lrmoo:F2_Expression ;
    hm:has_anthology_position :pos1 .

:pos1 a hm:AnthologyPosition ;
    hm:anthology_order 1 .
```

## SHACL required / strongly expected fields

| Shape | Property | Constraint |
|-------|----------|------------|
| ManuscriptShape | `rdfs:label` | minCount 1 (Violation) |
| ManuscriptShape | `hm:external_identifier_nli` | minCount 1, maxCount 1 (Violation) |
| ExpressionShape | `rdfs:label` | minCount 1 (Violation) |
| ExpressionShape | `lrmoo:R3_is_realised_in` | minCount 1 (Warning) |
| ExpressionShape | `hm:has_folio_range` | minCount 1 (Warning) |
| ColophonShape | `hm:colophon_text` | minCount 1 (Violation) |
| ColophonShape | `hm:mentions_scribe` | minCount 1 (Warning) |
| ProductionEventShape | `cidoc-crm:P4_has_time-span` | minCount 1 (Violation) |
| ProductionEventShape | `R27_materialized` | minCount 1 (Violation) |

Run validation with: `pyshacl -s shacl-shapes.ttl -d data.ttl`

## Multi-volume sets

- **From the set**: use `hm:has_volume` (domain `hm:MultiVolumeSet`, range `lrmoo:F4_Manifestation_Singleton`) to link a set to its volumes.
- **From the volume**: use `hm:is_volume_of` to link a volume to its set. The two properties are inverse; SHACL validates that each multi-volume set has at least two `hm:has_volume` values.

## Reasoning implications

- **`hm:is_volume_of`** is declared `owl:FunctionalProperty`: each volume belongs to at most one multi-volume set. If the same volume is asserted in two sets, the reasoner will infer that those sets are identical.
- Use an OWL-DL reasoner (e.g. Pellet, HermiT) for classification and consistency; use SHACL for data-quality validation (closed-world style checks where needed).
