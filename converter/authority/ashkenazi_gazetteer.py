"""Ashkenazi-community fallback gazetteer (CLAUDE.md Rule 60).

KIMA resolves MENA / Mediterranean / Levant toponyms well but is thin on
Ashkenazi diaspora communities (Prague, Worms, Kraków, Vilna, Frankfurt …)
whose Hebrew-script names and acronyms appear in manuscript provenance. This
module is a small, curated fallback consulted **only after KIMA misses**, so
no KIMA result is ever overridden.

``lookup(place_text)`` returns ``{lat, lon, wikidata_id}`` or ``None``.
``wikidata_uri(place_text)`` returns the ``https://www.wikidata.org/entity/Q…``
URI when the entry carries a verified QID (the shape ``KimaMatcher.match_place``
returns), else ``None``. Coordinates are real city centroids; nothing is
fabricated. Loaded once and cached.

This is the desktop mirror of the web ``app/pipeline/ashkenazi_gazetteer.py``;
keep the two data files (``data/ashkenazi_communities.json``) in sync.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# converter/authority → converter → repo root; data/ lives at the repo root.
_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "ashkenazi_communities.json"

_PUNCT_RE = re.compile(r"[\"'׳״“”‘’().,;:]")


def _normalise(text: str) -> str:
    """Casefold, drop quote/gershayim punctuation, collapse whitespace."""
    if not isinstance(text, str):
        return ""
    t = unicodedata.normalize("NFC", text).strip()
    t = _PUNCT_RE.sub("", t)
    t = re.sub(r"\s+", " ", t)
    return t.casefold()


@lru_cache(maxsize=1)
def _variant_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # noqa: BLE001
        logger.warning("ashkenazi gazetteer unavailable (%s): %s", _DATA_PATH, exc)
        return index
    for entry in raw.get("entries") or []:
        lat, lon = entry.get("lat"), entry.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        payload = {"lat": float(lat), "lon": float(lon), "wikidata_id": entry.get("qid")}
        for key in (entry.get("label_en", ""), *(entry.get("variants") or [])):
            norm = _normalise(key)
            if norm:
                index.setdefault(norm, payload)
    return index


def lookup(place_text: str) -> dict[str, Any] | None:
    """Resolve a place string to ``{lat, lon, wikidata_id}`` or ``None``."""
    norm = _normalise(place_text)
    if not norm:
        return None
    index = _variant_index()
    hit = index.get(norm)
    if hit is not None:
        return dict(hit)
    for key, payload in index.items():
        if len(key) >= 3 and (key in norm or norm in key):
            return dict(payload)
    return None


def wikidata_uri(place_text: str) -> str | None:
    """Return the Wikidata entity URI for a gazetteer hit with a QID, else None."""
    hit = lookup(place_text)
    if hit and hit.get("wikidata_id"):
        return f"https://www.wikidata.org/entity/{hit['wikidata_id']}"
    return None
