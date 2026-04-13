"""Revert the 808 ADDITIONAL bad merges found by the stricter heuristic.

Reads /tmp/extra_bad_merges.json (qid + issues) and joins with
/tmp/items_to_revert.json to get the my_revid for each.

Same safety guarantees as revert_my_modifications.py:
- Verifies first revision author != authenticated user before reverting
- Uses action=edit + undo=<my_revid>
- Retries on network errors

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/revert_extra_bad_merges.py <bearer_token>
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

    extra = json.load(open("/tmp/extra_bad_merges.json"))
    extra_qids = {e["qid"] for e in extra}

    # Get my_revid for each from items_to_revert.json
    all_mods = json.load(open("/tmp/items_to_revert.json"))
    # An item may have multiple modifications; revert ALL my mods on these qids
    mods_to_revert = [m for m in all_mods if m["qid"] in extra_qids and m.get("source")]
    # Sort newest first (highest revid) so we revert in reverse chrono order
    mods_to_revert.sort(key=lambda m: -m["revid"])

    print(f"Reverting {len(mods_to_revert)} modifications across {len(extra_qids)} items\n")

    ok = skip = fail = 0
    for i, mod in enumerate(mods_to_revert):
        qid = mod["qid"]
        my_revid = mod["revid"]
        print(f"[{i + 1}/{len(mods_to_revert)}] {qid} (rev {my_revid})...", end=" ", flush=True)

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
                    "summary": "Reverting wrong merge by automated script (item not created by me; conflicting metadata after merge)",
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
