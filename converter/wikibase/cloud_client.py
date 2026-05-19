"""Read-only Wikibase Cloud connection helper for the HMO Wikibase tab.

The :class:`WikibaseCloudClient` is read-only and used by the HMO Wikibase
preview panel for siteinfo checks.

The :class:`WikibaseCloudWriter` (added 2026-05-17 for Phase 3 / Rule 45)
is the authenticated companion that writes IIIF manifest JSON pages to
``mhm-hmo.wikibase.cloud`` under the ``IIIF:`` namespace. It is a
**separate** class so the read-only surface stays unambiguous. The
writer:

* enforces ``assert=bot`` on every edit
* is idempotent (reads existing wikitext first, skips identical content)
* retries on transient HTTP errors with exponential backoff capped at 30s
* redacts the bot password from ``__repr__``

Credentials are passed via :class:`WikibaseBotCredentials` and stored
via ``SettingsManager`` (OS keychain on macOS, Credential Manager on
Windows) — never on disk in plaintext.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import requests

_DEFAULT_HMO_WIKIBASE_URL = "https://mhm-hmo.wikibase.cloud"


@dataclass(frozen=True)
class WikibaseEndpointConfig:
    """Configuration for a Wikibase Cloud endpoint."""

    base_url: str
    display_name: str | None = None

    @property
    def api_url(self) -> str:
        """Return the MediaWiki API URL for the configured Wikibase base URL."""
        normalized = self.base_url.rstrip("/")
        if normalized.endswith("/w/api.php"):
            return normalized
        return f"{normalized}/w/api.php"


@dataclass(frozen=True)
class WikibaseConnectionResult:
    """Outcome of a read-only Wikibase Cloud siteinfo connection test."""

    ok: bool
    site_name: str
    generator: str
    api_url: str
    message: str


class WikibaseCloudClient:
    """Small read-only client for checking a Wikibase Cloud endpoint."""

    def __init__(
        self,
        config: WikibaseEndpointConfig,
        *,
        session: requests.Session | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._config = config
        self._session = session or requests.Session()
        self._timeout = timeout

    @classmethod
    def config_for_mhm_hmo_cloud(cls) -> WikibaseEndpointConfig:
        """Return the default endpoint configuration for the MHM HMO Wikibase."""
        return WikibaseEndpointConfig(
            base_url=_DEFAULT_HMO_WIKIBASE_URL,
            display_name="MHM HMO Wikibase",
        )

    @classmethod
    def for_mhm_hmo_cloud(cls, *, timeout: float = 20.0) -> WikibaseCloudClient:
        """Create a read-only client for the MHM HMO Wikibase Cloud instance."""
        return cls(cls.config_for_mhm_hmo_cloud(), timeout=timeout)

    def test_connection(self) -> WikibaseConnectionResult:
        """Fetch read-only siteinfo and return a graceful connection result."""
        api_url = self._config.api_url
        params: dict[str, str] = {
            "action": "query",
            "meta": "siteinfo",
            "siprop": "general",
            "format": "json",
        }
        try:
            response = self._session.get(api_url, params=params, timeout=self._timeout)
            response.raise_for_status()
            payload = cast(object, response.json())
        except requests.RequestException as exc:
            return self._failure(api_url, f"Network error: {exc}")
        except ValueError as exc:
            return self._failure(api_url, f"Invalid JSON response: {exc}")

        if not isinstance(payload, Mapping):
            return self._failure(api_url, "Unexpected API response: root is not an object")

        error_message = _api_error_message(payload)
        if error_message is not None:
            return self._failure(api_url, error_message)

        general = _nested_mapping(payload, "query", "general")
        if general is None:
            return self._failure(api_url, "Unexpected API response: missing query.general")

        site_name = _string_value(general, "sitename") or self._config.display_name or ""
        generator = _string_value(general, "generator") or ""
        if site_name == "" and generator == "":
            return self._failure(api_url, "Unexpected API response: missing site metadata")

        return WikibaseConnectionResult(
            ok=True,
            site_name=site_name,
            generator=generator,
            api_url=api_url,
            message="Connection successful",
        )

    def _failure(self, api_url: str, message: str) -> WikibaseConnectionResult:
        """Build a consistent failed connection result."""
        return WikibaseConnectionResult(
            ok=False,
            site_name=self._config.display_name or "",
            generator="",
            api_url=api_url,
            message=message,
        )


def _nested_mapping(
    mapping: Mapping[object, object],
    first_key: str,
    second_key: str,
) -> Mapping[object, object] | None:
    first_value = mapping.get(first_key)
    if not isinstance(first_value, Mapping):
        return None
    second_value = first_value.get(second_key)
    if not isinstance(second_value, Mapping):
        return None
    return second_value


def _string_value(mapping: Mapping[object, object], key: str) -> str | None:
    value = mapping.get(key)
    if isinstance(value, str):
        return value
    return None


def _api_error_message(payload: Mapping[object, object]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None

    code = _string_value(error, "code")
    info = _string_value(error, "info")
    if code is not None and info is not None:
        return f"API error {code}: {info}"
    if info is not None:
        return f"API error: {info}"
    if code is not None:
        return f"API error {code}"
    return "API error"


# ─────────────────────────────────────────────────────────────────────
# Rule 45 (Phase 3, 2026-05-17): Wikibase Cloud authenticated writer
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WikibaseBotCredentials:
    """Bot password tuple issued by ``Special:BotPasswords``.

    Login name format: ``"<username>@<bot_name>"``.
    """

    username: str
    bot_name: str
    password: str

    @property
    def login_name(self) -> str:
        """Build the canonical login name used by MediaWiki API ``action=login``."""
        return f"{self.username}@{self.bot_name}"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"WikibaseBotCredentials(username={self.username!r}, "
            f"bot_name={self.bot_name!r}, password='<REDACTED>')"
        )


@dataclass(frozen=True)
class EditOutcome:
    """Result of a single ``WikibaseCloudWriter.edit_page`` call."""

    page_url: str
    status: str  # "created" | "updated" | "unchanged" | "failed"
    message: str
    edit_id: int | None  # pageid from the API
    new_revid: int | None  # revid for the new revision (for permalinks)


class WikibaseCloudWriter:
    """Authenticated MediaWiki API writer for ``mhm-hmo.wikibase.cloud``.

    This class never writes to ``wikidata.org`` (that is the
    :class:`WikidataUploader`'s domain, governed by Rules 25 and 38).
    The Wikibase Cloud is a separate trust boundary used for hosting
    IIIF manifest pages and (later) HMO Wikibase items.

    Safety properties:

    * ``assert=bot`` on every edit — refuses if the session is not bot-flagged
    * idempotent — reads existing wikitext first; skips on SHA-256 match
    * retries on transient failures up to 6 times with exponential backoff
      (capped at 30 seconds)
    * never writes credentials to logs or ``__repr__``
    """

    _MAX_RETRIES = 6
    _BASE_BACKOFF_SECONDS = 1.0
    _MAX_BACKOFF_SECONDS = 30.0

    def __init__(
        self,
        config: WikibaseEndpointConfig,
        credentials: WikibaseBotCredentials,
        *,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        user_agent: str = "MHMPipeline/1.0 (https://github.com/alexandergolbergwix/pipeline)",
    ) -> None:
        self._config = config
        self._credentials = credentials
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._timeout = timeout
        self._csrf_token: str | None = None
        self._logged_in = False

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"WikibaseCloudWriter(base_url={self._config.base_url!r}, "
            f"username={self._credentials.username!r}, password='<REDACTED>')"
        )

    @classmethod
    def for_mhm_hmo_cloud(
        cls,
        credentials: WikibaseBotCredentials,
        *,
        timeout: float = 30.0,
    ) -> WikibaseCloudWriter:
        """Build a writer pointed at the default MHM HMO Wikibase Cloud."""
        return cls(
            WikibaseCloudClient.config_for_mhm_hmo_cloud(),
            credentials,
            timeout=timeout,
        )

    # ── URL builders ─────────────────────────────────────────────────

    def page_url(self, title: str) -> str:
        """Build the human-readable page URL for a given title."""
        normalized = self._config.base_url.rstrip("/")
        return f"{normalized}/wiki/{title}"

    def raw_url(self, title: str) -> str:
        """Build the raw-content URL (IIIF consumers expect JSON)."""
        return f"{self.page_url(title)}?action=raw&ctype=application/json"

    # ── Auth ─────────────────────────────────────────────────────────

    def login(self) -> None:
        """Perform the two-step MediaWiki login.

        Raises:
            RuntimeError: on any auth failure.
        """
        login_token_payload = self._post_with_retry(
            {
                "action": "query",
                "meta": "tokens",
                "type": "login",
                "format": "json",
            }
        )
        tokens = _nested_mapping(login_token_payload, "query", "tokens")
        login_token = _string_value(tokens or {}, "logintoken")
        if not login_token:
            raise RuntimeError("Failed to obtain login token from MediaWiki API")

        result = self._post_with_retry(
            {
                "action": "login",
                "lgname": self._credentials.login_name,
                "lgpassword": self._credentials.password,
                "lgtoken": login_token,
                "format": "json",
            }
        )
        login_block = result.get("login") if isinstance(result, Mapping) else None
        if not isinstance(login_block, Mapping):
            raise RuntimeError(f"Unexpected login response: {result!r}")
        outcome = _string_value(login_block, "result")
        if outcome != "Success":
            reason = _string_value(login_block, "reason") or outcome or "unknown"
            raise RuntimeError(f"Login failed ({reason})")
        self._logged_in = True

    def _get_csrf_token(self) -> str:
        """Return a cached CSRF token, fetching/refreshing if needed."""
        if self._csrf_token is not None:
            return self._csrf_token
        if not self._logged_in:
            self.login()
        payload = self._post_with_retry(
            {
                "action": "query",
                "meta": "tokens",
                "type": "csrf",
                "format": "json",
            }
        )
        tokens = _nested_mapping(payload, "query", "tokens")
        token = _string_value(tokens or {}, "csrftoken")
        if not token:
            raise RuntimeError(f"Failed to obtain CSRF token: {payload!r}")
        self._csrf_token = token
        return token

    # ── Read ─────────────────────────────────────────────────────────

    def read_page(self, title: str) -> str | None:
        """Read existing wikitext for the page, or ``None`` if it does not exist."""
        payload = self._post_with_retry(
            {
                "action": "parse",
                "page": title,
                "prop": "wikitext",
                "format": "json",
            }
        )
        # When the page is missing, MediaWiki returns an error block
        # rather than empty wikitext.
        if "error" in payload:
            error_block = payload["error"]
            if isinstance(error_block, Mapping):
                if _string_value(error_block, "code") == "missingtitle":
                    return None
            return None
        parse = payload.get("parse") if isinstance(payload, Mapping) else None
        if not isinstance(parse, Mapping):
            return None
        wikitext = parse.get("wikitext")
        if isinstance(wikitext, Mapping):
            text = wikitext.get("*")
            if isinstance(text, str):
                return text
        if isinstance(wikitext, str):
            return wikitext
        return None

    # ── Write ────────────────────────────────────────────────────────

    def edit_page(
        self,
        title: str,
        body: str,
        summary: str,
        *,
        content_model: str = "json",
    ) -> EditOutcome:
        """Create or update a page idempotently.

        If the existing wikitext matches the new body byte-for-byte
        (after stripping surrounding whitespace), no API write is sent
        and ``status="unchanged"`` is returned.
        """
        existing = self.read_page(title)
        if existing is not None and _content_hash(existing) == _content_hash(body):
            return EditOutcome(
                page_url=self.page_url(title),
                status="unchanged",
                message="content identical; edit skipped",
                edit_id=None,
                new_revid=None,
            )

        token = self._get_csrf_token()
        params: dict[str, str] = {
            "action": "edit",
            "title": title,
            "text": body,
            "summary": summary,
            "token": token,
            "bot": "1",
            "contentmodel": content_model,
            "format": "json",
            "assert": "bot",
        }
        result = self._post_with_retry(params)

        # Stale CSRF? refresh once and retry.
        error = result.get("error") if isinstance(result, Mapping) else None
        if isinstance(error, Mapping):
            code = _string_value(error, "code")
            if code in ("badtoken", "notoken"):
                self._csrf_token = None
                params["token"] = self._get_csrf_token()
                result = self._post_with_retry(params)
                error = result.get("error") if isinstance(result, Mapping) else None

        if isinstance(error, Mapping):
            msg = _api_error_message(result) or str(error)
            return EditOutcome(
                page_url=self.page_url(title),
                status="failed",
                message=msg,
                edit_id=None,
                new_revid=None,
            )

        edit = result.get("edit") if isinstance(result, Mapping) else None
        if not isinstance(edit, Mapping):
            return EditOutcome(
                page_url=self.page_url(title),
                status="failed",
                message=f"Unexpected response: {result!r}",
                edit_id=None,
                new_revid=None,
            )
        outcome = _string_value(edit, "result")
        if outcome != "Success":
            return EditOutcome(
                page_url=self.page_url(title),
                status="failed",
                message=f"edit result={outcome!r}",
                edit_id=None,
                new_revid=None,
            )
        status = "updated" if edit.get("oldrevid") else "created"
        pageid_val = edit.get("pageid")
        revid_val = edit.get("newrevid")
        return EditOutcome(
            page_url=self.page_url(title),
            status=status,
            message="ok",
            edit_id=int(pageid_val) if isinstance(pageid_val, int) else None,
            new_revid=int(revid_val) if isinstance(revid_val, int) else None,
        )

    # ── HTTP plumbing ────────────────────────────────────────────────

    def _post_with_retry(self, params: dict[str, str]) -> Mapping[object, object]:
        """POST to the MediaWiki API with exponential-backoff retry.

        Retries on connection / timeout / 5xx responses. Treats 4xx
        responses (other than 429) as terminal and returns the JSON
        body for the caller to inspect (it will contain the API error
        block in the standard MediaWiki shape).
        """
        api_url = self._config.api_url
        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                response = self._session.post(
                    api_url, data=params, timeout=self._timeout
                )
                if response.status_code == 429 or response.status_code >= 500:
                    self._sleep_for_backoff(attempt)
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise RuntimeError(f"API returned non-object payload: {payload!r}")
                return payload
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_exc = exc
                if attempt == self._MAX_RETRIES - 1:
                    raise
                self._sleep_for_backoff(attempt)
        # Defensive: loop should always either return or raise above.
        raise RuntimeError(  # pragma: no cover - defensive
            f"All retries exhausted: {last_exc!r}"
        )

    def _sleep_for_backoff(self, attempt: int) -> None:
        """Sleep for ``min(2**attempt, MAX_BACKOFF)`` seconds."""
        delay = min(
            self._BASE_BACKOFF_SECONDS * (2**attempt), self._MAX_BACKOFF_SECONDS
        )
        time.sleep(delay)


def _content_hash(text: str) -> str:
    """Stable SHA-256 of the stripped page body for idempotency checks."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
