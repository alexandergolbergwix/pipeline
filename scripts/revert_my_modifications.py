"""Revert ALL modifications I made to items I did NOT create.

Safety: For each modification, this script:
1. Gets the FIRST revision author of the item
2. If creator != my username → revert my edit (use action=edit + undo=<my_revid>)
3. If creator == my username → leave alone (it's my item)

Reads /tmp/items_to_revert.json built by audit script.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/revert_my_modifications.py <bearer_token>
"""

from __future__ import annotations

import json
import sys
import time

import requests

API = "https://www.wikidata.org/w/api.php"


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    token = sys.argv[1]

    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    s.headers["User-Agent"] = "MHMPipeline/1.0 (shvedbook@gmail.com)"

    csrf = s.get(API, params={"action": "query", "meta": "tokens", "format": "json"}).json()[
        "query"
    ]["tokens"]["csrftoken"]

    user_resp = s.get(API, params={"action": "query", "meta": "userinfo", "format": "json"})
    auth_user = user_resp.json().get("query", {}).get("userinfo", {}).get("name", "")
    print(f"Authenticated as: {auth_user}")

    # Load bad merges (items with conflicting GND/DOB/POB after my merge)
    with open("/tmp/bad_merge_targets.json") as f:
        bad_targets = {b["qid"] for b in json.load(f)}

    # Filter all my modifications to only those targeting bad-merge items
    with open("/tmp/items_to_revert.json") as f:
        all_mods = json.load(f)

    # Keep only bad-merge target modifications (where source != None means it was a merge)
    modifications = [m for m in all_mods if m["qid"] in bad_targets and m["source"]]

    print(f"\nFiltered to {len(modifications)} bad-merge reverts (from {len(all_mods)} total)")
    print()

    ok = skip = fail = 0
    for i, mod in enumerate(modifications):
        qid = mod["qid"]
        my_revid = mod["revid"]
        comment = mod.get("comment", "")

        print(f"[{i + 1}/{len(modifications)}] {qid} (rev {my_revid})...", end=" ", flush=True)

        # Get first revision author to verify it's not mine
        try:
            r = s.get(
                API,
                params={
                    "action": "query",
                    "prop": "revisions",
                    "titles": qid,
                    "rvprop": "user",
                    "rvdir": "newer",
                    "rvlimit": "1",
                    "format": "json",
                },
                timeout=10,
            )
            creator = ""
            for _pid, page in r.json().get("query", {}).get("pages", {}).items():
                revs = page.get("revisions", [])
                if revs:
                    creator = revs[0].get("user", "")
                    break
        except Exception as e:
            print(f"ERR getting creator: {e}")
            fail += 1
            continue

        if creator == auth_user:
            print("SKIP (I created this item)")
            skip += 1
            continue

        # Try to revert via undo
        try:
            res = s.post(
                API,
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

        # Refresh CSRF every 50
        if (i + 1) % 50 == 0:
            csrf = s.get(
                API, params={"action": "query", "meta": "tokens", "format": "json"}
            ).json()["query"]["tokens"]["csrftoken"]

    print()
    print(f"DONE: {ok} reverted, {skip} skipped, {fail} failed")


if __name__ == "__main__":
    main()
