"""Clean up duplicate claims on Wikidata items after merges.

When wbmergeitems copies claims from source→target, it can create duplicate
values for external-id properties (P214, P244, P227, P213, P268, P8189).
This script finds and removes duplicate claims on items we merged into.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/cleanup_duplicate_claims.py <bearer_token>
"""

from __future__ import annotations

import json
import sys
import time

import requests

# SAFETY: Only touch items in our range
OUR_QID_MIN = 138900000


def is_our_item(qid: str) -> bool:
    try:
        return int(qid[1:]) >= OUR_QID_MIN
    except (ValueError, IndexError):
        return False

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"

# Properties that should have only ONE value per item
SINGLE_VALUE_PROPS = ["P214", "P244", "P227", "P213", "P268", "P8189"]


def get_items_with_dup_claims(prop: str) -> list[dict]:
    """Find items that have duplicate values for a property via SPARQL."""
    headers = {
        "User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)",
        "Accept": "application/sparql-results+json",
    }
    # Find items with >1 value for this property that were targets of our merges
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
            SPARQL, params={"query": query, "format": "json"},
            headers=headers, timeout=60,
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


def remove_duplicate_claims(session: requests.Session, csrf: str, qid: str, prop: str) -> int:
    """Remove duplicate claims for a property on an item, keeping the first one."""
    # Get the item's claims
    resp = session.get(API, params={
        "action": "wbgetclaims", "entity": qid, "property": prop, "format": "json",
    })
    data = resp.json()
    claims = data.get("claims", {}).get(prop, [])

    if len(claims) <= 1:
        return 0

    # Group by value — find true duplicates (same value, different claim IDs)
    seen_values = {}
    to_remove = []

    for claim in claims:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        value = datavalue.get("value", "")
        if isinstance(value, dict):
            value = json.dumps(value, sort_keys=True)

        if value in seen_values:
            # Duplicate — remove this one (keep the first)
            to_remove.append(claim["id"])
        else:
            seen_values[value] = claim["id"]

    if not to_remove:
        return 0

    # Remove duplicate claims
    for claim_id in to_remove:
        resp = session.post(API, data={
            "action": "wbremoveclaims",
            "claim": claim_id,
            "token": csrf,
            "summary": "Removing duplicate claim created by merge (MHM Pipeline cleanup)",
            "format": "json",
        })
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

    token = sys.argv[1]

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    session.headers["User-Agent"] = "MHMPipeline/1.0 (shvedbook@gmail.com)"

    # Get CSRF token
    csrf = session.get(API, params={
        "action": "query", "meta": "tokens", "format": "json",
    }).json()["query"]["tokens"]["csrftoken"]

    total_removed = 0

    for prop in SINGLE_VALUE_PROPS:
        print(f"\n=== Checking {prop} for duplicate claims ===")
        items = get_items_with_dup_claims(prop)
        print(f"  Found {len(items)} items with duplicate {prop}")

        for i, item in enumerate(items):
            qid = item["qid"]
            if not is_our_item(qid):
                print(f"  [{i+1}/{len(items)}] {qid} — SKIPPED (not our item)")
                continue
            print(f"  [{i+1}/{len(items)}] {qid} ({item['count']} values)...", end=" ", flush=True)

            try:
                removed = remove_duplicate_claims(session, csrf, qid, prop)
                if removed:
                    print(f"removed {removed}")
                    total_removed += removed
                else:
                    print("no true dups")
            except Exception as e:
                print(f"ERROR: {e}")

            time.sleep(1)

            if (i + 1) % 50 == 0:
                csrf = session.get(API, params={
                    "action": "query", "meta": "tokens", "format": "json",
                }).json()["query"]["tokens"]["csrftoken"]

        time.sleep(3)

    print(f"\n=== DONE: Removed {total_removed} duplicate claims ===")


if __name__ == "__main__":
    main()
