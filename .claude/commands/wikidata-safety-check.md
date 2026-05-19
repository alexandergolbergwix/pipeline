Verify all Wikidata safety guards are wired up and unit + integration tests pass.

Use this:
- Before any bulk operation against Wikidata.
- After modifying any file under `converter/wikidata/`, `scripts/`, or any installer.
- As part of the PR review checklist.

## Run

```bash
# 1. Full safety-guard unit suite (covers Rules 23, 24, 25, 26, 28, 31, 33,
#    38, 42, 44 — see CLAUDE.md for each)
PYTHONPATH=src:. .venv/bin/python -m pytest tests/unit/test_safety_guards.py -v

# 2. Rule-by-rule integration tests against the real corpus
#    (covers Rules 23, 25, 28, 31, 38, 39, 42, 44, 45, 46 end-to-end)
PYTHONPATH=src:. .venv/bin/python -m pytest \
    tests/integration/test_rules_end_to_end.py -v

# 3. Hebrew transliteration + IIIF + cross-platform parity integration tests
#    (Rule 46 waterfall, Rule 45 IIIF + P6108 coexistence, macOS/Windows
#    installer parity for the 7 bundled models)
PYTHONPATH=src:. .venv/bin/python -m pytest \
    tests/integration/test_hebrew_translit_e2e.py \
    tests/unit/test_iiif_phase3.py \
    tests/unit/test_hebrew_translit.py \
    tests/unit/test_wikidata_reverse_lookup.py \
    tests/unit/test_nakdan_translit.py -v

# 4. Compile-check every revert / cleanup script
for f in scripts/revert_*.py scripts/lib/wikidata_safety.py \
         scripts/merge_duplicates.py scripts/fix_wikidata_items.py \
         scripts/find_more_bad_merges.py; do
  PYTHONPATH=src:. .venv/bin/python -m py_compile "$f" \
    && echo "OK: $f" || echo "FAIL: $f"
done

# 5. Confirm every revert script imports the shared safety module
grep -L "from scripts.lib.wikidata_safety import" scripts/revert_*.py
# ↑ should print nothing — every revert script must import it

# 6. Confirm the uploader still has the four-stage guard (Rule 38)
grep -n "_would_create_identity_conflict\|_is_our_item\|_assert_modifiable" \
    converter/wikidata/uploader.py

# 7. Confirm the reconciler still has the cross-identifier check
grep -n "_candidate_conflicts\|_fetch_identity_claims" \
    converter/wikidata/reconciler.py
```

## Pass criteria

- `test_safety_guards.py`: **502 tests pass, 0 fail.**
- `test_rules_end_to_end.py`: **all rule-by-rule tests pass** (1 skip allowed
  for the somevalue assertion when no anonymous-author records in the corpus).
- `test_hebrew_translit_e2e.py`: **18 tests pass** (5-tier waterfall + Stage 6
  pipeline + cache integration + graceful degradation + dissertation examples).
- All compile checks: **OK** for every script.
- Steps 5 + 6 + 7: every revert script imports the safety module; the uploader
  and reconciler still expose the guards.

## What each guard prevents

| Guard | Rule | Prevents |
|---|---|---|
| `_candidate_conflicts` (reconciler) | 23 / 26 | Single-ID match → wrong-merge of two unrelated entities |
| `_would_create_identity_conflict` (uploader) | 23 | Adding P569/P570/P19/P20/P227/P214/P8189/P213/P244/P21 that conflicts with existing value |
| `_MULTI_VALUE_IDENTITY_PROPS` (uploader) | 42 | P31 multi-value only inside the manuscript-class allowlist (no `P31=Q5` on a manuscript item) |
| `_assert_modifiable` (uploader, four stages) | 38 | Modification of items not created by the authenticated user |
| Label-overwrite guard (uploader) | 23 | Overwriting community-authored labels |
| `_has_conflict` (merge_duplicates) | 23 | `wbmergeitems` between items with different identity properties |
| `is_safe_to_revert` (wikidata_safety) | 24 | Reverting items I didn't create OR overriding someone else's correction |
| `_check_moratorium_for_live` (uploader) | 25 | Live Wikidata writes without `MORATORIUM_LIFTED=true` |
| `_is_anonymous_name` / `_is_role_descriptor` (item_builder) | 28 | Anonymous placeholders and role descriptors becoming person items |
| `TestMacOsWindowsInstallerParity` (integration test) | 46 | macOS .app and Windows installer drifting in bundled model set |
| `english_label_for_hebrew` waterfall + NLI fallback (item_builder) | 46 | Synthetic `"work from Hebrew manuscript X"` labels on Wikidata |

## See also

- `docs/WIKIDATA_REVERT_SAFETY.md` — full incident report and template
- `CLAUDE.md` rules 23–46 — non-negotiable safety rules (the most recent
  additions are 42 multi-P31 + ranks, 44 P973 + projection coverage,
  45 IIIF P6108 coexistence, 46 Hebrew transliteration waterfall +
  cross-platform installer parity)
- `/audit-wikidata-edits` — generate working files for revert
- `/revert-wikidata-edits` — perform reverts safely
- `/reinstall-app` — rebuild + reinstall the bundled .app (verifies the 7
  required models stay in lockstep across macOS and Windows installers)
