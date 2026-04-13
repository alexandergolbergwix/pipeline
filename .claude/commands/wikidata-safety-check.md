Verify all Wikidata safety guards are wired up and unit tests pass.

Use this:
- Before any bulk operation against Wikidata.
- After modifying any file under `converter/wikidata/` or `scripts/`.
- As part of the PR review checklist.

## Run

```bash
# 1. Unit tests for all four enforcement layers
PYTHONPATH=src:. .venv/bin/python -m pytest tests/unit/test_safety_guards.py -v

# 2. Compile-check every revert script
for f in scripts/revert_*.py scripts/lib/wikidata_safety.py \
         scripts/merge_duplicates.py scripts/fix_wikidata_items.py \
         scripts/find_more_bad_merges.py; do
  PYTHONPATH=src:. .venv/bin/python -m py_compile "$f" \
    && echo "OK: $f" || echo "FAIL: $f"
done

# 3. Confirm every revert script imports the shared safety module
grep -L "from scripts.lib.wikidata_safety import" scripts/revert_*.py
# ↑ should print nothing — every revert script must import it

# 4. Confirm the uploader still has the conflict guard
grep -n "_would_create_identity_conflict\|_is_our_item" converter/wikidata/uploader.py

# 5. Confirm the reconciler still has the cross-identifier check
grep -n "_candidate_conflicts\|_fetch_identity_claims" converter/wikidata/reconciler.py
```

## Pass criteria

- `test_safety_guards.py`: **19 tests pass, 0 fail.**
- All compile checks: **OK** for every script.
- Step 3: **no output** (every revert script imports the safety module).
- Steps 4 + 5: **at least one match per file**.

## What each guard prevents

| Guard | Prevents |
|---|---|
| `_candidate_conflicts` (reconciler) | Single-ID match → wrong-merge of two unrelated entities |
| `_would_create_identity_conflict` (uploader) | Adding P569/P570/P19/P20/P227/etc. that conflicts with existing value |
| Label-overwrite guard (uploader) | Overwriting community-authored labels |
| `_has_conflict` (merge_duplicates) | `wbmergeitems` between items with different identity properties |
| `is_safe_to_revert` (wikidata_safety) | Reverting items I didn't create OR overriding someone else's correction |

## See also

- `docs/WIKIDATA_REVERT_SAFETY.md` — full incident report and template
- `CLAUDE.md` rules 23 + 24 — non-negotiable safety rules
- `/audit-wikidata-edits` — generate working files for revert
- `/revert-wikidata-edits` — perform reverts safely
