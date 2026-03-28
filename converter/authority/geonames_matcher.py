"""GeoNames authority matcher.

Queries the GeoNames JSON search API to resolve place names to GeoNames URIs.

Free registration at https://www.geonames.org/login gives a username with
2 000 credits/hour (one credit per API call).  Pass the username at construction
time or set the GEONAMES_USERNAME environment variable.

GeoNames search endpoint: http://api.geonames.org/searchJSON
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_GEONAMES_SEARCH = "http://api.geonames.org/searchJSON"
_TIMEOUT = 8
_RATE_LIMIT = 0.2     # 5 req/s — well within the 2 000/hour free tier


class GeoNamesMatcher:
    """Match place names against the GeoNames geographic authority file.

    Returns URIs of the form ``https://www.geonames.org/{geonameId}``.
    All results are cached per-instance.

    Args:
        username: GeoNames API username.  Falls back to the
            ``GEONAMES_USERNAME`` environment variable, then ``"demo"``
            (limited to a few requests for testing only).
        feature_classes: GeoNames feature classes to accept.  Defaults to
            populated places (P) and administrative areas (A).
    """

    def __init__(
        self,
        username: str | None = None,
        feature_classes: tuple[str, ...] = ("P", "A"),
    ) -> None:
        self._username = (
            username
            or os.environ.get("GEONAMES_USERNAME", "demo")
        )
        if self._username == "demo":
            logger.warning(
                "GeoNames username is 'demo' — limited to a few test requests. "
                "Register a free account at https://www.geonames.org/login."
            )
        self._feature_classes = feature_classes
        self._cache: dict[str, str | None] = {}
        self._last_request: float = 0.0

    # ── public API ────────────────────────────────────────────────────

    def match_place(self, name: str) -> Optional[str]:
        """Return the GeoNames URI for *name*, or None if not found.

        The best matching populated place or administrative area is returned.
        Historic / transliterated Hebrew place names may not resolve; results
        are best-effort.
        """
        if name in self._cache:
            return self._cache[name]

        result = self._query_api(name)
        self._cache[name] = result
        return result

    # ── internals ─────────────────────────────────────────────────────

    def _query_api(self, name: str) -> Optional[str]:
        elapsed = time.monotonic() - self._last_request
        if elapsed < _RATE_LIMIT:
            time.sleep(_RATE_LIMIT - elapsed)

        params: dict[str, str | int] = {
            "q": name,
            "maxRows": 3,
            "username": self._username,
            "style": "SHORT",
        }
        try:
            resp = requests.get(_GEONAMES_SEARCH, params=params, timeout=_TIMEOUT)
            self._last_request = time.monotonic()
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("GeoNames request failed for %r: %s", name, exc)
            self._last_request = time.monotonic()
            return None

        if "status" in data:
            logger.warning("GeoNames API error for %r: %s", name, data["status"])
            return None

        geonames = data.get("geonames") or []
        # Prefer entries whose feature class is in the allowed set
        for entry in geonames:
            if entry.get("fcl") in self._feature_classes:
                geoname_id = entry.get("geonameId")
                if geoname_id:
                    return f"https://www.geonames.org/{geoname_id}"

        # Fall back to the top result regardless of feature class
        if geonames:
            geoname_id = geonames[0].get("geonameId")
            if geoname_id:
                return f"https://www.geonames.org/{geoname_id}"

        return None
