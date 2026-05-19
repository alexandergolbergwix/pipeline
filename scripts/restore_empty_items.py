"""Dedicated parallel restorer for the 1,805 items left empty by the first
(broken) revert script.

Reads:
  /tmp/first_script_audit.json  — the audit's `empty` list
  /tmp/all_my_contribs.json     — to find my wbmergeitems-to revid per QID

For each empty QID:
  1. Look up my wbmergeitems-to revid (the emptying edit)
  2. Apply the SAME three safety rules as scripts/lib/wikidata_safety
  3. Re-check latest editor at edit time (defeat race vs. live merges script)
  4. Issue action=edit&undo=<my_wbmergeitems-to_revid>
  5. Wikidata's invariant restores content + clears any leftover redirect

Coordinates with the live merges-only script via:
  - Independent checkpoint at /tmp/restore_empty_checkpoint.json
  - Wikidata's own idempotency: if the live script already restored an item,
    our undo returns "newer than" → SKIP (counted, not failed).
  - maxlag=5 — backs off if Wikidata's replication lag rises so we don't
    contribute to it.

Auto-verification every 50 restores; auto-halt if >5% empty after restore.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/restore_empty_items.py <bearer_token>

Optional flags:
    --dry-run     print plan without writing
    --max N       stop after N restores
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from scripts.lib.wikidata_safety import (
    RetryingSession,
    get_authenticated_user,
    get_csrf_token,
    get_first_revision_author,
    get_latest_revision_author,
)

CHECKPOINT = Path("/tmp/restore_empty_checkpoint.json")
AUDIT = Path("/tmp/first_script_audit.json")
CONTRIBS_CACHE = Path("/tmp/all_my_contribs.json")
LOG_FILE = Path("/tmp/restore_empty.log")
EDIT_SLEEP = 0.7  # seconds between writes
MAXLAG = 5  # seconds — Wikidata will return error if replication lag exceeds this


def load_checkpoint() -> set[int]:
    if CHECKPOINT.exists():
        return set(json.loads(CHECKPOINT.read_text()))
    return set()


def save_checkpoint(done: set[int]) -> None:
    CHECKPOINT.write_text(json.dumps(sorted(done)))


def verify_items_not_empty(s: RetryingSession, qids: list[str]) -> tuple[int, list[str]]:
    """Returns (healthy_count, empty_qids) for the given QIDs."""
    if not qids:
        return 0, []
    empty: list[str] = []
    healthy = 0
    for batch_start in range(0, len(qids), 50):
        batch = qids[batch_start : batch_start + 50]
        try:
            r = s.get(
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "labels|descriptions|claims|info",
                    "format": "json",
                },
            ).json()
        except Exception:
            continue
        for qid, ent in (r.get("entities") or {}).items():
            if "redirects" in ent or ent.get("missing") is not None:
                continue
            n_labels = len(ent.get("labels", {}) or {})
            n_desc = len(ent.get("descriptions", {}) or {})
            n_claims = sum(len(v) for v in (ent.get("claims") or {}).values())
            if n_labels + n_desc + n_claims == 0:
                empty.append(qid)
            else:
                healthy += 1
    return healthy, empty


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

    if not AUDIT.exists():
        print(f"ERROR: {AUDIT} not found. Run the first-script audit first.")
        sys.exit(2)
    if not CONTRIBS_CACHE.exists():
        print(f"ERROR: {CONTRIBS_CACHE} not found.")
        sys.exit(2)

    s = RetryingSession(bearer_token=token)
    csrf = get_csrf_token(s)
    auth_user = get_authenticated_user(s)
    print(f"Authenticated as: {auth_user}")
    print("Target: empty items left by the first (broken) revert script")
    print(f"maxlag = {MAXLAG}s; sleep between edits = {EDIT_SLEEP}s")
    if dry_run:
        print("DRY RUN — no edits will be made")

    audit = json.loads(AUDIT.read_text())
    empty_qids = sorted(set(audit.get("empty", [])))
    print(f"\nEmpty QIDs to restore: {len(empty_qids)}")

    contribs = json.loads(CONTRIBS_CACHE.read_text())
    # Index: qid -> newest wbmergeitems-to revid
    emptying: dict[str, dict] = {}
    for c in contribs:
        if "wbmergeitems-to" not in (c.get("comment") or ""):
            continue
        qid = c["qid"]
        if qid not in emptying or c["revid"] > emptying[qid]["revid"]:
            emptying[qid] = c

    plan: list[dict] = []
    no_revid: list[str] = []
    for qid in empty_qids:
        if qid in emptying:
            plan.append({**emptying[qid], "_kind": "emptying"})
        else:
            no_revid.append(qid)
    print(f"  with wbmergeitems-to revid: {len(plan)}")
    print(f"  without (cannot restore):   {len(no_revid)}")
    if no_revid:
        print(f"    sample: {no_revid[:5]}")

    done = load_checkpoint()
    if done:
        print(f"\nResuming from checkpoint: {len(done)} revids already processed")
    pending = [p for p in plan if p["revid"] not in done]
    pending.sort(key=lambda c: -c["revid"])  # newest first
    print(f"To process this run: {len(pending)} items\n")

    if not pending:
        print("Nothing to do.")
        return

    ok = skip = fail = 0
    consecutive_fail = 0
    start_time = time.time()
    recent_reverted: list[str] = []
    total_empty_after: list[str] = []

    for i, p in enumerate(pending):
        qid = p["qid"]
        my_revid = p["revid"]
        print(f"[{i + 1}/{len(pending)}] {qid} (rev {my_revid})...", end=" ", flush=True)

        # Rule 1: creator must NOT be me
        try:
            creator = get_first_revision_author(s, qid)
        except Exception as e:
            print(f"ERR creator: {e}")
            fail += 1
            consecutive_fail += 1
            if consecutive_fail >= 10:
                print("\nCIRCUIT BREAKER: 10 consecutive failures, halting")
                break
            continue
        if not creator:
            print("SKIP (no creator)")
            skip += 1
            done.add(my_revid)
            continue
        if creator == auth_user:
            print("SKIP (I created this item)")
            skip += 1
            done.add(my_revid)
            continue

        # Rule 2: latest editor MUST be me — re-check at edit time
        try:
            latest = get_latest_revision_author(s, qid)
        except Exception as e:
            print(f"ERR latest: {e}")
            fail += 1
            consecutive_fail += 1
            continue
        if not latest:
            print("SKIP (no latest)")
            skip += 1
            done.add(my_revid)
            continue
        if latest != auth_user:
            print(f"SKIP (latest is '{latest}' — refusing to override)")
            skip += 1
            done.add(my_revid)
            continue

        # Rule 3: built-in (revid sourced from MY contribs)

        if dry_run:
            print("WOULD RESTORE")
            ok += 1
            consecutive_fail = 0
            done.add(my_revid)
            if max_reverts is not None and ok >= max_reverts:
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
                        "(per Epìdosis: each merge produces an empty + a redirect "
                        "edit; this undoes the empty edit, Wikidata clears the "
                        "redirect automatically)"
                    ),
                    "maxlag": str(MAXLAG),
                    "format": "json",
                },
            ).json()
            if res.get("edit", {}).get("result") == "Success":
                print("RESTORED")
                ok += 1
                consecutive_fail = 0
                done.add(my_revid)
                recent_reverted.append(qid)
            elif "error" in res:
                err = res["error"].get("info", "")
                code = res["error"].get("code", "")
                if code == "maxlag":
                    print("MAXLAG — sleeping 10s")
                    time.sleep(10)
                    continue
                if "undofailure" in code or "newer than" in err.lower():
                    print("SKIP (already restored / superseded)")
                    skip += 1
                    done.add(my_revid)
                else:
                    print(f"FAIL: {code} — {err[:60]}")
                    fail += 1
                    consecutive_fail += 1
            else:
                print(f"???: {str(res)[:80]}")
                fail += 1
                consecutive_fail += 1
        except Exception as e:
            print(f"ERR: {e}")
            fail += 1
            consecutive_fail += 1

        if consecutive_fail >= 10:
            print("\nCIRCUIT BREAKER: 10 consecutive failures, halting")
            save_checkpoint(done)
            break

        time.sleep(EDIT_SLEEP)
        if (i + 1) % 50 == 0:
            csrf = get_csrf_token(s)
            save_checkpoint(done)
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta_min = (len(pending) - i - 1) / rate / 60 if rate > 0 else 0
            unique_recent = list(dict.fromkeys(recent_reverted))
            healthy, empty_now = verify_items_not_empty(s, unique_recent)
            print(
                f"  -- checkpoint @ {i + 1}/{len(pending)}: "
                f"{ok} ok, {skip} skip, {fail} fail | "
                f"verified {healthy}/{len(unique_recent)} healthy, "
                f"{len(empty_now)} still empty | "
                f"{rate:.1f}/s, ETA {eta_min:.0f} min --",
                flush=True,
            )
            if empty_now:
                total_empty_after.extend(empty_now)
                print(f"  ⚠ Still empty after restore: {empty_now[:10]}")
                if len(empty_now) > max(2, len(unique_recent) // 20):
                    print("\nHALT: too many still-empty items in batch")
                    save_checkpoint(done)
                    break
            recent_reverted.clear()
        if max_reverts is not None and ok >= max_reverts:
            print(f"\nStopping at --max {max_reverts}")
            break

    save_checkpoint(done)
    if recent_reverted and not dry_run:
        unique_recent = list(dict.fromkeys(recent_reverted))
        healthy, empty_now = verify_items_not_empty(s, unique_recent)
        print(
            f"\nFinal verification: {healthy}/{len(unique_recent)} healthy, {len(empty_now)} empty"
        )
        if empty_now:
            total_empty_after.extend(empty_now)
            print(f"  Empty: {empty_now[:20]}")
    print(f"\nDONE: {ok} ok, {skip} skipped, {fail} failed")
    if total_empty_after:
        print(f"⚠ {len(total_empty_after)} items still empty after restore — investigate")
    else:
        print("✓ No empty items detected after any restore.")


if __name__ == "__main__":
    main()
