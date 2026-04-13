"""Revert modifications I made to items I did NOT create (bad-merge subset).

Two-layer safety (see scripts/lib/wikidata_safety.py for the shared helpers):
1. Item creator must NOT be me — otherwise it's my own item, nothing to revert.
2. Latest editor MUST be me — otherwise someone else (e.g. Epìdosis) has touched
   the item after my edit, and undoing my older revision would silently
   override their correction.

Reads /tmp/items_to_revert.json built by audit script.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/revert_my_modifications.py <bearer_token>
"""

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

    with open("/tmp/bad_merge_targets.json") as f:
        bad_targets = {b["qid"] for b in json.load(f)}
    with open("/tmp/items_to_revert.json") as f:
        all_mods = json.load(f)
    modifications = [m for m in all_mods if m["qid"] in bad_targets and m["source"]]

    print(f"\nFiltered to {len(modifications)} bad-merge reverts (from {len(all_mods)} total)\n")

    ok = skip = fail = 0
    for i, mod in enumerate(modifications):
        qid = mod["qid"]
        my_revid = mod["revid"]
        print(f"[{i + 1}/{len(modifications)}] {qid} (rev {my_revid})...", end=" ", flush=True)

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

        try:
            res = s.post(
                data={
                    "action": "edit",
                    "title": qid,
                    "undo": my_revid,
                    "token": csrf,
                    "summary": "Reverting unauthorized modification by automated script (item not created by me)",
                    "format": "json",
                },
            ).json()
            if res.get("edit", {}).get("result") == "Success":
                print("REVERTED")
                ok += 1
            elif "error" in res:
                err = res["error"].get("info", "")
                if "undofailure" in res["error"].get("code", "") or "newer than" in err.lower():
                    print("SKIP (already reverted/redirect)")
                    skip += 1
                else:
                    print(f"FAIL: {err[:60]}")
                    fail += 1
            else:
                print(f"???: {res}")
                fail += 1
        except Exception as e:
            print(f"ERR: {e}")
            fail += 1

        time.sleep(1.5)
        if (i + 1) % 50 == 0:
            csrf = get_csrf_token(s)

    print(f"\nDONE: {ok} reverted, {skip} skipped, {fail} failed")


if __name__ == "__main__":
    main()
