"""Find additional wrong merges among the 1,359 unflagged merge targets.

The original heuristic only flagged items with conflicting GND/DOB/POB after
merge — it misses non-person merges (bands, places, organizations).

This script:
1. Loads the 1,359 unflagged merge targets from /tmp/items_to_revert.json
2. Batches wbgetentities calls (50 QIDs per call)
3. Flags items where ANY of these is true:
   - Multiple P31 (instance of) values from clearly different domains
   - Multiple P17 (country) values
   - Multiple P569 (DOB), P570 (DOD), P19 (POB), or P20 (POD) values
   - Multiple P495 (country of origin) or P740 (location of formation)
4. Writes results to /tmp/extra_bad_merges.json (compatible with revert script)

Read-only — no modifications.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/find_more_bad_merges.py
"""

from __future__ import annotations

import json
import time

import requests

API = "https://www.wikidata.org/w/api.php"
SUSPECT_PROPS = ("P31", "P17", "P569", "P570", "P19", "P20", "P495", "P740")


def _retry_get(s, **kwargs):
    last_exc = None
    for attempt in range(6):
        try:
            return s.get(API, timeout=20, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            time.sleep(min(2**attempt, 30))
    raise last_exc


def get_value(claim: dict) -> str | None:
    """Extract a comparable string value from a claim's mainsnak."""
    snak = claim.get("mainsnak", {})
    if snak.get("snaktype") != "value":
        return None
    val = snak.get("datavalue", {}).get("value")
    if isinstance(val, dict):
        if "id" in val:
            return val["id"]
        if "time" in val:
            return val["time"][:11]  # date precision
        return json.dumps(val, sort_keys=True)
    return str(val)


def main() -> None:
    s = requests.Session()
    s.headers["User-Agent"] = "MHMPipeline/1.0 (shvedbook@gmail.com)"

    bad_targets = {b["qid"] for b in json.load(open("/tmp/bad_merge_targets.json"))}
    all_mods = json.load(open("/tmp/items_to_revert.json"))

    # Unflagged merges: have source AND not in bad_targets
    unflagged_qids = sorted({m["qid"] for m in all_mods if m.get("source") and m["qid"] not in bad_targets})
    print(f"Checking {len(unflagged_qids)} unflagged merge targets...")

    flagged = []
    for batch_start in range(0, len(unflagged_qids), 50):
        batch = unflagged_qids[batch_start : batch_start + 50]
        print(f"  Batch {batch_start // 50 + 1}/{(len(unflagged_qids) + 49) // 50}: {len(batch)} items...", flush=True)
        try:
            r = _retry_get(
                s,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "claims",
                    "format": "json",
                },
            ).json()
        except Exception as e:
            print(f"    ERR: {e}")
            continue

        entities = r.get("entities", {})
        for qid in batch:
            ent = entities.get(qid, {})
            if ent.get("missing") is not None or "redirects" in ent:
                continue
            claims = ent.get("claims", {})
            issues: dict[str, list[str]] = {}
            for prop in SUSPECT_PROPS:
                values = []
                for c in claims.get(prop, []):
                    v = get_value(c)
                    if v:
                        values.append(v)
                unique = sorted(set(values))
                if len(unique) >= 2:
                    issues[prop] = unique
            if issues:
                flagged.append({"qid": qid, "issues": issues})
        time.sleep(0.5)

    print(f"\nFound {len(flagged)} additional likely wrong merges.")

    # Save
    with open("/tmp/extra_bad_merges.json", "w") as f:
        json.dump(flagged, f, indent=2, ensure_ascii=False)
    print("Wrote /tmp/extra_bad_merges.json")

    # Show sample
    print("\nSample (first 10):")
    for f in flagged[:10]:
        props = ", ".join(f"{p}={len(vs)}" for p, vs in f["issues"].items())
        print(f"  {f['qid']}: {props}")


if __name__ == "__main__":
    main()
