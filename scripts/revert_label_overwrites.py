"""Revert the 35 destructive label/description overwrites on items I did NOT create.

These are wbeditentity-update-languages edits where the MHM Pipeline's name-matching
heuristic added Hebrew labels/aliases to existing items not created by me.

Safety:
- Loads the 35 label edits from /tmp/items_to_revert.json (filter: comment contains "languages")
- Verifies first revision author != authenticated user before reverting
- Uses action=edit + undo=<my_revid> to restore previous state

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/revert_label_overwrites.py <bearer_token>
"""

from __future__ import annotations

import json
import sys
import time

import requests

API = "https://www.wikidata.org/w/api.php"


def _retry_get(s, **kwargs):
    last_exc = None
    for attempt in range(6):
        try:
            return s.get(API, timeout=15, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            time.sleep(min(2**attempt, 30))
    raise last_exc


def _retry_post(s, **kwargs):
    last_exc = None
    for attempt in range(6):
        try:
            return s.post(API, timeout=20, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            time.sleep(min(2**attempt, 30))
    raise last_exc


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    token = sys.argv[1]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    s.headers["User-Agent"] = "MHMPipeline/1.0 (shvedbook@gmail.com)"

    csrf = _retry_get(
        s, params={"action": "query", "meta": "tokens", "format": "json"}
    ).json()["query"]["tokens"]["csrftoken"]

    auth_user = _retry_get(
        s, params={"action": "query", "meta": "userinfo", "format": "json"}
    ).json().get("query", {}).get("userinfo", {}).get("name", "")
    print(f"Authenticated as: {auth_user}")

    all_mods = json.load(open("/tmp/items_to_revert.json"))
    label_edits = [
        m for m in all_mods if "languages" in (m.get("comment") or "")
    ]
    print(f"Found {len(label_edits)} label/desc edits to revert\n")

    ok = skip = fail = 0
    for i, mod in enumerate(label_edits):
        qid = mod["qid"]
        my_revid = mod["revid"]
        print(f"[{i + 1}/{len(label_edits)}] {qid} (rev {my_revid})...", end=" ", flush=True)

        # Verify creator != me
        try:
            r = _retry_get(
                s,
                params={
                    "action": "query",
                    "prop": "revisions",
                    "titles": qid,
                    "rvprop": "user",
                    "rvdir": "newer",
                    "rvlimit": "1",
                    "format": "json",
                },
            ).json()
            creator = ""
            for _pid, page in r.get("query", {}).get("pages", {}).items():
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

        try:
            res = _retry_post(
                s,
                data={
                    "action": "edit",
                    "title": qid,
                    "undo": my_revid,
                    "token": csrf,
                    "summary": "Reverting unauthorized label/description overwrite by automated script (item not created by me)",
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
            csrf = _retry_get(
                s, params={"action": "query", "meta": "tokens", "format": "json"}
            ).json()["query"]["tokens"]["csrftoken"]

    print(f"\nDONE: {ok} reverted, {skip} skipped, {fail} failed")


if __name__ == "__main__":
    main()
