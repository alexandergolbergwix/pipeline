"""One-off cleanup: remove generic "קובץ"-style Hebrew labels from items I
created (Geagea complaint, 2026-04-15). Per CLAUDE.md rule 25, this script
WILL NOT run against production Wikidata unless MORATORIUM_LIFTED=true is
set in the environment.

Strategy per item:
  1. Apply the standard 3-rule safety check (creator=me, latest=me, undo
     only my revids) via scripts/lib/wikidata_safety.is_safe_to_revert.
  2. Read the current Hebrew label.
  3. If still "קובץ." / "קבץ" / short-topical-קובץ → REMOVE the Hebrew
     label entirely AND copy the original string to the Hebrew aliases.
     Wikidata clients then fall back to the English label which already
     reads "Jerusalem, NLI, <shelfmark>".
  4. If the label has been corrected by a community editor, SKIP.

Reads /tmp/generic_kovetz_labels.json (produced by the audit at
/tmp/generic_kovetz_labels.json earlier in this incident).

Usage (after the moratorium is lifted by the env flag):
    MORATORIUM_LIFTED=true \\
    PYTHONPATH=src:. .venv/bin/python scripts/cleanup_generic_kovetz_labels.py <bearer_token>
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from scripts.lib.wikidata_safety import (
    RetryingSession,
    get_authenticated_user,
    get_csrf_token,
    is_safe_to_revert,
)

INPUT = Path("/tmp/generic_kovetz_labels.json")
LOG = Path("/tmp/cleanup_kovetz.log")


def _is_still_kovetz(label: str | None) -> bool:
    if not label:
        return False
    cleaned = label.strip().rstrip(".,;:")
    if cleaned in {"קובץ", "קבץ"}:
        return True
    if cleaned.startswith(("קובץ ", "קבץ ")) and len(cleaned) <= 25:
        return True
    return False


def main() -> None:
    if os.environ.get("MORATORIUM_LIFTED", "").lower() != "true":
        sys.stderr.write(
            "MORATORIUM IN EFFECT — set MORATORIUM_LIFTED=true to run.\n"
            "See CLAUDE.md rule 25 for the conditions that must hold first.\n"
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

    if not INPUT.exists():
        sys.stderr.write(f"ERROR: input {INPUT} not found.\n")
        sys.exit(1)
    items = json.loads(INPUT.read_text())
    print(f"Loaded {len(items)} candidate items from {INPUT}\n")

    ok = skip = fail = 0
    for i, entry in enumerate(items):
        qid = entry["qid"]
        recorded_label = entry.get("label", "")
        print(f"[{i + 1}/{len(items)}] {qid} (recorded={recorded_label!r})...", end=" ", flush=True)

        # Safety: skip items I didn't create OR where someone else has
        # touched the item since.
        try:
            safe, reason = is_safe_to_revert(s, qid, auth_user)
        except Exception as e:
            print(f"ERR safety: {e}")
            fail += 1
            continue
        if not safe:
            print(f"SKIP ({reason})")
            skip += 1
            continue

        # Verify the label is STILL kovetz-style (community may have fixed it).
        try:
            r = s.get(
                params={
                    "action": "wbgetentities",
                    "ids": qid,
                    "props": "labels|aliases",
                    "languages": "he",
                    "format": "json",
                },
            ).json()
        except Exception as e:
            print(f"ERR fetch: {e}")
            fail += 1
            continue
        ent = r.get("entities", {}).get(qid, {})
        current_label = (ent.get("labels", {}) or {}).get("he", {}).get("value", "")
        if not _is_still_kovetz(current_label):
            print(f"SKIP (label has changed: {current_label!r})")
            skip += 1
            continue

        # Add the placeholder string to aliases (preserving searchability)
        # then remove the Hebrew label. Two API calls, both idempotent.
        try:
            res_alias = s.post(
                data={
                    "action": "wbsetaliases",
                    "id": qid,
                    "language": "he",
                    "add": current_label,
                    "token": csrf,
                    "summary": (
                        "MHM Pipeline cleanup: moving generic catalog placeholder "
                        "to alias before removing as label (per Geagea complaint, "
                        "2026-04-15)"
                    ),
                    "bot": "1",
                    "format": "json",
                },
            ).json()
            if "error" in res_alias:
                code = res_alias["error"].get("code", "")
                if "no-change" not in code:
                    print(f"FAIL alias: {res_alias['error'].get('info', '')[:60]}")
                    fail += 1
                    continue
            res_label = s.post(
                data={
                    "action": "wbsetlabel",
                    "id": qid,
                    "language": "he",
                    "value": "",  # empty value removes the label
                    "token": csrf,
                    "summary": (
                        "MHM Pipeline cleanup: removing generic Hebrew label "
                        "(per Geagea complaint, 2026-04-15). Original string "
                        "preserved in he aliases."
                    ),
                    "bot": "1",
                    "format": "json",
                },
            ).json()
            if "error" in res_label:
                print(f"FAIL label remove: {res_label['error'].get('info', '')[:60]}")
                fail += 1
                continue
            print("OK")
            ok += 1
        except Exception as e:
            print(f"ERR write: {e}")
            fail += 1
            continue

        time.sleep(0.7)
        if (i + 1) % 50 == 0:
            csrf = get_csrf_token(s)

    print(f"\nDONE: {ok} cleaned, {skip} skipped, {fail} failed")


if __name__ == "__main__":
    main()
