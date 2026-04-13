# Wikidata revert safety

This document captures the rules that any script which modifies Wikidata items (revert, merge, edit, label change) MUST follow. It exists because of a real incident on 2026-04-12 — see "What went wrong" below.

If you change any script under `scripts/` that talks to the Wikidata write API, read this document first.

---

## The non-negotiable rules

Every write to Wikidata MUST satisfy ALL of:

1. **The item's creator is not me.** Use `get_first_revision_author()`.
2. **The item's most recent edit is mine.** Use `get_latest_revision_author()`.
3. **The HTTP client retries on transient network errors.** Use `RetryingSession`.
4. **The script can be re-run safely.** Already-handled items must be detected and SKIPPED, not re-applied.

The `is_safe_to_revert()` helper enforces (1) and (2) atomically. Use it. Do not write your own version.

---

## What went wrong (2026-04-12)

Three classes of error were committed by a cleanup script:

1. **Wrong merges of unrelated entities** (902 + 808 items). The pipeline trusted a single shared identifier (e.g., ISNI) and merged items that were actually different real-world entities — for example, two lawyers who shared an ISNI but had different GNDs and birth years. Result: items with multiple conflicting P227 / P569 / P19 values.

2. **Out-of-range modifications** (2,313 items). A SPARQL filter `CONTAINS(STR(?item), "/Q139")` matched both my Q138900000+ items AND unrelated Q139xxxx items. Items not created by me were modified.

3. **Destructive label/description overwrites** (35 items). The pipeline overwrote existing Hebrew labels on items not created by me.

Community complaints were filed on the talk page by Pallor, Kolja21, Dcflyer, MSGJ, Jcb, and Epìdosis.

### The Epìdosis follow-up

After the initial revert, Epìdosis manually re-applied four of my merges that were actually correct (Q109877110, Q479063, Q159933, Q55902460), commenting "Already checked, correct merge". They also pointed out that each merge produces TWO edits — one on the target (adds content) and one on the source (blanks + redirects); undoing only the target leaves the source as a blank redirect.

A naive re-run of the revert script would have re-undone Epìdosis's corrections, because `action=edit&undo=<my_revid>` undoes my revision regardless of who has touched the item since. The **latest-editor check** (rule 2 above) prevents this: if the most recent revision is by anyone other than me, SKIP — do not undo.

---

## How the safeguards are layered

Four layers, from earliest to latest in the pipeline:

| Layer | Where | What it enforces |
|---|---|---|
| Reconciler cross-check | `converter/wikidata/reconciler.py` `_candidate_conflicts()` | When a candidate matches by one identifier, fetch all other identifiers; reject the match if any conflict. |
| Uploader identity guard | `converter/wikidata/uploader.py` `_would_create_identity_conflict()` | Refuse to add a value to P569/P570/P19/P20/P227/P214/P8189/P213/P244/P31/P21 on an existing item that already has a different value. |
| Uploader label guard | `converter/wikidata/uploader.py` `_build_wbi_item()` | Only set a label on an existing item when the language slot is empty. |
| Pre-merge conflict check | `scripts/merge_duplicates.py` `_has_conflict()` | Refuse `wbmergeitems` when source and target disagree on any identity property. |
| Creator-author check | `converter/wikidata/uploader.py` `_is_our_item()` and `scripts/lib/wikidata_safety.get_first_revision_author()` | Refuse to modify items not created by the authenticated user. |
| Latest-editor check | `scripts/lib/wikidata_safety.get_latest_revision_author()` | Refuse to undo when the latest edit is by anyone else. |

---

## How to write a new revert script

Always start from this template. Replace the body of the loop with your own filter/predicate.

```python
"""One-line description of what this script reverts."""

from __future__ import annotations

import json
import sys
import time

from scripts.lib.wikidata_safety import (
    RetryingSession,
    get_authenticated_user,
    get_csrf_token,
    is_safe_to_revert,
)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    s = RetryingSession(bearer_token=sys.argv[1])
    csrf = get_csrf_token(s)
    auth_user = get_authenticated_user(s)
    print(f"Authenticated as: {auth_user}")

    # ── Load the modifications you intend to revert ──
    mods = json.load(open("/tmp/items_to_revert.json"))
    targets = [m for m in mods if some_predicate(m)]
    print(f"Considering {len(targets)} items\n")

    ok = skip = fail = 0
    for i, mod in enumerate(targets):
        qid = mod["qid"]
        my_revid = mod["revid"]
        print(f"[{i + 1}/{len(targets)}] {qid} (rev {my_revid})...", end=" ", flush=True)

        # ── Mandatory two-layer safety check ──
        try:
            safe, reason = is_safe_to_revert(s, qid, auth_user)
        except Exception as e:
            print(f"ERR safety check: {e}")
            fail += 1
            continue
        if not safe:
            print(f"SKIP ({reason})")
            skip += 1
            continue

        # ── Issue the undo ──
        try:
            res = s.post(data={
                "action": "edit",
                "title": qid,
                "undo": my_revid,
                "token": csrf,
                "summary": "Why this revert is being made — be specific",
                "format": "json",
            }).json()
            # ... handle success / error ...
        except Exception as e:
            print(f"ERR: {e}")
            fail += 1

        time.sleep(1.5)
        if (i + 1) % 50 == 0:
            csrf = get_csrf_token(s)

    print(f"\nDONE: {ok} reverted, {skip} skipped, {fail} failed")


if __name__ == "__main__":
    main()
```

---

## Things that are EXPLICITLY forbidden

- Skipping the latest-editor check "because we already verified".
- Bulk operations on items below Q138900000 without a per-item creator check.
- Replacing `RetryingSession` with bare `requests.get()` — you will lose hours of work to one DNS hiccup.
- Adding a P569 / P570 / P19 / P20 / P227 / P214 / P8189 / P213 / P244 value to an existing item without verifying it does not conflict.
- Overwriting existing labels.
- Running `wbmergeitems` without first fetching both source and target claims and checking for identity-property conflicts.

---

## Verification before any bulk run

1. Run `python -m pytest tests/unit/test_safety_guards.py` — all tests must pass.
2. Run the script on **one** item first and check the result manually on Wikidata.
3. Save the bearer token to `/tmp/wd_token.txt` (chmod 600) and pass with `"$(cat /tmp/wd_token.txt)"` to avoid copy-paste corruption.
4. Run with `2>&1 | tee /tmp/scriptname.log` so the full output is captured if the network drops.
5. After the run completes, count `REVERTED` / `SKIP` / `FAIL` lines and confirm against expectations.

---

## Files

| Path | Role |
|---|---|
| `scripts/lib/wikidata_safety.py` | Shared safety helpers — single source of truth |
| `scripts/revert_my_modifications.py` | Reverts bad merges (multi-property conflict) |
| `scripts/revert_extra_bad_merges.py` | Reverts merges flagged by stricter heuristic |
| `scripts/revert_label_overwrites.py` | Reverts destructive label edits |
| `scripts/revert_source_side_merges.py` | Restores blanked source items |
| `scripts/find_more_bad_merges.py` | Read-only audit using stricter heuristic |
| `scripts/merge_duplicates.py` | Merges with pre-merge conflict check |
| `scripts/fix_wikidata_items.py` | Fixes orgs / P1559 / commas with creator check |
| `tests/unit/test_safety_guards.py` | Unit tests — must always pass |

---

## Related Wikidata talk-page threads

- User talk:Alexander Goldberg IL § Wrong merge — Pallor, Kolja21, Epìdosis
- User talk:Alexander Goldberg IL § STOP high speed merging — Jcb
- User talk:Alexander Goldberg IL § Multiple erroneous new items created — Dcflyer, MSGJ
- User talk:Alexander Goldberg IL § Deaf and mute user — Pallor, Epìdosis
