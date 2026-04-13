"""Undo source-side edits of bad merges (restores blanked redirects).

Per Epìdosis: each merge produces TWO edits — one on the target (adds
merged content) and one on the source (blanks + redirects). Undoing only
the target leaves the source as a blank redirect.

Same two-layer safety as scripts/revert_my_modifications.py — see
scripts/lib/wikidata_safety.py for shared helpers.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/revert_source_side_merges.py <bearer_token>
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

    all_mods = json.load(open("/tmp/items_to_revert.json"))
    src_side = [m for m in all_mods if "wbmergeitems-to" in (m.get("comment") or "")]
    print(f"Found {len(src_side)} source-side merge edits\n")

    ok = skip = fail = 0
    for i, mod in enumerate(src_side):
        qid = mod["qid"]
        my_revid = mod["revid"]
        comment = mod.get("comment", "")[:60]
        print(
            f"[{i + 1}/{len(src_side)}] {qid} (rev {my_revid}) — {comment}...", end=" ", flush=True
        )

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
                    "summary": "Restoring source-item content (per Epìdosis: each merge needs two undos)",
                    "format": "json",
                },
            ).json()
            if res.get("edit", {}).get("result") == "Success":
                print("REVERTED")
                ok += 1
            elif "error" in res:
                err = res["error"].get("info", "")
                code = res["error"].get("code", "")
                if "undofailure" in code or "newer than" in err.lower():
                    print("SKIP (already reverted/changed)")
                    skip += 1
                else:
                    print(f"FAIL: {err[:80]}")
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
