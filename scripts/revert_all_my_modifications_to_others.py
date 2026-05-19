"""End-to-end revert: find EVERY modification I have ever made on items I did
not create, and undo each one — but only when I am still the latest editor.

This script does the audit AND the revert in one pass, so we are always
working from live Wikidata state (no stale /tmp/items_to_revert.json).

Three rules, enforced via scripts/lib/wikidata_safety.is_safe_to_revert():

  1. Item's first revision author is NOT me — otherwise it is my own item,
     nothing to revert.
  2. Item's most recent revision IS me — otherwise someone else (e.g.,
     Epìdosis re-applying a merge) has touched the item since my edit, and
     undoing my older revision would silently override their correction.
  3. Only my own revisions are ever undone (action=edit&undo=<my_revid>).

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/revert_all_my_modifications_to_others.py <bearer_token>

Optional flags (after the token):
    --since 2026-04-01T00:00:00Z   only consider edits after this timestamp
    --dry-run                      print the plan but do not edit anything
    --max 100                      stop after this many reverts
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.lib.wikidata_safety import (
    RetryingSession,
    get_authenticated_user,
    get_csrf_token,
    get_first_revision_author,
    get_latest_revision_author,
)

CHECKPOINT = Path("/tmp/revert_all_checkpoint.json")
CONTRIBS_CACHE = Path("/tmp/all_my_contribs.json")
EDIT_SLEEP = 0.7  # seconds between writes; highvolume grant allows ~1.4/s
LOOKUP_WORKERS = 8  # concurrent threads for read-only safety lookups
PREFETCH_AHEAD = 64  # how many items ahead to prefetch safety info


def lookup_safety(s: RetryingSession, qid: str) -> tuple[str, str]:
    """Per-item (creator, latest_editor) lookup. Defaults to ('', '') on failure."""
    try:
        creator = get_first_revision_author(s, qid)
    except Exception:
        creator = ""
    try:
        latest = get_latest_revision_author(s, qid)
    except Exception:
        latest = ""
    return creator, latest


def fetch_all_my_contribs(s: RetryingSession, auth_user: str, since: str | None) -> list[dict]:
    """Fetch every contribution I have ever made on Q-items.

    Returns a list of {qid, revid, ts, comment, parentid} dicts, newest first.
    """
    contribs: list[dict] = []
    uccontinue: str | None = None
    page = 0
    while True:
        page += 1
        params: dict[str, str] = {
            "action": "query",
            "list": "usercontribs",
            "ucuser": auth_user,
            "ucnamespace": "0",
            "ucprop": "title|ids|timestamp|comment",
            "uclimit": "500",
            "ucdir": "older",
            "format": "json",
        }
        if since:
            params["ucend"] = since  # ucdir=older means stop AT this date
        if uccontinue:
            params["uccontinue"] = uccontinue
        resp = s.get(params=params).json()
        batch = resp.get("query", {}).get("usercontribs", [])
        for c in batch:
            title = c.get("title", "")
            if not title.startswith("Q"):
                continue
            contribs.append(
                {
                    "qid": title,
                    "revid": c.get("revid"),
                    "parentid": c.get("parentid"),
                    "ts": c.get("timestamp", ""),
                    "comment": c.get("comment", ""),
                }
            )
        cont = resp.get("continue", {})
        uccontinue = cont.get("uccontinue")
        print(f"  page {page}: +{len(batch)} contribs, total {len(contribs)}", flush=True)
        if not uccontinue:
            break
        time.sleep(0.5)
    return contribs


def load_checkpoint() -> set[int]:
    if CHECKPOINT.exists():
        return set(json.loads(CHECKPOINT.read_text()))
    return set()


def save_checkpoint(done: set[int]) -> None:
    CHECKPOINT.write_text(json.dumps(sorted(done)))


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    token = sys.argv[1]
    args = sys.argv[2:]
    since = None
    dry_run = "--dry-run" in args
    max_reverts: int | None = None
    if "--since" in args:
        since = args[args.index("--since") + 1]
    if "--max" in args:
        max_reverts = int(args[args.index("--max") + 1])

    s = RetryingSession(bearer_token=token)
    csrf = get_csrf_token(s)
    auth_user = get_authenticated_user(s)
    print(f"Authenticated as: {auth_user}")
    if dry_run:
        print("DRY RUN — no edits will be made")
    if since:
        print(f"Only considering edits since {since}")

    # Step 1: contribs (cache in /tmp so re-runs are fast)
    if CONTRIBS_CACHE.exists():
        print(f"\nUsing cached contribs from {CONTRIBS_CACHE}")
        contribs = json.loads(CONTRIBS_CACHE.read_text())
    else:
        print("\nFetching all my contributions on Q-items...")
        contribs = fetch_all_my_contribs(s, auth_user, since)
        CONTRIBS_CACHE.write_text(json.dumps(contribs))
    print(f"Total contributions on Q-items: {len(contribs)}")

    # Step 2: dedupe by qid, keep the NEWEST revision per qid (that's what
    # action=edit&undo=<revid> needs to undo)
    by_qid: dict[str, dict] = {}
    for c in contribs:
        if c["qid"] not in by_qid or c["revid"] > by_qid[c["qid"]]["revid"]:
            by_qid[c["qid"]] = c
    print(f"Distinct items I have ever touched: {len(by_qid)}")

    # Step 3: revert chain with all three safety rules
    done = load_checkpoint()
    if done:
        print(f"Resuming from checkpoint: {len(done)} items already processed")

    targets = [c for c in by_qid.values() if c["revid"] not in done]
    targets.sort(key=lambda c: -c["revid"])  # newest first
    print(f"To process: {len(targets)} items (lookup workers={LOOKUP_WORKERS})\n")

    # Prefetch safety info concurrently. Each worker uses its own session so
    # network calls do not serialize on a shared connection.
    worker_sessions = [RetryingSession(bearer_token=token) for _ in range(LOOKUP_WORKERS)]

    def prefetch_one(idx_qid: tuple[int, str]) -> tuple[str, tuple[str, str]]:
        idx, qid = idx_qid
        ws = worker_sessions[idx % LOOKUP_WORKERS]
        return qid, lookup_safety(ws, qid)

    pool = ThreadPoolExecutor(max_workers=LOOKUP_WORKERS)
    safety_cache: dict[str, tuple[str, str]] = {}

    def schedule_prefetch(start: int, count: int) -> None:
        items = [
            (start + j, targets[start + j]["qid"]) for j in range(count) if start + j < len(targets)
        ]
        for fut in pool.map(prefetch_one, items):
            qid, info = fut
            safety_cache[qid] = info

    # Initial prefetch window
    schedule_prefetch(0, PREFETCH_AHEAD)

    ok = skip = fail = 0
    for i, mod in enumerate(targets):
        qid = mod["qid"]
        my_revid = mod["revid"]
        print(f"[{i + 1}/{len(targets)}] {qid} (rev {my_revid})...", end=" ", flush=True)

        # Get safety info from cache; if missing, fetch on demand
        if qid not in safety_cache:
            safety_cache[qid] = lookup_safety(s, qid)
        creator, latest = safety_cache[qid]

        # Top up prefetch window when we are PREFETCH_AHEAD/2 behind the front
        if i + PREFETCH_AHEAD // 2 < len(targets) and (i % (PREFETCH_AHEAD // 2)) == 0:
            schedule_prefetch(i + PREFETCH_AHEAD // 2, PREFETCH_AHEAD // 2)

        # Rule 1: creator must NOT be me
        if not creator:
            print("SKIP (could not determine creator)")
            skip += 1
            done.add(my_revid)
            continue
        if creator == auth_user:
            print("SKIP (I created this item)")
            skip += 1
            done.add(my_revid)
            continue

        # Rule 2: latest editor MUST be me
        if not latest:
            print("SKIP (could not determine latest editor)")
            skip += 1
            done.add(my_revid)
            continue
        if latest != auth_user:
            print(f"SKIP (latest is '{latest}', not me — refuse to override)")
            skip += 1
            done.add(my_revid)
            continue

        # Rule 3: only ever undo my own revid (this IS my revid by construction)
        if dry_run:
            print("WOULD REVERT")
            ok += 1
            done.add(my_revid)
            if max_reverts is not None and ok >= max_reverts:
                print(f"\nStopping at --max {max_reverts}")
                break
            continue

        try:
            res = s.post(
                data={
                    "action": "edit",
                    "title": qid,
                    "undo": my_revid,
                    "token": csrf,
                    "summary": (
                        "Reverting modification on item not created by me "
                        "(automated cleanup, see User talk:Alexander Goldberg IL)"
                    ),
                    "format": "json",
                },
            ).json()
            if res.get("edit", {}).get("result") == "Success":
                print("REVERTED")
                ok += 1
                done.add(my_revid)
            elif "error" in res:
                err = res["error"].get("info", "")
                code = res["error"].get("code", "")
                if "undofailure" in code or "newer than" in err.lower():
                    print("SKIP (already reverted/changed)")
                    skip += 1
                    done.add(my_revid)
                else:
                    print(f"FAIL: {err[:80]}")
                    fail += 1
            else:
                print(f"???: {res}")
                fail += 1
        except Exception as e:
            print(f"ERR: {e}")
            fail += 1

        time.sleep(EDIT_SLEEP)
        if (i + 1) % 50 == 0:
            csrf = get_csrf_token(s)
            save_checkpoint(done)
        if max_reverts is not None and ok >= max_reverts:
            print(f"\nStopping at --max {max_reverts}")
            break

    save_checkpoint(done)
    print(f"\nDONE: {ok} reverted, {skip} skipped, {fail} failed")
    print(f"Checkpoint: {CHECKPOINT}")
    print(f"Contribs cache: {CONTRIBS_CACHE} (delete to refresh on next run)")


if __name__ == "__main__":
    main()
