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

    # Get authenticated username for creator-author safety check
    user_resp = s.get(
        API,
        params={
            "action": "query",
            "meta": "userinfo",
            "format": "json",
        },
    )
    global AUTH_USER
    AUTH_USER = user_resp.json().get("query", {}).get("userinfo", {}).get("name", "")
    print(f"Authenticated as: {AUTH_USER}. CSRF token obtained.")
    return s, csrf


AUTH_USER: str = ""
_creator_cache: dict[str, str] = {}


def refresh_csrf(s: requests.Session) -> str:
    resp = s.get(API, params={"action": "query", "meta": "tokens", "format": "json"})
    return resp.json()["query"]["tokens"]["csrftoken"]


def get_creator(s: requests.Session, qid: str) -> str:
    """Get the username of the FIRST revision (creator) of an item."""
    if qid in _creator_cache:
        return _creator_cache[qid]
    try:
        resp = s.get(
            API,
            params={
                "action": "query",
                "prop": "revisions",
                "titles": qid,
                "rvprop": "user",
                "rvdir": "newer",
                "rvlimit": "1",
                "format": "json",
            },
        )
        for _pid, page in resp.json().get("query", {}).get("pages", {}).items():
            revs = page.get("revisions", [])
            if revs:
                author = revs[0].get("user", "")
                _creator_cache[qid] = author
                return author
    except Exception:
        pass
    return ""


def is_our_item(qid: str, session: requests.Session | None = None) -> bool:
    """STRICT: Check if QID was CREATED by the authenticated user."""
    # Range check first (cheap)
    try:
        if int(qid[1:]) < OUR_QID_MIN:
            return False
    except (ValueError, IndexError):
        return False

    # Author check (the real safety guard)
    if session and AUTH_USER:
        creator = get_creator(session, qid)
        if creator and creator != AUTH_USER:
            print(f"  SAFETY BLOCK: {qid} was created by '{creator}', not '{AUTH_USER}'")
            return False
    return True


# Properties whose conflicting values prove two items are different entities.
# If from_id and to_id both have one of these and the values disagree, REFUSE.
_CONFLICT_PROPS = ("P569", "P570", "P19", "P20", "P227", "P214", "P8189", "P213", "P244")


def _get_claims(s: requests.Session, qid: str) -> dict[str, set[str]]:
    """Fetch identity-relevant claim values for an item."""
    try:
        resp = s.get(
            API,
            params={
                "action": "wbgetentities",
                "ids": qid,
                "props": "claims",
                "format": "json",
            },
            timeout=15,
        )
        ent = resp.json().get("entities", {}).get(qid, {})
        out: dict[str, set[str]] = {}
        for prop in _CONFLICT_PROPS:
            for c in ent.get("claims", {}).get(prop, []):
                snak = c.get("mainsnak", {})
                if snak.get("snaktype") != "value":
                    continue
                v = snak.get("datavalue", {}).get("value")
                if isinstance(v, dict):
                    if "id" in v:
                        out.setdefault(prop, set()).add(v["id"])
                    elif "time" in v:
                        out.setdefault(prop, set()).add(v["time"][:11])
                else:
                    out.setdefault(prop, set()).add(str(v))
        return out
    except Exception as e:
        print(f"  (could not fetch claims for {qid}: {e})")
        return {}


def _has_conflict(from_claims: dict[str, set[str]], to_claims: dict[str, set[str]]) -> list[str]:
    """Return list of properties where from and to disagree."""
    bad = []
    for prop in _CONFLICT_PROPS:
        a = from_claims.get(prop, set())
        b = to_claims.get(prop, set())
        if a and b and not (a & b):
            bad.append(prop)
    return bad


def merge(s: requests.Session, csrf: str, from_id: str, to_id: str) -> dict:
    # Safety 1: from_id must be ours (created by authenticated user).
    if not is_our_item(from_id, s):
        return {
            "error": {
                "code": "safety-block",
                "info": f"{from_id} is NOT our item (range or creator mismatch)",
            }
        }
    # Safety 2: to_id must be a known target. We allow merging into items NOT
    # created by us (that's how dedup against community items works), but we
    # require the target to look like a real entity, not an obviously-different one.
    # The next check handles that.

    # Safety 3: pre-merge metadata conflict. If from and to disagree on any
    # identity property (DOB, POB, GND, VIAF, NLI, ISNI, LCCN), they are
    # different real-world entities and MUST NOT be merged.
    from_claims = _get_claims(s, from_id)
    to_claims = _get_claims(s, to_id)
    conflicts = _has_conflict(from_claims, to_claims)
    if conflicts:
        return {
            "error": {
                "code": "safety-block",
                "info": f"{from_id}↔{to_id} conflict on {','.join(conflicts)} — refusing merge",
            }
        }
    return s.post(
        API,
        data={
            "action": "wbmergeitems",
            "fromid": from_id,
            "toid": to_id,
            "token": csrf,
            "ignoreconflicts": "description|sitelink|statement",
            "summary": "Merging duplicate from MHM Pipeline automated upload",
            "format": "json",
        },
    ).json()


def blank(s: requests.Session, csrf: str, qid: str) -> dict:
    if not is_our_item(qid, s):
        return {
            "error": {
                "code": "safety-block",
                "info": f"{qid} is NOT our item (range or creator mismatch)",
            }
        }
    return s.post(
        API,
        data={
            "action": "wbeditentity",
            "id": qid,
            "clear": "true",
            "data": json.dumps(
                {
                    "labels": {
                        "en": {"language": "en", "value": "deleted - MHM Pipeline duplicate"}
                    },
                    "descriptions": {},
                    "claims": [],
                }
            ),
            "token": csrf,
            "summary": "Blanking orphan duplicate from MHM Pipeline test run",
            "format": "json",
        },
    ).json()


def run_op(s, csrf, items, op_func, op_name):
    ok = skip = fail = 0
    for i, item in enumerate(items):
        if isinstance(item, list):
            from_id, to_id = item
            label = f"{from_id} → {to_id}"
        else:
            from_id, to_id = item, None
            label = item

        print(f"[{i + 1}/{len(items)}] {op_name} {label}...", end=" ", flush=True)
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
                if any(
                    x in code + info.lower()
                    for x in ["no-such-entity", "cant-load", "redirect", "already"]
                ):
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
                print("???")
                fail += 1
        except Exception as e:
            print(f"ERR: {e}")
            fail += 1

        time.sleep(1.5)
        if (i + 1) % 50 == 0:
            csrf = refresh_csrf(s)

    print(f"\n{op_name} done: {ok} ok, {skip} skip, {fail} fail")
    return csrf


def main() -> None:
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
