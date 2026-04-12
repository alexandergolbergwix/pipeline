# Duplicate Prevention Guide — MHM Pipeline → Wikidata

## Incident Summary

On April 11-12, 2026, the MHM Pipeline created thousands of duplicate person items on Wikidata because:

1. **Multiple pipeline runs** created the same persons repeatedly (up to 10x per person across test runs)
2. **VIAF cluster harvesting** added LCCN/GND/ISNI identifiers to our items, which already existed on established Wikidata items — triggering Wikidata's duplicate detection bots
3. **Reconciliation only checked VIAF and NLI IDs**, missing existing items findable via LCCN, GND, or ISNI

**Cleanup required:** ~3,000 merge operations across 4 rounds.

---

## Root Causes

### 1. No upload_results.json cache between runs

Each pipeline run created new items without checking if a previous run had already created them. The `upload_results.json` from prior runs was not loaded.

**Fix:** The pipeline now loads `upload_results.json` and `existing_qids.json` from the output directory before uploading. Items with known QIDs are updated, not re-created.

### 2. Reconciliation only checked 2 identifiers

The `WikidataReconciler.reconcile_person()` only checked:
- P214 (VIAF ID)
- P8189 (NLI J9U ID)

It did NOT check:
- P244 (LCCN) — Library of Congress
- P227 (GND) — German National Library
- P213 (ISNI) — International Standard Name Identifier
- P268 (BnF) — French National Library

**Fix:** `reconcile_person()` now accepts and checks `lc_id`, `gnd_id`, and `isni` parameters. It checks all 5 identifiers in order: VIAF → NLI → LCCN → GND → ISNI.

### 3. VIAF cluster harvesting created identifier conflicts

When a person matched VIAF, the pipeline fetched the VIAF cluster JSON and extracted LCCN/GND/ISNI identifiers. These were added to our newly created items — but those same identifiers already existed on established Wikidata items for the same person. This created two items with the same LCCN, which Wikidata's duplicate detection bot flagged.

**Fix:** The reconciler now checks ALL cluster-harvested identifiers BEFORE creating any new item. If an item already exists with any of these identifiers, the pipeline updates it instead of creating a new one.

---

## Prevention Checklist

Before ANY Wikidata upload:

### Pre-Upload

- [ ] Load `upload_results.json` from prior runs (if exists) to get known QIDs
- [ ] Run batch SPARQL reconciliation using ALL available identifiers:
  - P8189 (NLI J9U ID)
  - P214 (VIAF ID)
  - P244 (LCCN)
  - P227 (GND ID)
  - P213 (ISNI)
- [ ] Apply reconciled QIDs to items BEFORE upload

### During Upload

- [ ] Use `ActionIfExists.MERGE_REFS_OR_APPEND` — never REPLACE_ALL
- [ ] Check if `existing_qid` is set on each item:
  - If yes → update existing item (add new claims only)
  - If no → create new item
- [ ] Save `upload_results.json` after EVERY item (not just at the end)
- [ ] Track `created_qids` dict for `__LOCAL:` resolution

### Post-Upload

- [ ] Run SPARQL verification for each identifier type:
  ```sparql
  SELECT ?item1 ?item2 ?id WHERE {
    ?item1 wdt:P244 ?id .
    ?item2 wdt:P244 ?id .
    FILTER(STR(?item1) < STR(?item2))
    FILTER(CONTAINS(STR(?item1), "/Q139"))
  }
  ```
- [ ] If duplicates found, merge immediately using `scripts/merge_duplicates.py`

---

## Code Locations

| Component | File | Purpose |
|---|---|---|
| Reconciler | `converter/wikidata/reconciler.py` | SPARQL-based dedup before upload |
| `reconcile_person()` | Line ~285 | Checks VIAF → NLI → LCCN → GND → ISNI |
| `reconcile_person_by_external_id()` | Line ~243 | Generic external-id SPARQL check |
| `reconcile_batch_by_nli_id()` | Line ~314 | Batch NLI reconciliation (VALUES clause) |
| Uploader | `converter/wikidata/uploader.py` | Wikidata API upload with claim diffing |
| `MERGE_REFS_OR_APPEND` | Line ~200 | Safe upsert mode (no duplicates, no deletions) |
| `__LOCAL:` resolution | Line ~464 | Resolves local IDs to created QIDs |
| Item Builder | `converter/wikidata/item_builder.py` | Builds items with dedup keys |
| `_person_key()` | Line ~137 | Person dedup: mazal > viaf > name |
| `_work_key()` | Line ~149 | Work dedup: normalized title |
| VIAF Cluster | `converter/authority/viaf_matcher.py` | Harvests GND/LC/ISNI/BnF from VIAF |
| `get_cluster_identifiers()` | Line ~57 | Fetches viaf.org/viaf/{id} JSON |
| Merge Script | `scripts/merge_duplicates.py` | Emergency cleanup tool |

---

## Key Wikidata Properties for Deduplication

| Property | Label | Type | Source |
|---|---|---|---|
| P8189 | NLI J9U ID | external-id | Mazal authority DB |
| P214 | VIAF ID | external-id | VIAF name search |
| P244 | LCCN | external-id | VIAF cluster (LC source) |
| P227 | GND ID | external-id | VIAF cluster (DNB source) |
| P213 | ISNI | external-id | VIAF cluster (ISNI source) |
| P268 | BnF ID | external-id | VIAF cluster (BNF source) |

**Rule:** If ANY of these identifiers matches an existing Wikidata item, UPDATE that item instead of creating a new one.

---

## Emergency Cleanup Procedure

If duplicates are discovered after upload:

1. **Identify duplicates** via SPARQL:
   ```sparql
   SELECT ?item1 ?item2 ?id WHERE {
     ?item1 wdt:P244 ?id .
     ?item2 wdt:P244 ?id .
     FILTER(STR(?item1) < STR(?item2))
     FILTER(STRSTARTS(SUBSTR(STR(?item1), 32), "Q139"))
   }
   ```

2. **Build merge list** — for each pair, merge our item (Q139xxx) into the established item

3. **Run merge script:**
   ```bash
   PYTHONPATH=src:. .venv/bin/python scripts/merge_duplicates.py "<bearer_token>"
   ```

4. **Verify** — re-run the SPARQL queries to confirm zero duplicates

5. **Notify Wikidata community** if the duplicates were flagged

---

## Lessons Learned

1. **Always reconcile before creating.** Check ALL known identifiers, not just the primary one.
2. **Load prior upload results.** Never start a fresh upload without checking what was already created.
3. **Test on test.wikidata.org first.** Use `is_test=True` in `WikidataUploader` for new features.
4. **One upload run, one dataset.** Don't run the same dataset through the pipeline multiple times without loading prior results.
5. **VIAF cluster identifiers are shared.** If you harvest LCCN from VIAF, that LCCN likely already exists on an established Wikidata item. Check it before creating.
