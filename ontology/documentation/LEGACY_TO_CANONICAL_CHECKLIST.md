# Legacy-to-Canonical Checklist (Breaking Cleanup)

This checklist maps legacy/deprecated constructs in the Hebrew Manuscripts ontology to their canonical replacements. **Breaking cleanup**: legacy terms are removed; data and queries must use only canonical terms.

## Properties Removed (use canonical replacement)

| Legacy (removed) | Canonical replacement | Notes |
|------------------|------------------------|--------|
| `hm:has_Script_Mode` | `hm:has_script_mode` | Same domain/range; casing normalized. |
| `hm:external_wikidata_id` | `hm:wikidata_id` | Same semantics; single canonical name. |
| `hm:anthology_position` | `hm:has_anthology_position` (object) + `hm:AnthologyPosition` + `hm:anthology_order` (integer on position) | Expression → integer shortcut removed; use Expression → has_anthology_position → AnthologyPosition → anthology_order. |
| `hm:links_work_to_tradition` | `hm:has_linked_work` | ParadigmBridge → Work. |
| `hm:links_tradition_to_work` | `hm:has_linked_tradition` | ParadigmBridge → TextTradition. |

## Classes and patterns unchanged

- **Multi-volume**: Canonical pair `hm:has_volume` / `hm:is_volume_of` already in use; no legacy alias existed.
- **Script mode**: Only property name changed (has_Script_Mode → has_script_mode).
- **Wikidata**: Only property name changed (external_wikidata_id → wikidata_id).
- **Anthology position**: Legacy was a datatype property (Expression → xsd:integer). Canonical is object property (Expression → AnthologyPosition) with AnthologyPosition having `hm:anthology_order` (integer).
- **Paradigm bridge**: Legacy names links_work_to_tradition / links_tradition_to_work; canonical are has_linked_work / has_linked_tradition.

## References to update outside ontology

- **SHACL**: No validation paths for removed properties; confirm no `sh:path` to legacy names.
- **Documentation**: Replace all mentions and examples of legacy properties with canonical ones (see docs-canonicalization todo).
- **Conversion pipelines**: MARC-to-TTL or other ETL must output only canonical properties.

## Validation after cleanup

- Grep for removed identifiers: `has_Script_Mode`, `external_wikidata_id`, `anthology_position`, `links_work_to_tradition`, `links_tradition_to_work` — should appear only in this checklist and migration note.
- Turtle parse check; run reasoner and SHACL on sample data.
