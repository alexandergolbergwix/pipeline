"""Merge duplicate Wikidata items created by the MHM pipeline.

SAFETY: Only operates on items in our QID range (Q138900000+).
Any merge where from_id has a numeric part < 138900000 is BLOCKED.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/merge_duplicates.py <bearer_token> [--merges-only|--blanks-only]
"""

from __future__ import annotations

import json
import sys

# SAFETY: Our items are in this range. NEVER touch items below this.
OUR_QID_MIN = 138900000
import time

import requests

API = "https://www.wikidata.org/w/api.php"


def get_session(bearer_token: str) -> tuple[requests.Session, str]:
    """Create session with bearer auth, return (session, csrf_token)."""
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {bearer_token}"
    s.headers["User-Agent"] = "MHMPipeline/1.0 (shvedbook@gmail.com)"

    resp = s.get(API, params={"action": "query", "meta": "tokens", "format": "json"})
    csrf = resp.json()["query"]["tokens"]["csrftoken"]
    print(f"Authenticated. CSRF token obtained.")
    return s, csrf


def refresh_csrf(s: requests.Session) -> str:
    resp = s.get(API, params={"action": "query", "meta": "tokens", "format": "json"})
    return resp.json()["query"]["tokens"]["csrftoken"]


def is_our_item(qid: str) -> bool:
    """Check if a QID is in our range (Q138900000+)."""
    try:
        return int(qid[1:]) >= OUR_QID_MIN
    except (ValueError, IndexError):
        return False


def merge(s: requests.Session, csrf: str, from_id: str, to_id: str) -> dict:
    if not is_our_item(from_id):
        return {"error": {"code": "safety-block", "info": f"{from_id} is NOT our item (< Q{OUR_QID_MIN})"}}
    return s.post(API, data={
        "action": "wbmergeitems", "fromid": from_id, "toid": to_id,
        "token": csrf, "ignoreconflicts": "description|sitelink|statement",
        "summary": "Merging duplicate from MHM Pipeline automated upload",
        "format": "json",
    }).json()


def blank(s: requests.Session, csrf: str, qid: str) -> dict:
    if not is_our_item(qid):
        return {"error": {"code": "safety-block", "info": f"{qid} is NOT our item (< Q{OUR_QID_MIN})"}}
    return s.post(API, data={
        "action": "wbeditentity", "id": qid, "clear": "true",
        "data": json.dumps({
            "labels": {"en": {"language": "en", "value": "deleted - MHM Pipeline duplicate"}},
            "descriptions": {},
            "claims": [],
        }),
        "token": csrf,
        "summary": "Blanking orphan duplicate from MHM Pipeline test run",
        "format": "json",
    }).json()


def run_op(s, csrf, items, op_func, op_name):
    ok = skip = fail = 0
    for i, item in enumerate(items):
        if isinstance(item, list):
            from_id, to_id = item
            label = f"{from_id} → {to_id}"
        else:
            from_id, to_id = item, None
            label = item

        print(f"[{i+1}/{len(items)}] {op_name} {label}...", end=" ", flush=True)
        try:
            if to_id:
                result = op_func(s, csrf, from_id, to_id)
            else:
                result = op_func(s, csrf, from_id)

            if result.get("success"):
                print("OK")
                ok += 1
            elif "error" in result:
                code = result["error"].get("code", "")
                info = result["error"].get("info", "")
                if any(x in code + info.lower() for x in ["no-such-entity", "cant-load", "redirect", "already"]):
                    print("SKIP")
                    skip += 1
                elif "badtoken" in code:
                    csrf = refresh_csrf(s)
                    if to_id:
                        r2 = op_func(s, csrf, from_id, to_id)
                    else:
                        r2 = op_func(s, csrf, from_id)
                    if r2.get("success"):
                        print("OK (retry)")
                        ok += 1
                    else:
                        print(f"FAIL: {r2.get('error', {}).get('info', '')}")
                        fail += 1
                else:
                    print(f"FAIL: {code} — {info[:80]}")
                    fail += 1
            else:
                print(f"???")
                fail += 1
        except Exception as e:
            print(f"ERR: {e}")
            fail += 1

        time.sleep(1.5)
        if (i + 1) % 50 == 0:
            csrf = refresh_csrf(s)

    print(f"\n{op_name} done: {ok} ok, {skip} skip, {fail} fail")
    return csrf


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    token = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "--all"

    s, csrf = get_session(token)

    if mode in ("--all", "--merges-only"):
        with open("/tmp/wikidata_merges.json") as f:
            merges = json.load(f)
        print(f"\n=== PHASE 1: {len(merges)} merges ===\n")
        csrf = run_op(s, csrf, merges, merge, "Merge")

    if mode in ("--all", "--blanks-only"):
        with open("/tmp/wikidata_orphans.json") as f:
            orphans = json.load(f)
        print(f"\n=== PHASE 2: {len(orphans)} blanks ===\n")
        csrf = run_op(s, csrf, orphans, blank, "Blank")


if __name__ == "__main__":
    main()
