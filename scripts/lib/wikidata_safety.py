"""Shared Wikidata safety helpers used by every script that modifies items.

Why this module exists
----------------------
On 2026-04-12 a cleanup script merged 902+ unrelated entities, prompting
complaints from Pallor, Kolja21, and Epìdosis. After the initial revert,
Epìdosis manually re-applied some merges that were actually correct.
A naive re-run of the revert script would have overwritten Epìdosis's
corrections.

These helpers enforce the rules that prevent this class of error from
ever happening again:

1. ``get_authenticated_user`` — identify the bearer token's user.
2. ``get_first_revision_author`` — never modify items I did not create.
3. ``get_latest_revision_author`` — never undo anyone else's edit.
4. ``is_safe_to_revert`` — combined guard for revert scripts.
5. ``RetryingSession`` — drop-in requests.Session that retries on network errors.

Every revert / merge / edit script in scripts/ should import from this module.
"""

from __future__ import annotations

import time
from typing import Any

import requests

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "MHMPipeline/1.0 (shvedbook@gmail.com)"
OUR_QID_MIN = 138900000  # All MHM Pipeline items have QIDs >= this number.


class RetryingSession:
    """Wrapper around requests.Session that retries on network errors.

    Six attempts with exponential backoff capped at 30 s. Use this for every
    network call in a long-running script so a transient DNS / TCP error does
    not abort hours of work.
    """

    def __init__(self, bearer_token: str | None = None) -> None:
        self._s = requests.Session()
        self._s.headers["User-Agent"] = USER_AGENT
        if bearer_token:
            self._s.headers["Authorization"] = f"Bearer {bearer_token}"

    def get(self, **kwargs: Any) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(6):
            try:
                return self._s.get(WIKIDATA_API, timeout=15, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                time.sleep(min(2**attempt, 30))
        assert last_exc is not None
        raise last_exc

    def post(self, **kwargs: Any) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(6):
            try:
                return self._s.post(WIKIDATA_API, timeout=20, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                time.sleep(min(2**attempt, 30))
        assert last_exc is not None
        raise last_exc


def get_csrf_token(s: RetryingSession) -> str:
    """Fetch a CSRF token for write operations."""
    return s.get(params={"action": "query", "meta": "tokens", "format": "json"}).json()["query"][
        "tokens"
    ]["csrftoken"]


def get_authenticated_user(s: RetryingSession) -> str:
    """Return the username of the bearer token. Empty string if unknown."""
    return (
        s.get(params={"action": "query", "meta": "userinfo", "format": "json"})
        .json()
        .get("query", {})
        .get("userinfo", {})
        .get("name", "")
    )


def get_first_revision_author(s: RetryingSession, qid: str) -> str:
    """Return the username that created the item. Empty string on lookup failure.

    Used to enforce: never modify an item I did not create.
    """
    r = s.get(
        params={
            "action": "query",
            "prop": "revisions",
            "titles": qid,
            "rvprop": "user",
            "rvdir": "newer",
            "rvlimit": "1",
            "format": "json",
        },
    ).json()
    for _pid, page in r.get("query", {}).get("pages", {}).items():
        revs = page.get("revisions", [])
        if revs:
            return str(revs[0].get("user", ""))
    return ""


def get_latest_revision_author(s: RetryingSession, qid: str) -> str:
    """Return the username of the MOST RECENT revision. Empty on failure.

    Used to enforce: NEVER undo when the latest edit is by anyone else
    (their edit may be a deliberate correction of mine — e.g., Epìdosis
    re-applying a merge I had wrongly reverted). Undoing my older revision
    would silently override their correction.
    """
    r = s.get(
        params={
            "action": "query",
            "prop": "revisions",
            "titles": qid,
            "rvprop": "user",
            "rvdir": "older",
            "rvlimit": "1",
            "format": "json",
        },
    ).json()
    for _pid, page in r.get("query", {}).get("pages", {}).items():
        revs = page.get("revisions", [])
        if revs:
            return str(revs[0].get("user", ""))
    return ""


def is_safe_to_revert(
    s: RetryingSession,
    qid: str,
    auth_user: str,
) -> tuple[bool, str]:
    """Combined safety check for revert scripts.

    Returns ``(safe, reason)``. ``safe`` is True only when:

    1. The item's first revision author is NOT the authenticated user
       (otherwise the item is mine and there is nothing to "revert" —
       it was never wrong to begin with).
    2. The item's most recent revision is by the authenticated user
       (otherwise someone else has touched the item since my edit, and
       undoing my older revision would silently override their work).

    The wording of ``reason`` is suitable for printing in script logs.
    """
    creator = get_first_revision_author(s, qid)
    if not creator:
        return False, "could not determine creator (refuse to be safe)"
    if creator == auth_user:
        return False, "I created this item — nothing to revert"

    latest = get_latest_revision_author(s, qid)
    if not latest:
        return False, "could not determine latest editor (refuse to be safe)"
    if latest != auth_user:
        return False, (
            f"latest edit is by '{latest}', not me — refusing to undo "
            "(would silently override their correction)"
        )
    return True, "safe"
