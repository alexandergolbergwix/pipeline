"""Second-pass revert: restore CONTENT to source items I emptied during merges.

When wbmergeitems is called, the source item gets two consecutive edits:
  1. wbmergeitems-to  — empties content (moves it to the target)
  2. wbcreateredirect — redirects the now-empty source to the target

The first revert pass (revert_all_my_modifications_to_others.py) only undid
edit #2 — leaving pages empty (Epìdosis flagged this on Q139096947, 2026-04-13).

This script undoes edit #1 — the emptying edit. Restoring the content also
forces Wikidata to clear any redirect status (an item cannot be both
contentful and a redirect), so a single undo here finishes the job whether
the redirect was already cleared or not.

Three safety rules (same as scripts/lib/wikidata_safety.is_safe_to_revert):
  1. First revision author of the item is NOT me.
  2. Latest revision of the item IS me.
  3. The revid being undone is one of MY OWN revisions.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/revert_emptying_edits.py <bearer_token>
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

CHECKPOINT = Path("/tmp/revert_emptying_checkpoint.json")
CONTRIBS_CACHE = Path("/tmp/all_my_contribs.json")
EDIT_SLEEP = 0.7
LOOKUP_WORKERS = 8
PREFETCH_AHEAD = 64


def lookup_safety(s: RetryingSession, qid: str) -> tuple[str, str]:
    try:
        creator = get_first_revision_author(s, qid)
    except Exception:
        creator = ""
    try:
        latest = get_latest_revision_author(s, qid)
    except Exception:
        latest = ""
    return creator, latest


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
    dry_run = "--dry-run" in args
    max_reverts: int | None = None
    if "--max" in args:
        max_reverts = int(args[args.index("--max") + 1])

    s = RetryingSession(bearer_token=token)
    csrf = get_csrf_token(s)
    auth_user = get_authenticated_user(s)
    print(f"Authenticated as: {auth_user}")
    if dry_run:
        print("DRY RUN — no edits will be made")

    if not CONTRIBS_CACHE.exists():
        print(
            f"ERROR: {CONTRIBS_CACHE} not found. Run "
            "scripts/revert_all_my_modifications_to_others.py first to fetch contribs."
        )
        sys.exit(1)
    contribs = json.loads(CONTRIBS_CACHE.read_text())

    # Filter to ONLY my wbmergeitems-to edits (the emptying ones)
    emptying = [c for c in contribs if "wbmergeitems-to" in (c.get("comment") or "")]
    print(f"\nTotal contributions in cache: {len(contribs)}")
    print(f"Of which 'wbmergeitems-to' (emptying) edits: {len(emptying)}")

    # If a QID has multiple emptying edits, we want the NEWEST one
    # (so undoing that brings us back to before the most-recent merge of mine)
    by_qid: dict[str, dict] = {}
    for c in emptying:
        if c["qid"] not in by_qid or c["revid"] > by_qid[c["qid"]]["revid"]:
            by_qid[c["qid"]] = c
    print(f"Distinct QIDs to process: {len(by_qid)}")

    done = load_checkpoint()
    if done:
        print(f"Resuming from checkpoint: {len(done)} revids already processed")

    targets = [c for c in by_qid.values() if c["revid"] not in done]
    targets.sort(key=lambda c: -c["revid"])  # newest first
    print(f"To process this run: {len(targets)} items (lookup workers={LOOKUP_WORKERS})\n")

    # Concurrent prefetch of safety info
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

    schedule_prefetch(0, PREFETCH_AHEAD)

    ok = skip = fail = 0
    for i, mod in enumerate(targets):
        qid = mod["qid"]
        my_revid = mod["revid"]
        print(f"[{i + 1}/{len(targets)}] {qid} (rev {my_revid})...", end=" ", flush=True)

        if qid not in safety_cache:
            safety_cache[qid] = lookup_safety(s, qid)
        creator, latest = safety_cache[qid]

        if i + PREFETCH_AHEAD // 2 < len(targets) and (i % (PREFETCH_AHEAD // 2)) == 0:
            schedule_prefetch(i + PREFETCH_AHEAD // 2, PREFETCH_AHEAD // 2)

        # Rule 1
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
        # Rule 2
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

        if dry_run:
            print("WOULD REVERT (emptying edit)")
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
                        "Restoring content emptied by automated merge "
                        "(per Epìdosis: each merge needs two undos; this is the "
                        "second of the pair, restoring the source item's claims)"
                    ),
                    "format": "json",
                },
            ).json()
            if res.get("edit", {}).get("result") == "Success":
                print("RESTORED")
                ok += 1
                done.add(my_revid)
            elif "error" in res:
                err = res["error"].get("info", "")
                code = res["error"].get("code", "")
                if "undofailure" in code or "newer than" in err.lower():
                    print("SKIP (already restored / superseded)")
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
    print(f"\nDONE: {ok} restored, {skip} skipped, {fail} failed")
    print(f"Checkpoint: {CHECKPOINT}")


if __name__ == "__main__":
    main()
