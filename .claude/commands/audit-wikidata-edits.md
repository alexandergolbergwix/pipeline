Audit my Wikidata contributions for items I modified that I did NOT create.

Use this when:
- A community member complains about edits/merges from the MHM Pipeline.
- Before any bulk revert run, to refresh the working set in `/tmp/`.
- After a fresh upload, to confirm zero out-of-range modifications.

## What it does

Runs the audit logic that produces `/tmp/items_to_revert.json`:

1. Fetches all my recent Wikidata contributions via `usercontribs`.
2. For each Q-item I touched, fetches its first-revision author.
3. If the first-revision author ≠ me, records the modification.
4. Writes the result to `/tmp/items_to_revert.json` with fields:
   `qid`, `revid`, `ts`, `source` (merge from-id or null), `comment`.

Then runs the post-merge conflict heuristic:

5. For each merge-target QID, fetches current claims for P227 / P569 / P19 / P20 / P31 / P17 / P495 / P740.
6. Items with multiple values on any of those properties land in `/tmp/bad_merge_targets.json` (3-way) or `/tmp/extra_bad_merges.json` (any single conflict).

## Run

```bash
PYTHONPATH=src:. .venv/bin/python audit_wikidata_manuscripts.py
PYTHONPATH=src:. .venv/bin/python scripts/find_more_bad_merges.py
```

Both are read-only; no token needed.

## Verify

```bash
python3 -c "
import json
mods = json.load(open('/tmp/items_to_revert.json'))
bad  = json.load(open('/tmp/bad_merge_targets.json'))
extra = json.load(open('/tmp/extra_bad_merges.json'))
print(f'Modifications on items I did not create: {len(mods)}')
print(f'Bad merges (3-way conflict):             {len(bad)}')
print(f'Extra bad merges (any conflict):         {len(extra)}')
"
```

## Companion commands

- `/revert-wikidata-edits` — revert what this audit found
- `/wikidata-safety-check` — verify safety guards are in place
