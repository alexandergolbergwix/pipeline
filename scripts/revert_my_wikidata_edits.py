"""Single canonical revert script — undoes EVERY MHM Pipeline modification on
items I did not create, with full safety guards.

The pipeline made two classes of changes on items I did not create:

  (A) Direct edits to existing items (label/desc updates, claim additions
      that conflicted with existing values, identity-property writes).
      Each one is a single revid in my contribution history.

  (B) Merges of existing items into other items. Each wbmergeitems call
      produces TWO consecutive edits on the SOURCE item:
        1. wbmergeitems-to    — empties the source's content
        2. wbcreateredirect   — turns the empty source into a redirect

      To restore the source we MUST undo edit (1). Undoing edit (2) only
      removes the redirect and leaves the page empty (Epìdosis flagged this
      on Q139096947, 2026-04-13). When we undo edit (1), Wikidata's
      invariant that an item cannot be both contentful and a redirect
      automatically clears any leftover redirect status.

This single script handles both classes:

  * For QIDs where I did a wbmergeitems-to edit (class B): undo that
    specific revid (NOT the wbcreateredirect revid).
  * For QIDs where I did NOT do a wbmergeitems-to edit (class A): undo my
    newest revid on the page.

Three safety rules per item, enforced in is_safe_to_revert():

  1. First-revision author of the item must NOT be me — otherwise it's my
     own item and there is nothing to revert.
  2. Latest revision of the item must BE me — otherwise someone else
     (e.g. Epìdosis re-applying a merge) has touched the item since my
     edit, and undoing my older revision would silently override their
     correction.
  3. Only revids attributed to me are ever passed to action=edit&undo.

Operational guards:

  * Resumable via /tmp/revert_my_wikidata_edits_checkpoint.json.
  * Contributions are cached in /tmp/all_my_contribs.json (delete to refresh).
  * Network errors retry with exponential backoff (RetryingSession, 6 attempts).
  * Concurrent prefetch of safety lookups (8 workers) saturates the
    serialised edit rate (1 edit per 0.7 s = ~85 edits/min, well within
    the 5000/hour bot grant).

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/revert_my_wikidata_edits.py <bearer_token>

Optional flags:
    --dry-run                       print planned reverts without writing
    --max N                         stop after N successful reverts
    --since 2026-04-12T00:00:00Z    only consider my edits after this time
    --refresh-contribs              ignore the contribs cache and re-fetch
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

CHECKPOINT = Path("/tmp/revert_my_wikidata_edits_checkpoint.json")
CONTRIBS_CACHE = Path("/tmp/all_my_contribs.json")
LOG_FILE = Path("/tmp/revert_my_wikidata_edits.log")
EDIT_SLEEP = 0.7  # seconds between writes; bot grant allows ~1.4/s
LOOKUP_WORKERS = 8
PREFETCH_AHEAD = 64


# ── Step 1: contributions ───────────────────────────────────────────────────


def fetch_all_my_contribs(s: RetryingSession, auth_user: str, since: str | None) -> list[dict]:
    """Fetch every contribution I have ever made on Q-items, newest first."""
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
            params["ucend"] = since
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
        time.sleep(0.3)
    return contribs


# ── Step 2: pick the right revid per QID ────────────────────────────────────

# Substrings that mark an edit as one of MY OWN prior revert / restoration
# actions. Such edits must NEVER be undone — undoing a revert re-applies the
# original wrong merge.
_REVERT_MARKERS = (
    "Reverting",
    "Restoring",
    "Undid revision",
    "wbcreateredirect",  # the redirect-creation edit; we never undo this
    # because undoing it leaves an empty page (Epìdosis 2026-04-13 fix)
)


def is_my_substantive_edit(comment: str) -> bool:
    """True iff this is one of my ORIGINAL pipeline edits, not a later cleanup.

    A pipeline edit has a wbmergeitems-* / wbeditentity / wbcreateclaim /
    wbsetlabel / wbsetclaim / wbsetdescription / wbsetaliases / wbremoveclaims
    style auto-comment, OR a custom MHM Pipeline summary that does NOT contain
    revert/restore/undo keywords.
    """
    if not comment:
        return False
    for marker in _REVERT_MARKERS:
        if marker in comment:
            return False
    return True


def classify(comment: str) -> str:
    """Return 'emptying' | 'merge-target' | 'direct' | None for a comment."""
    if not is_my_substantive_edit(comment):
        return ""
    if "wbmergeitems-to" in comment:
        return "emptying"
    if "wbmergeitems-from" in comment:
        return "merge-target"
    return "direct"


def plan_per_qid(contribs: list[dict], mode: str) -> dict[str, list[dict]]:
    """Build a per-QID plan: list of revids to undo, NEWEST FIRST.

    Wikidata's `undo` parameter applies the inverse of one specific revision.
    To fully undo N consecutive edits, we apply N undos in REVERSE
    chronological order (newest first) so that each subsequent undo's
    base state is what the previous undo produced.

    For 'emptying' kind: undoing the wbmergeitems-to revid restores the
    source item's content AND automatically clears the redirect status.
    Only ONE undo per QID is needed even if the source was the target of
    multiple subsequent merges (which is rare; a redirect cannot be merged).

    For 'merge-target' and 'direct' kinds: a QID may have several of my
    revids. We undo ALL of them, newest first, so the page returns to
    its state before my first touch.

    Modes:
      'merges' — only emptying + merge-target (safer first pass)
      'direct' — only direct edits
      'all'    — everything
    """
    per_qid: dict[str, list[dict]] = {}
    for c in contribs:
        qid = c["qid"]
        kind = classify(c.get("comment") or "")
        if not kind:
            continue
        if mode == "merges" and kind == "direct":
            continue
        if mode == "direct" and kind != "direct":
            continue
        per_qid.setdefault(qid, []).append({**c, "_kind": kind})

    # Sort each list newest-first (highest revid first)
    for qid in per_qid:
        per_qid[qid].sort(key=lambda c: -c["revid"])
    return per_qid


# ── Step 3: safety prefetch ─────────────────────────────────────────────────


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


# ── Step 4: checkpoint ──────────────────────────────────────────────────────


def load_checkpoint() -> set[int]:
    if CHECKPOINT.exists():
        return set(json.loads(CHECKPOINT.read_text()))
    return set()


def save_checkpoint(done: set[int]) -> None:
    CHECKPOINT.write_text(json.dumps(sorted(done)))


# ── Main ────────────────────────────────────────────────────────────────────


def verify_items_not_empty(s: RetryingSession, qids: list[str]) -> tuple[int, list[str]]:
    """Fetch a batch of items via wbgetentities and return (healthy_count, empty_qids).

    A QID is "empty" if AFTER our undo it has 0 labels + 0 descriptions + 0 claims.
    Followed redirects and missing pages are NOT counted as empty (they had no
    content to restore from a different cause).
    """
    if not qids:
        return 0, []
    empty: list[str] = []
    healthy = 0
    # wbgetentities accepts up to 50 ids per call
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
            # Skip redirects and missing pages — they're not "empty" in our sense
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


def summary_for(kind: str) -> str:
    if kind == "emptying":
        return (
            "Restoring content emptied by automated merge "
            "(per Epìdosis: each merge produces an empty + a redirect edit; "
            "this undoes the empty edit and Wikidata clears the redirect "
            "automatically)"
        )
    if kind == "merge-target":
        return (
            "Reverting wrongly-imported content from automated merge "
            "(item not created by me; see User talk:Alexander Goldberg IL)"
        )
    return (
        "Reverting modification on item not created by me "
        "(automated cleanup, see User talk:Alexander Goldberg IL)"
    )


def label_for(kind: str) -> str:
    return {"emptying": "RESTORED", "merge-target": "REVERTED", "direct": "REVERTED"}[kind]


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    token = sys.argv[1]
    args = sys.argv[2:]
    dry_run = "--dry-run" in args
    refresh_contribs = "--refresh-contribs" in args
    since: str | None = None
    if "--since" in args:
        since = args[args.index("--since") + 1]
    max_reverts: int | None = None
    if "--max" in args:
        max_reverts = int(args[args.index("--max") + 1])
    mode = "merges"  # default: only undo merges (safer)
    if "--mode" in args:
        mode = args[args.index("--mode") + 1]
        if mode not in ("merges", "direct", "all"):
            print(f"Invalid --mode {mode!r}; must be one of: merges, direct, all")
            sys.exit(2)
    circuit_breaker_threshold = 10
    if "--circuit-breaker" in args:
        circuit_breaker_threshold = int(args[args.index("--circuit-breaker") + 1])

    s = RetryingSession(bearer_token=token)
    csrf = get_csrf_token(s)
    auth_user = get_authenticated_user(s)
    print(f"Authenticated as: {auth_user}")
    print(f"Mode: {mode}  (merges = wbmergeitems-to/from; direct = other; all = both)")
    print(f"Circuit breaker: halt after {circuit_breaker_threshold} consecutive failures")
    if dry_run:
        print("DRY RUN — no edits will be made")
    if since:
        print(f"Only considering my edits since {since}")

    # Step 1: contribs
    if CONTRIBS_CACHE.exists() and not refresh_contribs:
        print(f"\nUsing cached contribs from {CONTRIBS_CACHE}")
        contribs = json.loads(CONTRIBS_CACHE.read_text())
    else:
        print("\nFetching all my contributions on Q-items...")
        contribs = fetch_all_my_contribs(s, auth_user, since)
        CONTRIBS_CACHE.write_text(json.dumps(contribs))
    print(f"Total contributions on Q-items: {len(contribs)}")

    # Step 2: per-QID plan (newest revid first within each QID)
    plan = plan_per_qid(contribs, mode=mode)
    n_emptying = sum(1 for revs in plan.values() for r in revs if r["_kind"] == "emptying")
    n_target = sum(1 for revs in plan.values() for r in revs if r["_kind"] == "merge-target")
    n_direct = sum(1 for revs in plan.values() for r in revs if r["_kind"] == "direct")
    n_revids = n_emptying + n_target + n_direct
    print("\nPlan (after filtering my revert/restore actions):")
    print(f"  distinct QIDs:                                    {len(plan)}")
    print(f"  total revids to undo across those QIDs:           {n_revids}")
    print(f"  merge-source emptying (wbmergeitems-to):          {n_emptying}")
    print(f"  merge-target receiving (wbmergeitems-from):       {n_target}")
    print(f"  direct edits (wbeditentity / wbsetlabel / etc.):  {n_direct}")

    # Step 3: checkpoint filter (per-revid)
    done = load_checkpoint()
    if done:
        print(f"\nResuming from checkpoint: {len(done)} revids already processed")

    # Build flat target list, ordered by:
    #   (1) QID block keeps its newest-first internal order
    #   (2) blocks ordered by newest revid (so we process recent QIDs first)
    pending: list[dict] = []
    qid_order: list[str] = sorted(plan.keys(), key=lambda q: -plan[q][0]["revid"])
    for qid in qid_order:
        for rev in plan[qid]:
            if rev["revid"] in done:
                continue
            pending.append(rev)
    print(f"To process this run: {len(pending)} revids (lookup workers={LOOKUP_WORKERS})\n")
    if not pending:
        print("Nothing to do.")
        return

    # Step 4: concurrent prefetch of safety info, indexed by QID (not revid)
    qids_in_order: list[str] = []
    seen_qid: set[str] = set()
    for r in pending:
        if r["qid"] not in seen_qid:
            qids_in_order.append(r["qid"])
            seen_qid.add(r["qid"])

    worker_sessions = [RetryingSession(bearer_token=token) for _ in range(LOOKUP_WORKERS)]

    def prefetch_one(idx_qid: tuple[int, str]) -> tuple[str, tuple[str, str]]:
        idx, qid = idx_qid
        ws = worker_sessions[idx % LOOKUP_WORKERS]
        return qid, lookup_safety(ws, qid)

    pool = ThreadPoolExecutor(max_workers=LOOKUP_WORKERS)
    creator_cache: dict[str, str] = {}  # creator never changes, prefetch once

    def schedule_prefetch(start: int, count: int) -> None:
        items = [
            (start + j, qids_in_order[start + j])
            for j in range(count)
            if start + j < len(qids_in_order)
        ]
        for fut in pool.map(prefetch_one, items):
            qid, (creator, _latest) = fut
            creator_cache[qid] = creator

    schedule_prefetch(0, PREFETCH_AHEAD)

    # Step 5: revert loop
    ok = skip = fail = 0
    consecutive_fail = 0
    start_time = time.time()
    last_qid: str = ""
    qid_idx = 0
    recent_reverted: list[str] = []  # QIDs reverted since last verification
    total_empty_found: list[str] = []
    for i, rev in enumerate(pending):
        qid = rev["qid"]
        my_revid = rev["revid"]
        kind = rev["_kind"]
        kind_tag = {"emptying": "EMPTY", "merge-target": "TARGET", "direct": "DIRECT"}[kind]

        # Track QID transitions for prefetch top-up
        if qid != last_qid:
            qid_idx += 1
            last_qid = qid
            if qid_idx + PREFETCH_AHEAD // 2 < len(qids_in_order) and (
                qid_idx % (PREFETCH_AHEAD // 2) == 0
            ):
                schedule_prefetch(qid_idx + PREFETCH_AHEAD // 2, PREFETCH_AHEAD // 2)

        print(
            f"[{i + 1}/{len(pending)}] {qid} (rev {my_revid}, {kind_tag})...",
            end=" ",
            flush=True,
        )

        # ── Rule 1: creator (cached, immutable) ──
        if qid not in creator_cache:
            creator_cache[qid], _ = lookup_safety(s, qid)
        creator = creator_cache[qid]
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

        # ── Rule 2: latest editor — RE-CHECKED at edit time (defeat race) ──
        try:
            latest = get_latest_revision_author(s, qid)
        except Exception as e:
            print(f"ERR latest lookup: {e}")
            fail += 1
            consecutive_fail += 1
            if consecutive_fail >= circuit_breaker_threshold:
                print(f"\nCIRCUIT BREAKER: {consecutive_fail} consecutive failures, halting")
                break
            continue
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

        # ── Rule 3: built-in (my_revid was sourced from MY contribs) ──

        if dry_run:
            print(f"WOULD UNDO ({kind})")
            ok += 1
            consecutive_fail = 0
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
                    "summary": summary_for(kind),
                    "format": "json",
                },
            ).json()
            if res.get("edit", {}).get("result") == "Success":
                print(label_for(kind))
                ok += 1
                consecutive_fail = 0
                done.add(my_revid)
                recent_reverted.append(qid)
            elif "error" in res:
                err = res["error"].get("info", "")
                code = res["error"].get("code", "")
                if "undofailure" in code or "newer than" in err.lower():
                    print("SKIP (already reverted/superseded)")
                    skip += 1
                    done.add(my_revid)
                else:
                    print(f"FAIL: {code} — {err[:60]}")
                    fail += 1
                    consecutive_fail += 1
            else:
                print(f"???: {str(res)[:100]}")
                fail += 1
                consecutive_fail += 1
        except Exception as e:
            print(f"ERR: {e}")
            fail += 1
            consecutive_fail += 1

        if consecutive_fail >= circuit_breaker_threshold:
            print(f"\nCIRCUIT BREAKER: {consecutive_fail} consecutive failures, halting")
            break

        time.sleep(EDIT_SLEEP)
        if (i + 1) % 50 == 0:
            csrf = get_csrf_token(s)
            save_checkpoint(done)
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta_min = (len(pending) - i - 1) / rate / 60 if rate > 0 else 0
            # Verify the items we just reverted are not empty/corrupted
            unique_recent = list(dict.fromkeys(recent_reverted))
            healthy, empty_found = verify_items_not_empty(s, unique_recent)
            print(
                f"  -- checkpoint @ {i + 1}/{len(pending)}: "
                f"{ok} ok, {skip} skip, {fail} fail | "
                f"verified {healthy}/{len(unique_recent)} healthy, {len(empty_found)} empty | "
                f"{rate:.1f}/s, ETA {eta_min:.0f} min --",
                flush=True,
            )
            if empty_found:
                total_empty_found.extend(empty_found)
                print(
                    f"  ⚠ EMPTY ITEMS detected after revert: {empty_found[:10]}"
                    f"{'...' if len(empty_found) > 10 else ''}",
                    flush=True,
                )
                # Auto-halt if more than 5% of recent batch is empty (script is misbehaving)
                if len(empty_found) > max(2, len(unique_recent) // 20):
                    print(
                        f"\nHALT: {len(empty_found)}/{len(unique_recent)} of recent batch "
                        f"is empty — exceeds 5% threshold. Investigate before continuing.",
                        flush=True,
                    )
                    save_checkpoint(done)
                    break
            recent_reverted.clear()
        if max_reverts is not None and ok >= max_reverts:
            print(f"\nStopping at --max {max_reverts}")
            break

    save_checkpoint(done)
    # Final verification of any remaining un-verified items
    if recent_reverted and not dry_run:
        unique_recent = list(dict.fromkeys(recent_reverted))
        healthy, empty_found = verify_items_not_empty(s, unique_recent)
        print(
            f"\nFinal verification: {healthy}/{len(unique_recent)} healthy, {len(empty_found)} empty"
        )
        if empty_found:
            total_empty_found.extend(empty_found)
            print(f"  ⚠ Empty: {empty_found[:20]}")
    print(f"\nDONE: {ok} ok, {skip} skipped, {fail} failed")
    if total_empty_found:
        print(f"⚠ TOTAL empty items detected during run: {len(total_empty_found)}")
        print(f"  Sample: {total_empty_found[:20]}")
    else:
        print("✓ No empty items detected in any verification batch.")
    print(f"Checkpoint: {CHECKPOINT}")
    print(f"Contribs cache: {CONTRIBS_CACHE} (--refresh-contribs to refetch)")


if __name__ == "__main__":
    main()
