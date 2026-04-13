"""Clean up duplicate claims on Wikidata items after merges.

When wbmergeitems copies claims from source→target, it can create duplicate
values for external-id properties (P214, P244, P227, P213, P268, P8189).
This script finds and removes duplicate claims on items we merged into.

Safety (uses scripts/lib/wikidata_safety.py):
1. Range check (QID >= Q138900000) — cheap pre-filter.
2. Creator check — first revision author must be the authenticated user.
3. Latest-editor check — most recent edit must be the authenticated user
   (otherwise someone else has touched the item since my edit, and removing
   a "duplicate" claim could undo their correction).

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/cleanup_duplicate_claims.py <bearer_token>
"""

from __future__ import annotations

import json
import sys
import time

import requests

from scripts.lib.wikidata_safety import (
    OUR_QID_MIN,
    RetryingSession,
    get_authenticated_user,
    get_csrf_token,
    is_safe_to_revert,
)

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"

# Properties that should have only ONE value per item
SINGLE_VALUE_PROPS = ["P214", "P244", "P227", "P213", "P268", "P8189"]


def in_our_range(qid: str) -> bool:
    """Cheap pre-filter — final safety is the creator+latest-editor check."""
    try:
        return int(qid[1:]) >= OUR_QID_MIN
    except (ValueError, IndexError):
        return False


def get_items_with_dup_claims(prop: str) -> list[dict]:
    """Find items that have duplicate values for a property via SPARQL."""
    headers = {
        "User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)",
        "Accept": "application/sparql-results+json",
    }
    query = f"""
    SELECT ?item (COUNT(?val) AS ?count) WHERE {{
      ?item wdt:{prop} ?val .
      ?item wdt:P1343 wd:Q118384267 .
    }}
    GROUP BY ?item
    HAVING (COUNT(?val) > 1)
    LIMIT 500
    """
    try:
        resp = requests.get(
            SPARQL,
            params={"query": query, "format": "json"},
            headers=headers,
            timeout=60,
        )
        bindings = resp.json()["results"]["bindings"]
        return [
            {
                "qid": b["item"]["value"].split("/")[-1],
                "count": int(b["count"]["value"]),
            }
            for b in bindings
        ]
    except Exception as e:
        print(f"  SPARQL error for {prop}: {e}")
        return []


def remove_duplicate_claims(s: RetryingSession, csrf: str, qid: str, prop: str) -> int:
    """Remove duplicate claims for a property on an item, keeping the first one."""
    resp = s.get(
        params={
            "action": "wbgetclaims",
            "entity": qid,
            "property": prop,
            "format": "json",
        },
    )
    data = resp.json()
    claims = data.get("claims", {}).get(prop, [])

    if len(claims) <= 1:
        return 0

    seen_values: dict[str, str] = {}
    to_remove: list[str] = []
    for claim in claims:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        value = datavalue.get("value", "")
        if isinstance(value, dict):
            value = json.dumps(value, sort_keys=True)
        if value in seen_values:
            to_remove.append(claim["id"])
        else:
            seen_values[value] = claim["id"]

    if not to_remove:
        return 0

    for claim_id in to_remove:
        resp = s.post(
            data={
                "action": "wbremoveclaims",
                "claim": claim_id,
                "token": csrf,
                "summary": "Removing duplicate claim created by merge (MHM Pipeline cleanup)",
                "format": "json",
            },
        )
        result = resp.json()
        if "error" in result:
            code = result["error"].get("code", "")
            if "no-such-claim" not in code:
                print(f"    Error removing {claim_id}: {result['error'].get('info', '')}")

    return len(to_remove)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    s = RetryingSession(bearer_token=sys.argv[1])
    csrf = get_csrf_token(s)
    auth_user = get_authenticated_user(s)
    print(f"Authenticated as: {auth_user}")

    total_removed = 0

    for prop in SINGLE_VALUE_PROPS:
        print(f"\n=== Checking {prop} for duplicate claims ===")
        items = get_items_with_dup_claims(prop)
        print(f"  Found {len(items)} items with duplicate {prop}")

        for i, item in enumerate(items):
            qid = item["qid"]
            print(
                f"  [{i + 1}/{len(items)}] {qid} ({item['count']} values)...",
                end=" ",
                flush=True,
            )

            # Cheap pre-filter
            if not in_our_range(qid):
                print("SKIP (out of range)")
                continue

            # Full safety check: creator must be me AND latest editor must be me
            try:
                safe, reason = is_safe_to_revert(s, qid, auth_user)
            except Exception as e:
                print(f"ERR safety check: {e}")
                continue
            if not safe:
                print(f"SKIP ({reason})")
                continue

            try:
                removed = remove_duplicate_claims(s, csrf, qid, prop)
                if removed:
                    print(f"removed {removed}")
                    total_removed += removed
                else:
                    print("no true dups")
            except Exception as e:
                print(f"ERROR: {e}")

            time.sleep(1.5)

            if (i + 1) % 50 == 0:
                csrf = get_csrf_token(s)

        time.sleep(3)

    print(f"\n=== DONE: Removed {total_removed} duplicate claims ===")


if __name__ == "__main__":
    main()
