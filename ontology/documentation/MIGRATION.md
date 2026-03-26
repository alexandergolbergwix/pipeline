# Migration: Breaking Cleanup (Legacy Properties Removed)

As of the breaking cleanup pass, the following ontology properties **have been removed**. Existing data and queries that use them must be remapped to the canonical replacements.

| Removed property | Use instead |
|------------------|-------------|
| `hm:has_Script_Mode` | `hm:has_script_mode` |
| `hm:external_wikidata_id` | `hm:wikidata_id` |
| `hm:anthology_position` (Expression → xsd:integer) | `hm:has_anthology_position` → `hm:AnthologyPosition` → `hm:anthology_order` |
| `hm:links_work_to_tradition` | `hm:has_linked_work` |
| `hm:links_tradition_to_work` | `hm:has_linked_tradition` |

- **Script mode / Wikidata / Paradigm bridge:** Replace the old predicate with the canonical one; domain/range and semantics are unchanged.
- **Anthology position:** Replace triples `?expr hm:anthology_position ?n` with the object pattern: create an `hm:AnthologyPosition` instance, link with `hm:has_anthology_position`, and set `hm:anthology_order` to the integer on that position.

See `LEGACY_TO_CANONICAL_CHECKLIST.md` for full details. After migration, run Turtle parse checks and SHACL validation on your data.
