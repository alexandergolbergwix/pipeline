Revert MHM Pipeline modifications on items not created by me — safely.

Use this AFTER running `/audit-wikidata-edits` to refresh the working files in `/tmp/`.

## Mandatory pre-flight

```bash
# 1. Verify safety guards still pass
PYTHONPATH=src:. .venv/bin/python -m pytest tests/unit/test_safety_guards.py -v

# 2. Verify token authenticates as the expected user
curl -sH "Authorization: Bearer $(cat /tmp/wd_token.txt)" \
  "https://www.wikidata.org/w/api.php?action=query&meta=userinfo&format=json"

# 3. Verify input files exist
ls -la /tmp/items_to_revert.json /tmp/bad_merge_targets.json /tmp/extra_bad_merges.json
```

## Run order (tightest filter first)

Each script enforces:
1. **Creator check** — never touch items I did not create.
2. **Latest-editor check** — never undo when someone else has touched the item since my edit (Epìdosis re-merge case).
3. **Network retry** — six attempts, exponential backoff capped at 30 s.

```bash
# Phase 1 — bad merges (3-way GND/DOB/POB conflict)
PYTHONPATH=src:. .venv/bin/python scripts/revert_my_modifications.py \
  "$(cat /tmp/wd_token.txt)" 2>&1 | tee /tmp/revert_phase1.log

# Phase 2 — destructive label/description overwrites
PYTHONPATH=src:. .venv/bin/python scripts/revert_label_overwrites.py \
  "$(cat /tmp/wd_token.txt)" 2>&1 | tee /tmp/revert_phase2.log

# Phase 3 — extra bad merges (any single conflict, e.g. DOB only)
PYTHONPATH=src:. .venv/bin/python scripts/revert_extra_bad_merges.py \
  "$(cat /tmp/wd_token.txt)" 2>&1 | tee /tmp/revert_phase3.log

# Phase 4 — restore source-side blanked redirects
PYTHONPATH=src:. .venv/bin/python scripts/revert_source_side_merges.py \
  "$(cat /tmp/wd_token.txt)" 2>&1 | tee /tmp/revert_phase4.log
```

## Watch progress while running

```bash
tail -f /tmp/revert_phase1.log
# or
grep -c REVERTED /tmp/revert_phase1.log
```

## After completion

Each phase ends with `DONE: <n> reverted, <n> skipped, <n> failed`. **Failed must be 0.** Skipped is fine (idempotent re-runs and items where the latest editor isn't me both report SKIP).

If any phase shows failures, do NOT re-run blindly — investigate the failure first.

## Companion commands

- `/audit-wikidata-edits` — generate the input files
- `/wikidata-safety-check` — verify the safety guards
