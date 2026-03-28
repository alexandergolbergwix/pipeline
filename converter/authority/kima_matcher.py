"""KIMA authority matcher for Hebrew historical place names.

Queries the local KIMA SQLite index to resolve Hebrew place names to
Wikidata URIs.  Falls back gracefully when the index is unavailable.

KIMA (https://kima.huc.knaw.nl) is an open, attestation-based,
historical database of place names in the Hebrew script maintained
by the Dutch Institute for Language and Speech Technology (HLT/INT)
and the National Library of Israel.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Repo-relative default index location
_DEFAULT_DB = Path(__file__).parents[2] / "data" / "kima" / "kima_index.db"


def _get_default_index_path() -> Path:
    return _DEFAULT_DB


class KimaMatcher:
    """Match Hebrew place names against the KIMA authority database.

    Returns Wikidata URIs (``https://www.wikidata.org/entity/Q…``) for
    matched places.  All results are cached per-instance.

    Args:
        index_path: Path to the KIMA SQLite index file.  Defaults to
            ``data/kima/kima_index.db`` relative to the repo root.
    """

    def __init__(self, index_path: str | None = None) -> None:
        if index_path is None:
            index_path = str(_get_default_index_path())
        self.index_path = index_path
        self._index: object = None
        self._cache: dict[str, str | None] = {}
        self._stats: dict[str, dict[str, int]] = {
            "matched": {"count": 0},
            "unmatched": {"count": 0},
        }

    # ── availability ──────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """True when the KIMA index file exists and is non-empty."""
        p = Path(self.index_path)
        return p.exists() and p.stat().st_size > 0

    @property
    def index(self) -> object:
        """Lazy-loaded KimaIndex instance."""
        if self._index is None and self.is_available:
            try:
                from converter.authority.kima_index import KimaIndex
                self._index = KimaIndex(self.index_path)
                logger.debug("KIMA index loaded from %s", self.index_path)
            except Exception as exc:
                logger.warning("Could not load KIMA index: %s", exc)
        return self._index

    # ── public API ────────────────────────────────────────────────────

    def match_place(self, name: str) -> Optional[str]:
        """Return the Wikidata URI for place *name*, or None if not found.

        The primary return value is a Wikidata entity URI
        (``https://www.wikidata.org/entity/Q…``).  100 % of KIMA records
        have a Wikidata identifier so no fallback is needed.
        """
        if not name:
            return None
        if name in self._cache:
            return self._cache[name]

        result = self._lookup(name)
        self._cache[name] = result
        if result:
            self._stats["matched"]["count"] += 1
        else:
            self._stats["unmatched"]["count"] += 1
        return result

    def get_stats(self) -> dict[str, int]:
        return {
            "matched": self._stats["matched"]["count"],
            "unmatched": self._stats["unmatched"]["count"],
        }

    def close(self) -> None:
        if self._index is not None:
            self._index.close()  # type: ignore[attr-defined]
            self._index = None

    def __enter__(self) -> KimaMatcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ── internals ─────────────────────────────────────────────────────

    def _lookup(self, name: str) -> Optional[str]:
        if not self.is_available:
            logger.debug("KIMA index not available; skipping lookup for %r", name)
            return None
        idx = self.index
        if idx is None:
            return None
        try:
            row = idx.lookup_place(name)  # type: ignore[attr-defined]
            if not row:
                return None
            wd = row.get("wikidata_id")
            if wd:
                return f"https://www.wikidata.org/entity/{wd}"
            # Fallback: VIAF URI
            viaf = row.get("viaf_id")
            if viaf:
                return f"https://viaf.org/viaf/{viaf}"
            return None
        except Exception as exc:
            logger.debug("KIMA lookup failed for %r: %s", name, exc)
            return None
