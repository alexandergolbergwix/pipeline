"""Upload WikidataItem objects to Wikidata via WikibaseIntegrator.

Handles live upload with rate limiting, retry logic, and per-entity
error handling. WikibaseIntegrator is imported lazily so the module
can be loaded even when the library is not installed (dry-run mode
does not require it).

Supports both production wikidata.org and test.wikidata.org.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from converter.wikidata.item_builder import WikidataItem, WikidataStatement

logger = logging.getLogger(__name__)

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_WIKIDATA_URL = "https://www.wikidata.org"

_TEST_API = "https://test.wikidata.org/w/api.php"
_TEST_SPARQL = "https://test.wikidata.org/w/api.php"
_TEST_URL = "https://test.wikidata.org"

_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5.0
_EDIT_DELAY_SECONDS = 1.5  # ~40 edits/minute (safe for OAuth with 5000 req/hr)


@dataclass
class UploadResult:
    """Result of uploading a single item."""

    local_id: str
    qid: str | None = None
    status: str = "pending"  # "success" | "updated" | "exists" | "failed" | "skipped"
    message: str = ""
    added_properties: list[str] = field(default_factory=list)


class WikidataUploader:
    """Upload WikidataItem objects to Wikidata.

    Requires ``wikibaseintegrator`` package for live uploads.
    Install with: ``pip install wikibaseintegrator``

    Usage::

        uploader = WikidataUploader(token="your-oauth-token")
        results = uploader.upload_all(items)
    """

    def __init__(self, token: str, is_test: bool = False, batch_mode: bool = False) -> None:
        """Initialize the uploader.

        Args:
            token: OAuth bearer token or bot password for Wikidata API.
            is_test: If True, use test.wikidata.org instead of production.
            batch_mode: If True, pause 60s every 45 items to stay under rate limits.
        """
        self._token = token
        self._is_test = is_test
        self._batch_mode = batch_mode
        self._wbi = None
        self._last_edit_time: float = 0.0
        self._authenticated_user: str | None = None  # Set after first auth
        self._creator_cache: dict[str, str] = {}  # qid → first revision author

    def _init_wbi(self) -> object:
        """Lazily initialize WikibaseIntegrator.

        Returns:
            A configured WikibaseIntegrator instance.

        Raises:
            ImportError: If wikibaseintegrator is not installed.
        """
        if self._wbi is not None:
            return self._wbi

        try:
            from wikibaseintegrator import (
                WikibaseIntegrator,  # noqa: PLC0415
                wbi_login,  # noqa: PLC0415
            )
            from wikibaseintegrator.wbi_config import config as wbi_config  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "wikibaseintegrator is required for live Wikidata upload. "
                "Install it with: pip install wikibaseintegrator\n"
                "Or use dry-run mode to export QuickStatements instead."
            ) from exc

        if self._is_test:
            wbi_config["MEDIAWIKI_API_URL"] = _TEST_API
            wbi_config["SPARQL_ENDPOINT_URL"] = _TEST_SPARQL
            wbi_config["WIKIBASE_URL"] = _TEST_URL
        else:
            wbi_config["MEDIAWIKI_API_URL"] = _WIKIDATA_API
            wbi_config["SPARQL_ENDPOINT_URL"] = _WIKIDATA_SPARQL
            wbi_config["WIKIBASE_URL"] = _WIKIDATA_URL

        # Limit retries to avoid infinite waits during server load
        wbi_config["MAXLAG"] = 5
        wbi_config["BACKOFF_MAX_TRIES"] = 3  # Max 3 retries (default 5)
        wbi_config["BACKOFF_MAX_VALUE"] = 30  # Max 30s backoff (default 3600!)

        # Support three authentication methods:
        # 1. Bot password: "Username@BotName:password"
        # 2. OAuth 2.0: "consumer_key|consumer_secret"
        # 3. OAuth 1.0a: "consumer_key|consumer_secret|access_token|access_secret"
        api_url = wbi_config["MEDIAWIKI_API_URL"]
        user_agent = "MHMPipeline/1.0 (shvedbook@gmail.com)"

        if "|" in self._token:
            parts = self._token.split("|")
            if len(parts) == 2:
                # OAuth 2.0: consumer_key|consumer_secret
                login = wbi_login.OAuth2(
                    consumer_token=parts[0].strip(),
                    consumer_secret=parts[1].strip(),
                    mediawiki_api_url=api_url,
                    user_agent=user_agent,
                )
            elif len(parts) >= 4:
                # OAuth 1.0a: consumer_key|consumer_secret|access_token|access_secret
                login = wbi_login.OAuth1(
                    consumer_token=parts[0].strip(),
                    consumer_secret=parts[1].strip(),
                    access_token=parts[2].strip(),
                    access_secret=parts[3].strip(),
                    mediawiki_api_url=api_url,
                    user_agent=user_agent,
                )
            else:
                raise ValueError(
                    "Invalid OAuth token format. Use:\n"
                    "  OAuth 2.0: consumer_key|consumer_secret\n"
                    "  OAuth 1.0a: consumer_key|consumer_secret|access_token|access_secret"
                )
        elif ":" in self._token and "@" in self._token.split(":")[0]:
            # Bot password: "Username@BotName:password"
            parts = self._token.split(":", 1)
            login = wbi_login.Login(
                user=parts[0],
                password=parts[1],
                mediawiki_api_url=api_url,
                user_agent=user_agent,
            )
        else:
            raise ValueError(
                "Invalid authentication format. Use one of:\n"
                "  Bot password: Username@BotName:password\n"
                "  OAuth 2.0: consumer_key|consumer_secret"
            )

        self._wbi = WikibaseIntegrator(login=login)
        return self._wbi

    def _rate_limit(self) -> None:
        """Enforce edit rate limiting."""
        elapsed = time.time() - self._last_edit_time
        if elapsed < _EDIT_DELAY_SECONDS:
            time.sleep(_EDIT_DELAY_SECONDS - elapsed)
        self._last_edit_time = time.time()

    # Identity properties: if the existing item already has a DIFFERENT value
    # on one of these, adding our value would create the multi-value conflict
    # pattern that Kolja21 flagged (e.g., two birth dates, two GNDs). We MUST
    # skip our value in that case — the items are different real-world entities.
    _IDENTITY_PROPS = frozenset(
        {"P569", "P570", "P19", "P20", "P227", "P214", "P8189", "P213", "P244", "P31", "P21"}
    )

    def _would_create_identity_conflict(
        self, wbi_item: object, stmt: WikidataStatement
    ) -> bool:
        """Return True if adding this statement to the existing item would create
        a multi-value conflict on an identity property."""
        if stmt.property_id not in self._IDENTITY_PROPS:
            return False
        try:
            existing_claims = wbi_item.claims.get(stmt.property_id) or []
        except Exception:
            return False
        if not existing_claims:
            return False
        new_value = str(stmt.value)
        for existing in existing_claims:
            existing_value = self._extract_claim_value(existing)
            if existing_value == new_value:
                return False  # same value — safe to add (WBI will dedup)
            # date precision: compare just the date prefix for P569/P570
            if stmt.property_id in ("P569", "P570") and existing_value[:11] == new_value[:11]:
                return False
        return True  # existing has different value(s) — would conflict

    def _build_wbi_item(self, item: WikidataItem) -> tuple[object, int, list[str]]:
        """Convert a WikidataItem to a WikibaseIntegrator item object.

        For existing items, performs claim diffing to avoid duplicates AND
        refuses to write identity-property values that conflict with existing ones.

        Returns:
            Tuple of (wbi_item, new_claims_count, added_properties).
        """

        wbi = self._init_wbi()

        if item.existing_qid:
            wbi_item = wbi.item.get(item.existing_qid)
        else:
            wbi_item = wbi.item.new()

        # Labels — never overwrite an existing label on an item we did not create.
        # The creator-author check upstream already guarantees we only modify our
        # own items here, but be defensive: only set a label if the language slot
        # is empty.
        for lang, label in item.labels.items():
            if item.existing_qid:
                try:
                    current = wbi_item.labels.get(lang)
                    current_val = current.value if current and getattr(current, "value", None) else ""
                except Exception:
                    current_val = ""
                if current_val:
                    continue
            wbi_item.labels.set(lang, label)

        # Descriptions
        for lang, desc in item.descriptions.items():
            wbi_item.descriptions.set(lang, desc)

        # Aliases
        for lang, alias_list in item.aliases.items():
            for alias in alias_list:
                wbi_item.aliases.set(lang, alias)

        # Statements — use WBI's built-in dedup for existing items
        from wikibaseintegrator.wbi_enums import ActionIfExists  # noqa: PLC0415

        action = (
            ActionIfExists.MERGE_REFS_OR_APPEND
            if item.existing_qid
            else ActionIfExists.FORCE_APPEND
        )

        added_properties: list[str] = []
        for stmt in item.statements:
            # SAFETY: never add an identity-property value that conflicts with
            # an existing one on a pre-existing item.
            if item.existing_qid and self._would_create_identity_conflict(wbi_item, stmt):
                logger.warning(
                    "SAFETY: Refusing to add %s=%s to %s (existing item has different value — would create conflict)",
                    stmt.property_id,
                    stmt.value,
                    item.existing_qid,
                )
                continue
            claim = self._build_claim(stmt)
            if claim:
                count_before = len(wbi_item.claims)
                wbi_item.claims.add(claim, action_if_exists=action)
                if len(wbi_item.claims) > count_before:
                    added_properties.append(stmt.property_id)

        new_claims = len(added_properties) if item.existing_qid else len(item.statements)
        return wbi_item, new_claims, added_properties

    def _claim_exists(self, wbi_item: object, stmt: WikidataStatement) -> bool:
        """Check if a claim with the same property+value already exists on the item."""
        try:
            existing_claims = wbi_item.claims.get(stmt.property_id)
            if not existing_claims:
                return False

            new_value = str(stmt.value)

            for existing in existing_claims:
                existing_value = self._extract_claim_value(existing)
                if existing_value == new_value:
                    return True
        except Exception:
            pass  # If comparison fails, assume claim doesn't exist → add it
        return False

    @staticmethod
    def _extract_claim_value(wbi_claim: object) -> str:
        """Extract a comparable string value from a WBI claim."""
        try:
            snak = wbi_claim.mainsnak
            dv = snak.datavalue
            if not dv:
                return ""
            val = dv.get("value", dv) if isinstance(dv, dict) else dv
            if isinstance(val, dict):
                if "id" in val:
                    return val["id"]  # Q12345
                if "time" in val:
                    return val["time"]  # +1650-00-00T00:00:00Z
                if "text" in val:
                    return val["text"]  # monolingual text
                if "amount" in val:
                    return val["amount"]
                return str(val)
            return str(val)
        except Exception:
            return ""

    def _build_claim(self, stmt: WikidataStatement) -> object | None:
        """Convert a WikidataStatement to a WikibaseIntegrator claim.

        Args:
            stmt: The statement to convert.

        Returns:
            A datatypes claim object, or None if conversion fails.
        """
        from wikibaseintegrator import datatypes  # noqa: PLC0415
        from wikibaseintegrator.models import Reference, References  # noqa: PLC0415

        # Build references
        refs = References()
        if stmt.references:
            ref = Reference()
            for ref_snak in stmt.references:
                ref_claim = self._build_reference_snak(ref_snak)
                if ref_claim:
                    ref.add(ref_claim)
            refs.add(ref)

        value = stmt.value
        # Skip local references (unresolved persons)
        if isinstance(value, str) and value.startswith("__LOCAL:"):
            return None

        # Build qualifiers
        from wikibaseintegrator.models import Qualifiers  # noqa: PLC0415

        qualifiers = Qualifiers()
        for qual in stmt.qualifiers or []:
            qual_claim = self._build_reference_snak(qual)
            if qual_claim:
                qualifiers.add(qual_claim)

        try:
            if stmt.value_type == "item":
                return datatypes.Item(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                )
            if stmt.value_type == "string":
                return datatypes.String(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                )
            if stmt.value_type == "external-id":
                return datatypes.ExternalID(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                )
            if stmt.value_type == "time":
                return datatypes.Time(
                    prop_nr=stmt.property_id,
                    time=str(value),
                    precision=stmt.precision,
                    references=refs,
                    qualifiers=qualifiers,
                )
            if stmt.value_type == "quantity":
                # Map unit strings to Wikidata entity URLs
                unit_url_map = {
                    "mm": "http://www.wikidata.org/entity/Q174789",
                    "cm": "http://www.wikidata.org/entity/Q174728",
                    "m": "http://www.wikidata.org/entity/Q11573",
                }
                unit_val = unit_url_map.get(stmt.unit, "1") if stmt.unit else "1"
                return datatypes.Quantity(
                    prop_nr=stmt.property_id,
                    amount=value,
                    unit=unit_val,
                    references=refs,
                    qualifiers=qualifiers,
                )
            if stmt.value_type == "url":
                return datatypes.URL(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                )
            if stmt.value_type == "monolingualtext":
                return datatypes.MonolingualText(
                    prop_nr=stmt.property_id,
                    text=str(value),
                    language=stmt.language,
                    references=refs,
                    qualifiers=qualifiers,
                )
        except Exception as exc:
            logger.warning(
                "Failed to build claim for %s=%s: %s",
                stmt.property_id,
                value,
                exc,
            )
            return None

        return None

    def _build_reference_snak(self, ref_snak: dict[str, str]) -> object | None:
        """Build a reference snak for WikibaseIntegrator."""
        from wikibaseintegrator import datatypes  # noqa: PLC0415

        prop = ref_snak.get("property", "")
        value = ref_snak.get("value", "")
        snak_type = ref_snak.get("type", "string")

        try:
            if snak_type == "item":
                return datatypes.Item(prop_nr=prop, value=value)
            if snak_type == "url":
                return datatypes.URL(prop_nr=prop, value=value)
            if snak_type == "time":
                precision = ref_snak.get("precision", 11)
                return datatypes.Time(prop_nr=prop, time=str(value), precision=int(precision))
            return datatypes.String(prop_nr=prop, value=value)
        except Exception as exc:
            logger.warning("Failed to build reference snak %s: %s", prop, exc)
            return None

    def _get_authenticated_user(self) -> str | None:
        """Get the username of the authenticated session via API."""
        if self._authenticated_user is not None:
            return self._authenticated_user
        try:
            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            headers = {"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"}

            # OAuth 2.0 bearer token format (single token, no |)
            if "|" not in self._token and ":" not in self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            elif "|" in self._token:
                # OAuth: extract bearer if WBI provided one
                parts = self._token.split("|")
                if len(parts) == 2:
                    # Owner-only consumer — use as bearer
                    headers["Authorization"] = f"Bearer {self._token}"

            resp = requests.get(
                api_url,
                params={"action": "query", "meta": "userinfo", "format": "json"},
                headers=headers,
                timeout=10,
            )
            user = resp.json().get("query", {}).get("userinfo", {}).get("name")
            if user and user != "127.0.0.1":  # not anonymous
                self._authenticated_user = user
                logger.info("Authenticated as Wikidata user: %s", user)
                return user
        except Exception as exc:
            logger.warning("Could not determine authenticated user: %s", exc)
        return None

    def _get_first_revision_author(self, qid: str) -> str | None:
        """Get the username of the FIRST revision (creator) of an item via API."""
        if qid in self._creator_cache:
            return self._creator_cache[qid]
        try:
            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            resp = requests.get(
                api_url,
                params={
                    "action": "query",
                    "prop": "revisions",
                    "titles": qid,
                    "rvprop": "user",
                    "rvdir": "newer",
                    "rvlimit": "1",
                    "format": "json",
                },
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=10,
            )
            pages = resp.json().get("query", {}).get("pages", {})
            for _pid, page in pages.items():
                revs = page.get("revisions", [])
                if revs:
                    author = revs[0].get("user")
                    if author:
                        self._creator_cache[qid] = author
                        return author
        except Exception as exc:
            logger.warning("Could not get first revision author for %s: %s", qid, exc)
        return None

    def _is_our_item(self, qid: str) -> bool:
        """Check if an existing Wikidata item was created by the authenticated user.

        STRICT SAFETY CHECK: Verifies that the FIRST revision author of the item
        matches the currently authenticated user. This prevents modifying items
        created by other users, even if they have a P1343=Q_KTIV marker.

        Falls back to P1343=Q118384267 (Ktiv) marker check only if the user
        identity cannot be determined.
        """
        # Primary check: first revision author must match authenticated user
        auth_user = self._get_authenticated_user()
        if auth_user:
            creator = self._get_first_revision_author(qid)
            if creator and creator != auth_user:
                logger.warning(
                    "SAFETY: Item %s was created by '%s', not '%s' — REFUSING to modify",
                    qid,
                    creator,
                    auth_user,
                )
                return False
            if creator == auth_user:
                return True
            # If we couldn't determine creator, fall through to marker check

        # Fallback: P1343=Q118384267 (Ktiv) marker
        try:
            wbi = self._init_wbi()
            existing = wbi.item.get(qid)
            for claim in existing.claims.get("P1343") or []:
                mainsnak = claim.mainsnak
                if hasattr(mainsnak, "datavalue") and "Q118384267" in str(mainsnak.datavalue):
                    return True
            return False
        except Exception:
            return False

    def upload_item(self, item: WikidataItem) -> UploadResult:
        """Upload a single item to Wikidata with retry logic and smart diffing.

        For existing items: fetches current claims, compares with new claims,
        and only writes if there are actual changes (avoids duplicates).

        SAFETY: Only modifies existing items that were created by the MHM Pipeline
        (verified by checking for P1343=Q118384267 marker). If an existing item
        was NOT created by us, we skip it entirely to avoid modifying community items.

        Returns:
            UploadResult with QID and status.
        """
        self._init_wbi()

        # SAFETY: If this item has an existing_qid, verify it's OUR item before modifying
        if item.existing_qid and not self._is_our_item(item.existing_qid):
            logger.warning(
                "SAFETY: Skipping %s — existing item %s was NOT created by MHM Pipeline",
                item.local_id,
                item.existing_qid,
            )
            return UploadResult(
                local_id=item.local_id,
                qid=item.existing_qid,
                status="skipped",
                message=f"Skipped {item.existing_qid} — not created by MHM Pipeline (safety guard)",
            )

        last_error = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            self._rate_limit()
            try:
                wbi_item, new_claims, added_props = self._build_wbi_item(item)

                # Skip write if existing item has no new claims
                if item.existing_qid and new_claims == 0:
                    return UploadResult(
                        local_id=item.local_id,
                        qid=item.existing_qid,
                        status="exists",
                        message=f"No changes needed for {item.existing_qid}",
                    )

                result = wbi_item.write()
                qid = result.id if result else None

                if item.existing_qid:
                    from collections import Counter  # noqa: PLC0415

                    prop_summary = ", ".join(
                        f"{pid}x{cnt}" for pid, cnt in Counter(added_props).most_common(5)
                    )
                    return UploadResult(
                        local_id=item.local_id,
                        qid=qid,
                        status="updated",
                        message=f"Updated {qid}: +{new_claims} claims ({prop_summary})",
                        added_properties=added_props,
                    )
                return UploadResult(
                    local_id=item.local_id,
                    qid=qid,
                    status="success",
                    message=f"Created {qid} ({new_claims} claims)",
                    added_properties=added_props,
                )
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Upload attempt %d/%d for %s failed: %s",
                    attempt,
                    _MAX_RETRIES,
                    item.local_id,
                    exc,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_SECONDS * attempt)

        return UploadResult(
            local_id=item.local_id,
            status="failed",
            message=f"Failed after {_MAX_RETRIES} attempts: {last_error[:200]}",
        )

    def upload_all(
        self,
        items: list[WikidataItem],
        progress_cb: Callable[[int, int, str], None] | None = None,
        entity_cb: Callable[[str, str, str | None, str | None], None] | None = None,
    ) -> list[UploadResult]:
        """Upload all items with progress tracking.

        Args:
            items: List of WikidataItem instances.
            progress_cb: Called with (completed, total, message).
            entity_cb: Called with (local_id, status, qid, message) per entity.

        Returns:
            List of UploadResult instances.
        """
        results: list[UploadResult] = []
        total = len(items)

        # Track created QIDs so manuscripts can reference freshly created persons
        created_qids: dict[str, str] = {}

        # Batch tracking: pause between batches (only when batch_mode enabled)
        batch_size = 45 if self._batch_mode else 0
        batch_count = 0

        for idx, item in enumerate(items):
            if entity_cb:
                entity_cb(item.local_id, "uploading", None, f"Uploading {item.entity_type}...")

            # Resolve __LOCAL: references to QIDs of previously uploaded items
            for stmt in item.statements:
                if isinstance(stmt.value, str) and stmt.value.startswith("__LOCAL:"):
                    local_ref = stmt.value[len("__LOCAL:") :]
                    resolved_qid = created_qids.get(local_ref)
                    if resolved_qid:
                        stmt.value = resolved_qid

            result = self.upload_item(item)
            results.append(result)
            batch_count += 1

            # Remember the QID for future resolution (both new and existing items)
            # Also track "skipped" items so __LOCAL: references still resolve
            if result.qid and result.status in ("success", "exists", "skipped", "updated"):
                created_qids[item.local_id] = result.qid

            if entity_cb:
                entity_cb(item.local_id, result.status, result.qid, result.message)

            if progress_cb:
                progress_cb(idx + 1, total, result.message)

            # Pause between batches to avoid rate limiting (only in batch mode)
            if batch_size > 0 and batch_count >= batch_size and idx + 1 < total:
                batch_num = (idx + 1) // batch_size
                total_batches = (total + batch_size - 1) // batch_size
                msg = f"Batch {batch_num}/{total_batches} complete. Pausing 30s..."
                logger.info(msg)
                if progress_cb:
                    progress_cb(idx + 1, total, msg)
                time.sleep(30)
                batch_count = 0

        success = sum(1 for r in results if r.status in ("success", "exists"))
        failed = sum(1 for r in results if r.status == "failed")
        logger.info(
            "Upload complete: %d/%d succeeded, %d failed",
            success,
            total,
            failed,
        )
        return results

    @staticmethod
    def write_results(results: list[UploadResult], output_path: Path) -> Path:
        """Write upload results to a JSON file.

        Args:
            results: List of UploadResult instances.
            output_path: Destination file path.

        Returns:
            The output path written to.
        """
        data = [
            {
                "local_id": r.local_id,
                "qid": r.qid,
                "status": r.status,
                "message": r.message,
            }
            for r in results
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path
