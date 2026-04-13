"""Fix data quality issues on existing MHM Pipeline Wikidata items.

Reads /tmp/audit_issues.json and corrects:
- org_as_human: P31=Q5 → Q43229 (organization)
- p1559_lang_mismatch: re-tag P1559 with correct language by script
- trailing_comma: strip trailing punctuation from labels and P1559

SAFETY: Every modification verifies that the FIRST revision author of the
target item matches the authenticated user. Items not created by us are
silently skipped. QID range check (>= Q138900000) is the first filter.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/fix_wikidata_items.py <bearer_token>
"""

from __future__ import annotations

import json
import sys
import time

import requests

API = "https://www.wikidata.org/w/api.php"
OUR_QID_MIN = 138900000

AUTH_USER: str = ""
_creator_cache: dict[str, str] = {}


def get_session(bearer_token: str) -> tuple[requests.Session, str]:
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {bearer_token}"
    s.headers["User-Agent"] = "MHMPipeline/1.0 (shvedbook@gmail.com)"

    csrf = s.get(API, params={"action": "query", "meta": "tokens", "format": "json"}).json()[
        "query"
    ]["tokens"]["csrftoken"]

    user_resp = s.get(API, params={"action": "query", "meta": "userinfo", "format": "json"})
    global AUTH_USER  # noqa: PLW0603
    AUTH_USER = user_resp.json().get("query", {}).get("userinfo", {}).get("name", "")
    print(f"Authenticated as: {AUTH_USER}")
    return s, csrf


def refresh_csrf(s: requests.Session) -> str:
    return s.get(API, params={"action": "query", "meta": "tokens", "format": "json"}).json()[
        "query"
    ]["tokens"]["csrftoken"]


def get_creator(s: requests.Session, qid: str) -> str:
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


def is_my_item(s: requests.Session, qid: str) -> bool:
    """STRICT: True only if QID >= Q138900000 AND creator == authenticated user."""
    try:
        if int(qid[1:]) < OUR_QID_MIN:
            return False
    except (ValueError, IndexError):
        return False
    creator = get_creator(s, qid)
    if not creator:
        # Could not determine creator — REFUSE to be safe
        return False
    if creator != AUTH_USER:
        return False
    return True


def detect_script_lang(text: str) -> str | None:
    """Return BCP-47 language code based on script of text."""
    if any("\u0590" <= c <= "\u05ff" for c in text):
        return "he"
    if any("\u0400" <= c <= "\u04ff" for c in text):
        return "ru"
    if any("\u0600" <= c <= "\u06ff" for c in text):
        return "ar"
    if any("a" <= c.lower() <= "z" for c in text):
        return "la"
    return None


def fix_org_as_human(s: requests.Session, csrf: str, qid: str) -> str:
    """Change P31 from Q5 to Q43229. Remove human-only properties."""
    # Get current claims
    r = s.get(
        API,
        params={
            "action": "wbgetentities",
            "ids": qid,
            "props": "claims",
            "format": "json",
        },
    ).json()
    claims = r.get("entities", {}).get(qid, {}).get("claims", {})

    # Update P31: Q5 → Q43229
    for c in claims.get("P31", []):
        val = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if val.get("id") == "Q5":
            c["mainsnak"]["datavalue"]["value"]["id"] = "Q43229"
            c["mainsnak"]["datavalue"]["value"]["numeric-id"] = 43229
            res = s.post(
                API,
                data={
                    "action": "wbsetclaim",
                    "claim": json.dumps(c),
                    "token": csrf,
                    "summary": "Fix P31: Q5 (human) → Q43229 (organization) for library/seminary/etc.",
                    "format": "json",
                },
            ).json()
            if "error" in res:
                return f"FAIL P31: {res['error'].get('info', '')[:60]}"

    # Remove human-only properties: P21 (gender), P1412 (language), P569/P570 (dates)
    for prop in ("P21", "P1412", "P569", "P570"):
        for c in claims.get(prop, []):
            claim_id = c.get("id")
            if claim_id:
                s.post(
                    API,
                    data={
                        "action": "wbremoveclaims",
                        "claim": claim_id,
                        "token": csrf,
                        "summary": f"Remove {prop} from organization (human-only property)",
                        "format": "json",
                    },
                )
                time.sleep(0.5)

    return "OK"


def fix_p1559_language(s: requests.Session, csrf: str, qid: str) -> str:
    """Re-tag P1559 with correct language based on script."""
    r = s.get(
        API,
        params={
            "action": "wbgetclaims",
            "entity": qid,
            "property": "P1559",
            "format": "json",
        },
    ).json()
    claims = r.get("claims", {}).get("P1559", [])
    fixed = 0
    for c in claims:
        val = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        text = val.get("text", "")
        lang = val.get("language", "")
        correct_lang = detect_script_lang(text)
        if correct_lang and correct_lang != lang:
            c["mainsnak"]["datavalue"]["value"]["language"] = correct_lang
            res = s.post(
                API,
                data={
                    "action": "wbsetclaim",
                    "claim": json.dumps(c),
                    "token": csrf,
                    "summary": f"Fix P1559 language: {lang} → {correct_lang} (matches script)",
                    "format": "json",
                },
            ).json()
            if "error" not in res:
                fixed += 1
            time.sleep(0.5)
    return f"OK ({fixed} fixed)" if fixed else "no-op"


def fix_trailing_comma(s: requests.Session, csrf: str, qid: str) -> str:
    """Strip trailing punctuation from he label and P1559 values."""
    fixed = []

    # Get labels
    r = s.get(
        API,
        params={
            "action": "wbgetentities",
            "ids": qid,
            "props": "labels|claims",
            "languages": "he|en",
            "format": "json",
        },
    ).json()
    e = r.get("entities", {}).get(qid, {})

    # Fix he label
    for lang in ("he", "en"):
        label = e.get("labels", {}).get(lang, {}).get("value", "")
        if label and (label.endswith(",") or label.endswith(";") or label.endswith(":")):
            cleaned = label.strip().rstrip(",;:")
            if cleaned and len(cleaned) >= 2:
                res = s.post(
                    API,
                    data={
                        "action": "wbsetlabel",
                        "id": qid,
                        "language": lang,
                        "value": cleaned,
                        "token": csrf,
                        "summary": "Strip trailing punctuation from MARC name",
                        "format": "json",
                    },
                ).json()
                if "error" not in res:
                    fixed.append(f"label[{lang}]")
                time.sleep(0.5)

    # Fix P1559 values
    for c in e.get("claims", {}).get("P1559", []):
        val = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        text = val.get("text", "")
        if text and (text.endswith(",") or text.endswith(";") or text.endswith(":")):
            cleaned = text.strip().rstrip(",;:")
            if cleaned and len(cleaned) >= 2:
                c["mainsnak"]["datavalue"]["value"]["text"] = cleaned
                res = s.post(
                    API,
                    data={
                        "action": "wbsetclaim",
                        "claim": json.dumps(c),
                        "token": csrf,
                        "summary": "Strip trailing punctuation from P1559",
                        "format": "json",
                    },
                ).json()
                if "error" not in res:
                    fixed.append("P1559")
                time.sleep(0.5)

    return f"OK ({', '.join(fixed)})" if fixed else "no-op"


def run_fixes(
    s: requests.Session, csrf: str, items: list, fix_func, label: str
) -> tuple[int, int, int]:
    """Run a fix function over a list of (qid, ...) tuples."""
    ok = skip = fail = 0
    for i, item in enumerate(items):
        qid = item[0] if isinstance(item, list) else item
        print(f"[{label}] [{i + 1}/{len(items)}] {qid}...", end=" ", flush=True)

        if not is_my_item(s, qid):
            print("SKIP (not my item or unknown creator)")
            skip += 1
            continue

        try:
            result = fix_func(s, csrf, qid)
            print(result)
            if result.startswith("OK") or result == "no-op":
                ok += 1
            else:
                fail += 1
        except Exception as exc:
            print(f"ERR: {exc}")
            fail += 1

        time.sleep(1.5)
        if (i + 1) % 50 == 0:
            csrf = refresh_csrf(s)

    print(f"\n[{label}] DONE: {ok} ok, {skip} skip, {fail} fail")
    return ok, skip, fail


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    token = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "--all"

    s, csrf = get_session(token)

    with open("/tmp/audit_issues.json") as f:
        issues = json.load(f)

    print("\n=== AUDIT SUMMARY ===")
    for k, v in issues.items():
        print(f"  {k}: {len(v)}")
    print()

    if mode in ("--all", "--orgs"):
        print(f"\n=== Fix 1: org_as_human ({len(issues['org_as_human'])} items) ===")
        run_fixes(s, csrf, issues["org_as_human"], fix_org_as_human, "ORG")

    if mode in ("--all", "--p1559"):
        print(f"\n=== Fix 2: p1559_lang_mismatch ({len(issues['p1559_lang_mismatch'])} items) ===")
        run_fixes(s, csrf, issues["p1559_lang_mismatch"], fix_p1559_language, "P1559")

    if mode in ("--all", "--commas"):
        print(f"\n=== Fix 3: trailing_comma ({len(issues['trailing_comma'])} items) ===")
        run_fixes(s, csrf, issues["trailing_comma"], fix_trailing_comma, "COMMA")

    print("\nAll done.")


if __name__ == "__main__":
    main()
