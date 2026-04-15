"""One-off cleanup for the two items where I still have a P3959 (NNL item ID
- bibliographic) statement that should not be there.

Geagea (2026-04-15) cleaned 100+ similar P3959-on-person items via his
batch script; only Q139159451 and Q139328025 remain in my range.

Per CLAUDE.md rule 25, this script WILL NOT run against production
Wikidata unless MORATORIUM_LIFTED=true is set in the environment.

Strategy:
  1. For each QID, fetch all P3959 claim IDs.
  2. If the value starts with '99' (bibliographic) → remove the claim.
  3. If the value starts with '9870' (authority record), the wrong
     property is in use — also remove (the user can re-add as P8189
     manually if appropriate).

Usage (after the moratorium is lifted by the env flag):
    MORATORIUM_LIFTED=true \\
    PYTHONPATH=src:. .venv/bin/python scripts/fix_p3959_residual.py <bearer_token>
"""

from __future__ import annotations

import os
import sys

from scripts.lib.wikidata_safety import (
    RetryingSession,
    get_authenticated_user,
    get_csrf_token,
    is_safe_to_revert,
)

TARGET_QIDS = ["Q139159451", "Q139328025"]


def main() -> None:
    if os.environ.get("MORATORIUM_LIFTED", "").lower() != "true":
        sys.stderr.write(
            "MORATORIUM IN EFFECT — set MORATORIUM_LIFTED=true to run.\nSee CLAUDE.md rule 25.\n"
        )
        sys.exit(2)
    if len(sys.argv) < 2:
        sys.stderr.write(__doc__)
        sys.exit(1)

    token = sys.argv[1]
    s = RetryingSession(bearer_token=token)
    csrf = get_csrf_token(s)
    auth_user = get_authenticated_user(s)
    print(f"Authenticated as: {auth_user}")

    for qid in TARGET_QIDS:
        print(f"\n=== {qid} ===")
        safe, reason = is_safe_to_revert(s, qid, auth_user)
        if not safe:
            print(f"  SKIP ({reason})")
            continue
        # Fetch P3959 claims
        r = s.get(
            params={
                "action": "wbgetclaims",
                "entity": qid,
                "property": "P3959",
                "format": "json",
            },
        ).json()
        claims = r.get("claims", {}).get("P3959", [])
        if not claims:
            print("  no P3959 claims (already cleaned by community?)")
            continue
        for c in claims:
            cid = c.get("id")
            val = c.get("mainsnak", {}).get("datavalue", {}).get("value", "")
            print(f"  removing claim {cid} (value={val})")
            res = s.post(
                data={
                    "action": "wbremoveclaims",
                    "claim": cid,
                    "token": csrf,
                    "summary": (
                        "MHM Pipeline cleanup: removing wrongly-attached P3959 "
                        "(NNL item ID is for bibliographic records, not for the "
                        "person items I created; per Geagea, 2026-04-15)"
                    ),
                    "bot": "1",
                    "format": "json",
                },
            ).json()
            if "error" in res:
                print(f"  FAIL: {res['error'].get('info', '')[:60]}")
            else:
                print("  OK")


if __name__ == "__main__":
    main()
