# HMO to Wikidata Gap Analysis

Date: 2026-05-16

## Purpose

This note records how close the current MHM Wikidata projection can get to the
Hebrew Manuscripts Ontology (HMO), what is already represented, and what should
remain ontology-native in `output.ttl`.

The recommended architecture is deliberately centered on the canonical HMO RDF
graph, with two downstream publication paths:

1. `output.ttl` is the full scholarly HMO graph.
2. Optional HMO Wikibase export is a browsable/editable deployment of the full
   scholarly model.
3. Wikidata is the richest safe public projection of that graph.

Wikidata should not be forced to fully replace HMO. Wikidata's data model is
collaborative and property-driven; new properties require community proposal and
discussion, and many HMO distinctions are more precise than Wikidata's current
manuscript model.

The HMO Wikibase layer should also not be treated as a replacement for the
ontology TTL. It can expose HMO entities, relationships, and curation workflows
through familiar Wikibase pages and statements, but the ontology and generated
`output.ttl` remain the source of record for HMO semantics, validation, and
preservation.

## Evidence Checked

- Local HMO graph: `/Users/alexandergo/Desktop/test_sub2/output.ttl`
- Local Wikidata export: `/Users/alexandergo/Desktop/test_sub2/quickstatements.txt`
- Local HMO Wikibase export: `/Users/alexandergo/Desktop/test_sub2/wikibase_entities.json`
- Local HMO Wikibase report: `/Users/alexandergo/Desktop/test_sub2/wikibase_export_report.json`
- Local source report: `/Users/alexandergo/Desktop/test_sub2/wikidata_source_report.json`
- Pipeline code:
  - `converter/wikidata/property_mapping.py`
  - `converter/wikidata/item_builder.py`
  - `converter/wikidata/hmo_crosswalk.py`
- Official/community Wikidata references:
  - `https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model`
  - `https://www.wikidata.org/wiki/Wikidata:WikiProject_Medieval_manuscript_codicology`
  - `https://www.wikidata.org/wiki/Help:Data_model`
  - `https://www.wikidata.org/wiki/Help:Qualifier`
  - `https://www.wikidata.org/wiki/Help:Properties`
  - `https://www.wikidata.org/wiki/Wikidata:Property_proposal`

## Current Output Snapshot

The current Stage 4 HMO graph contains:

- `10,303` RDF triples.
- `39` RDF classes in the generated test graph.
- Major classes include:
  - `F4_Manifestation_Singleton` (`68`)
  - `Bibliographic_Unit` (`68`)
  - `Paleographical_Unit` (`68`)
  - `Codicological_Unit` (`144`)
  - `F1_Work` (`145`)
  - `F2_Expression` (`144`)
  - `TransmissionWitness` (`144`)
  - `TextTradition` (`141`)
  - `ParadigmBridge` (`141`)
  - `E12_Production` (`68`)
  - `E8_Acquisition` (`36`)
  - `E21_Person` (`288`)
  - `E53_Place` (`43`)
  - `E74_Group` (`47`)
  - `DigitalAccess` (`11`)
  - `Colophon` (`4`)
  - `ScribalIntervention` (`9`)

The current Stage 6 Wikidata projection contains:

- `193` projected items:
  - `68` manuscript items
  - `111` work items
  - `14` person items
- `2,021` projected statements.
- `28` Wikidata properties.

Most frequent current Wikidata properties:

| Property | Count | Meaning |
|---|---:|---|
| `P7535` | 427 | scope and content |
| `P1574` | 123 | exemplar of |
| `P407` | 88 | language of work or name |
| `P921` | 76 | main subject |
| `P31` | 68 | instance of |
| `P195` | 68 | collection |
| `P17` | 68 | country |
| `P131` | 68 | located in administrative territorial entity |
| `P282` | 68 | writing system |
| `P5008` | 68 | on focus list |
| `P571` | 66 | inception |
| `P217` | 65 | inventory number |
| `P1476` | 64 | title |
| `P6216` | 62 | copyright status |
| `P7153` | 53 | significant place |
| `P1071` | 52 | location of creation |
| `P136` | 49 | genre |
| `P50` | 15 | author |
| `P127` | 7 | owned by |
| `P1684` | 4 | inscription |
| `P186` | 4 | material used |
| `P2635` | 2 | number of parts |

The current HMO Wikibase draft export contains:

- `1,939` draft entities, matching the `1,939` typed HMO nodes in `output.ttl`.
- `6,736` RDF-derived draft statements, matching the source graph after
  excluding `rdf:type` and `rdfs:label`, because those are represented as
  entity class metadata and multilingual labels.
- `1,863` URI-backed typed nodes with `0` missing and `0` extra URI entities in
  the JSON draft.
- `76` blank-node-backed typed nodes; their class distribution matches between
  the source TTL and the JSON draft. Their internal blank-node IDs are parser
  local and should not be compared as stable identifiers.
- `121` multi-typed RDF nodes. These are represented as draft entities, but the
  current JSON schema exposes one preferred `class_uri` per entity. A future
  `class_uris` array should preserve all secondary `rdf:type` assertions
  explicitly.

## Key Finding

The current projection is already strong for the manuscript-level public data
model: manuscript identity, shelfmark, collection, language, writing system,
date, place, genre, subject, contents, work links, ownership, material, digital
access, and catalog references.

The remaining gap is not mainly "missing Q/P mappings." The main gap is
structural: HMO models internal scholarly objects and interpretive claims that
Wikidata usually does not model as separate first-class items.

The new optional HMO Wikibase export addresses this structural gap for local
scholarly deployment: it can represent the full HMO model in an editable
Wikibase interface while preserving `output.ttl` as the canonical graph.
Wikidata remains intentionally narrower because it is a public projection into a
shared community knowledge base.

The app-created `wikibase_entities.json` is therefore full at the entity and
statement level for the current HMO graph. It should not yet be described as a
perfect type-level mirror of the ontology until multi-class nodes expose all
their RDF classes through `class_uris`.

## Crosswalk Recommendation

| HMO concept | Current Wikidata behavior | Recommended Wikidata strategy | Keep full detail in HMO? |
|---|---|---|---|
| Manuscript / `F4_Manifestation_Singleton` | Creates manuscript item with `P31`, `P195`, `P217`, `P1476`, `P571`, `P1071`, etc. | Keep as primary Wikidata anchor. | Yes |
| Bibliographic Unit | Implicit in manuscript item. | Do not create separate item unless cataloged as a separate public manuscript object. | Yes |
| Work / `F1_Work` | Links with `P1574`; creates local work items when needed. | Link known works to existing QIDs; create new work items only when work is identifiable and reusable. | Yes |
| Expression / `F2_Expression` | Mostly collapsed into manuscript/work statements. | Do not create expression items by default; use title, language, author, folio qualifiers on work/manuscript statements. | Yes |
| Codicological Unit | Partly represented by `P2635`; folio ranges appear as `P958` qualifiers on `P1574`. | Keep CU nodes in HMO. In Wikidata use `P2635`, `P7535`, and `P958`; only use `P527` / `P361` if a part is separately identifiable and useful. | Yes |
| Paleographical Unit | Mostly represented through script style and hand notes. | Use `P9302` script style, `P1684` inscription, and `P7535` notes; avoid separate PU items. | Yes |
| Production Event | Represented as `P571` and `P1071`. | Keep event node in HMO; Wikidata gets date/place/person claims with qualifiers/references. | Yes |
| Acquisition / ownership event | Represented as `P127` where owner is available. | Add date qualifiers (`P580`, `P582`) and `P1932` named-as when extracted; consider `P11811` / `P11812` only when chain order is reliable. | Yes |
| Person | Creates/links person items when authority evidence exists. | Continue conservative creation; require VIAF/NLI/strong evidence. | Partly |
| Place | Uses KIMA/Wikidata QIDs for `P1071` and `P7153`. | Continue mapping place entities to existing Wikidata QIDs, not new local place items. | Partly |
| TextTradition | Not represented directly. | Keep in HMO unless tradition is independently notable; possible future property proposal. | Yes |
| TransmissionWitness | Not represented directly. | Keep in HMO. Possible future Wikidata modeling through `P144` / `P4969` for copied-from/derivative relationships when concrete manuscript-to-manuscript evidence exists. | Yes |
| ParadigmBridge | Not represented directly. | HMO-only. Too methodological/interpretive for Wikidata item creation. | Yes |
| PhilologicalView | Not represented directly. | HMO-only; Wikidata can carry source references and qualifiers but not full view modeling. | Yes |
| Evidence chain / epistemological status | Partly represented by references and `P1480` / `P887`. | Use references, `P1480` sourcing circumstances, `P887` based on heuristic, ranks where appropriate. Keep full chain in HMO. | Yes |
| Colophon | Represented with `P1684` + role qualifier. | Keep current mapping; add folio/location qualifiers if reliable. | Yes |
| Scribal intervention | Partly represented with `P1684`. | Use `P1684` with `P3831` role (`gloss`, `correction`, `marginalia`) and `P958` if location is known. | Yes |
| DigitalAccess | Represented via `P973` / `P6108` when available. | Prefer `P6108` for IIIF manifest and `P973`/`P953` for catalog/full-work URLs. | Partly |
| RightsDetermination | Represented via `P6216` when defensible. | Keep conservative: public domain only when date supports it and jurisdiction qualifier is added. | Yes |

## Implementation Priorities

### Priority 1: Keep HMO RDF as the canonical source

Already implemented directionally:

- Stage 4 prefers `authority_enriched_reviewed.json`.
- Stage 6 accepts `output.ttl` as preferred input.
- `wikidata_source_report.json` records source provenance.
- Optional HMO Wikibase export is positioned as a deployment layer over the full
  HMO graph, not a replacement graph.

Next improvement:

- Add a `wikidata_projection_coverage.json` report that lists each HMO class in
  the TTL and how many nodes were projected to Wikidata directly, indirectly, or
  HMO-only.
- Add `class_uris` to `WikibaseEntityDraft` and the JSON export so multi-typed
  HMO resources preserve every RDF class, not only the preferred display class.

### Priority 2: Make `hmo_crosswalk.py` less sidecar-dependent

Current behavior parses RDF first, but when `authority_enriched_reviewed.json`
or `authority_enriched.json` exists beside the TTL, the item builder still uses
that sidecar for rich Wikidata construction.

This is useful for preserving current richness, but it means the crosswalk is
not yet a fully RDF-native projection.

Recommended next steps:

1. Extract manuscript identity directly from `F4_Manifestation_Singleton`.
2. Extract Work/Expression links directly from `hm:has_work`, `hm:has_expression`,
   and `lrmoo:R4_embodies`.
3. Extract folio ranges and text locations directly from HMO `TextLocation`.
4. Extract production event date/place directly from `hm:has_production_event`.
5. Extract acquisition/ownership events directly from `E8_Acquisition`.
6. Use the sidecar only for authority decisions or fields not present in RDF.

### Priority 3: Improve CU and internal-structure projection without creating noisy items

Use:

- `P2635` for number of codicological parts when reliable.
- `P958` as a qualifier for folio/section text.
- `P7535` for human-readable structure summaries.
- `P527` / `P361` only when a part deserves a distinct Wikidata item.

Do not create a Wikidata item for every HMO `Codicological_Unit`.

### Priority 4: Improve provenance chains

Where HMO has enough order/date evidence:

- Use `P127` (`owned by`).
- Add `P580` / `P582` date qualifiers.
- Add `P1932` (`object named as`) for original catalog spelling.
- Consider `P11811` / `P11812` only when the chain order is explicit and
  reliable.

### Priority 5: Use manuscript-copy relationships when evidence exists

WikiProject Manuscripts lists:

- `P144` (`based on`) for manuscripts used as copying models.
- `P4969` (`derivative work`) for copies/apographa.
- `P518` (`applies to part`) or `P958` (`section`) to scope relationships to a
  specific part.

Recommendation:

- Map HMO `copied_from` / transmission relations to `P144` only when the source
  manuscript has a Wikidata QID or can be safely represented.
- Do not map generic `TransmissionWitness` nodes to Wikidata items.

### Priority 6: Use property proposals only after exhausting current modeling

Wikidata property creation requires public proposal and discussion. Help:Properties
also notes a practical expectation that a property should usually be usable on
at least about 100 items.

Potential future property proposal candidates:

| Candidate property | Why it may be useful | Risk |
|---|---|---|
| `textual witness of` | Would map `TransmissionWitness` to a tradition/work more cleanly than `P1574`. | May be considered too specialized or covered by existing manuscript model. |
| `belongs to text tradition` | Would expose HMO `TextTradition` in Wikidata. | Requires strong examples and community agreement. |
| `codicological unit of` | Would expose internal manuscript parts. | Likely too granular unless many projects need it. |
| `has foliation / folio range` as structured property | Better than string `P958`. | Wikidata may prefer existing `P958` / `P304` style strings. |

Do not start with property proposals. First improve the exporter with existing
properties and collect examples from at least 100 records.

## Recommended Development Plan

1. Create a machine-readable HMO-to-Wikidata crosswalk table in code or YAML.
2. Add `wikidata_projection_coverage.json` after Stage 6.
3. Keep the optional HMO Wikibase exporter aligned with the full HMO model, but
   continue to treat `output.ttl` as the canonical source for validation and
   preservation.
4. Extend the HMO Wikibase draft schema with `class_uris` for multi-class RDF
   resources.
5. Refactor `hmo_crosswalk.py` to extract more directly from RDF.
6. Add tests that check HMO nodes are projected intentionally:
   - manuscript nodes become manuscript items;
   - work nodes become work links/items when identifiable;
   - codicological units are summarized, not blindly turned into items;
   - provenance events become `P127` with qualifiers when supported;
   - epistemological structures become references/qualifiers, not items.
7. Only after that, prepare a Wikidata property proposal dossier if a real
   repeated gap remains.

## Bottom Line

The goal should be:

> HMO TTL is canonical; HMO Wikibase is browsable and editable; Wikidata is
> faithful, useful, conservative, and public.

The best next code change is not to create more Wikidata items for every HMO
node. The best next code change is to make the RDF crosswalk more explicit and
report exactly which HMO structures were projected, summarized, or intentionally
kept in HMO only.
