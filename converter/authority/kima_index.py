"""KIMA (Hebrew place names) SQLite authority index.

Builds and queries a local SQLite index from the three KIMA TSV files:
  - '20251015 Kima places.tsv'   — 48 128 primary place records
  - 'Kima-Hebrew-Variants-20250929.tsv' — 48 225 Hebrew name variants
  - 'Maagarim-Zurot-&-Arachim.tsv'     — 14 522 grammatical word-forms

Usage::

    from converter.authority.kima_index import KimaIndex, build_kima_index

    # build once from the TSV directory
    build_kima_index("/path/to/data/kima", "/path/to/kima_index.db")

    # query at runtime
    idx = KimaIndex("/path/to/kima_index.db")
    result = idx.lookup_place("ירושלים")  # -> dict or None
    idx.close()
"""

from __future__ import annotations

import csv
import logging
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

# ── file name patterns ────────────────────────────────────────────────
_PLACES_GLOB = "*Kima places*"
_VARIANTS_GLOB = "*Kima*Variants*"
_MAAGARIM_GLOB = "Maagarim*"


def _find_tsv(directory: Path, glob: str) -> Path | None:
    matches = list(directory.glob(glob))
    if not matches:
        return None
    return sorted(matches)[0]


# ── SQLite schema ─────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS places (
    kima_id     INTEGER PRIMARY KEY,
    primary_heb TEXT,
    primary_rom TEXT,
    wikidata_id TEXT,
    viaf_id     TEXT,
    geonames_id TEXT,
    mazal_nli_id TEXT,
    lat         REAL,
    lon         REAL
);

CREATE TABLE IF NOT EXISTS name_index (
    normalized_name TEXT NOT NULL,
    kima_id         INTEGER NOT NULL,
    script          TEXT,
    FOREIGN KEY (kima_id) REFERENCES places(kima_id)
);

CREATE INDEX IF NOT EXISTS idx_name ON name_index(normalized_name);
"""


class KimaIndex:
    """Low-level SQLite interface for KIMA authority data.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    # ── schema ────────────────────────────────────────────────────────

    def create_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ── name normalization ────────────────────────────────────────────

    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalize a place name for fuzzy lookup.

        Steps:
        1. Strip trailing parenthetical geopolitical context — "(ישראל)", "(Italy)"
        2. Remove Hebrew niqqud (U+0591–U+05C7)
        3. NFD decompose + strip combining diacritics
        4. Remove punctuation (except hyphens and apostrophes)
        5. Collapse whitespace, lowercase
        """
        if not name:
            return ""
        # Strip trailing (...) — geopolitical context added by KIMA
        name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
        # Remove Hebrew niqqud
        name = re.sub(r"[\u0591-\u05C7]", "", name)
        # NFD normalization + remove combining characters
        name = "".join(
            c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c)
        )
        # Remove punctuation except hyphens and apostrophes
        name = re.sub(r"[^\w\s\-']", "", name, flags=re.UNICODE)
        return re.sub(r"\s+", " ", name).strip().lower()

    # ── inserts ───────────────────────────────────────────────────────

    def insert_place(
        self,
        kima_id: int,
        primary_heb: str,
        primary_rom: str,
        wikidata_id: str,
        viaf_id: str,
        geonames_id: str,
        mazal_nli_id: str,
        lat: float | None,
        lon: float | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO places
              (kima_id, primary_heb, primary_rom, wikidata_id, viaf_id,
               geonames_id, mazal_nli_id, lat, lon)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                kima_id,
                primary_heb,
                primary_rom,
                wikidata_id or None,
                viaf_id or None,
                geonames_id or None,
                mazal_nli_id or None,
                lat,
                lon,
            ),
        )

    def insert_name_variant(self, normalized_name: str, kima_id: int, script: str) -> None:
        if not normalized_name:
            return
        self.conn.execute(
            "INSERT INTO name_index (normalized_name, kima_id, script) VALUES (?,?,?)",
            (normalized_name, kima_id, script),
        )

    # ── lookup ────────────────────────────────────────────────────────

    def lookup_place(self, name: str) -> dict | None:
        """Return place record dict for *name*, or None if not found."""
        normalized = self.normalize_name(name)
        if not normalized:
            return None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT p.kima_id, p.primary_heb, p.primary_rom,
                   p.wikidata_id, p.viaf_id, p.geonames_id, p.lat, p.lon
            FROM name_index n
            JOIN places p ON n.kima_id = p.kima_id
            WHERE n.normalized_name = ?
            LIMIT 1
            """,
            (normalized,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def stats(self) -> dict:
        """Return row counts for monitoring."""
        c = self.conn.cursor()
        places = c.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        names = c.execute("SELECT COUNT(*) FROM name_index").fetchone()[0]
        return {"places": places, "name_variants": names}

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> KimaIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── index builder ─────────────────────────────────────────────────────


def build_kima_index(
    tsv_dir: str,
    db_path: str,
    verbose: bool = False,
    progress_cb: object = None,
) -> None:
    """Build a KIMA SQLite authority index from the TSV source files.

    Args:
        tsv_dir:     Directory containing the three KIMA TSV files.
        db_path:     Path for the output SQLite database.
        verbose:     If True, log progress to the root logger.
        progress_cb: Optional callable(int) — receives percent complete 0–100.
    """
    # Some KIMA rows contain very long URL fields; raise the csv field limit once.
    csv.field_size_limit(sys.maxsize)

    directory = Path(tsv_dir)

    places_file = _find_tsv(directory, _PLACES_GLOB)
    variants_file = _find_tsv(directory, _VARIANTS_GLOB)
    maagarim_file = _find_tsv(directory, _MAAGARIM_GLOB)

    if not places_file:
        raise FileNotFoundError(f"No KIMA places file matching '{_PLACES_GLOB}' in {directory}")
    if not variants_file:
        raise FileNotFoundError(f"No KIMA variants file matching '{_VARIANTS_GLOB}' in {directory}")

    if verbose:
        logger.info("Building KIMA index from %s", directory)
        logger.info("  Places:   %s", places_file.name)
        logger.info("  Variants: %s", variants_file.name)
        if maagarim_file:
            logger.info("  Maagarim: %s", maagarim_file.name)

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    index = KimaIndex(db_path)
    index.create_schema()

    # ── Phase 1: load places (40 % of progress) ───────────────────────
    total_places = 0
    with open(places_file, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            kima_id_raw = (row.get("Id") or "").strip()
            if not kima_id_raw:
                continue
            try:
                kima_id = int(kima_id_raw)
            except ValueError:
                logger.debug("Skipping malformed places row with Id=%r", kima_id_raw)
                continue

            lat_raw = (row.get("lat") or "").strip()
            lon_raw = (row.get("lon") or "").strip()
            lat = float(lat_raw) if lat_raw else None
            lon = float(lon_raw) if lon_raw else None

            heb = (row.get("primary_heb_full") or "").strip()
            rom = (row.get("primary_rom_full") or "").strip()

            index.insert_place(
                kima_id=kima_id,
                primary_heb=heb,
                primary_rom=rom,
                wikidata_id=(row.get("WD") or "").strip(),
                viaf_id=(row.get("VIAF_ID") or "").strip(),
                geonames_id=(row.get("Geoname_ID") or "").strip(),
                mazal_nli_id=(row.get("MAZAL_ID") or "").strip(),
                lat=lat,
                lon=lon,
            )

            # Index primary Hebrew name (with and without geopolitical context)
            norm_heb = KimaIndex.normalize_name(heb)
            if norm_heb:
                index.insert_name_variant(norm_heb, kima_id, "heb")
            # Also index just the raw base name stripped of context
            heb_base = re.sub(r"\s*\([^)]*\)\s*$", "", heb).strip()
            norm_heb_base = KimaIndex.normalize_name(heb_base)
            if norm_heb_base and norm_heb_base != norm_heb:
                index.insert_name_variant(norm_heb_base, kima_id, "heb")

            # Index primary romanized name
            norm_rom = KimaIndex.normalize_name(rom)
            if norm_rom:
                index.insert_name_variant(norm_rom, kima_id, "rom")

            total_places += 1
            if total_places % 10_000 == 0:
                index.conn.commit()
                if verbose:
                    logger.info("  %d places loaded…", total_places)

    index.conn.commit()
    if verbose:
        logger.info("Phase 1 complete: %d places", total_places)
    if progress_cb:
        progress_cb(40)

    # ── Phase 2: load Hebrew variants (85 % of progress) ─────────────
    total_variants = 0
    with open(variants_file, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            place_id_raw = (row.get("PlaceId") or "").strip()
            variant = (row.get("variant") or "").strip()
            if not place_id_raw or not variant:
                continue
            try:
                kima_id = int(place_id_raw)
            except ValueError:
                logger.debug("Skipping malformed variants row with PlaceId=%r", place_id_raw)
                continue
            norm = KimaIndex.normalize_name(variant)
            if norm:
                index.insert_name_variant(norm, kima_id, "heb")

            total_variants += 1
            if total_variants % 10_000 == 0:
                index.conn.commit()

    index.conn.commit()
    if verbose:
        logger.info("Phase 2 complete: %d variants", total_variants)
    if progress_cb:
        progress_cb(85)

    # ── Phase 3: load Maagarim grammatical forms (100 % of progress) ──
    total_maagarim = 0
    if maagarim_file:
        # Maagarim entries link word forms to dictionary entries, not to KIMA
        # place IDs directly.  We match on the canonical 'word' field against
        # the name_index we already built to find the kima_id, then add the
        # grammatical 'ZURA' form as an additional variant.
        # Build a fast word → kima_id lookup from the index we just created.
        word_to_kima: dict[str, int] = {}
        cur = index.conn.cursor()
        cur.execute("SELECT normalized_name, kima_id FROM name_index WHERE script='heb'")
        for norm_name, kid in cur.fetchall():
            if norm_name not in word_to_kima:
                word_to_kima[norm_name] = kid

        with open(maagarim_file, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                word = (row.get("word") or "").strip()
                zura = (row.get("ZURA") or "").strip()
                if not word or not zura or zura == word:
                    continue
                norm_word = KimaIndex.normalize_name(word)
                kima_id = word_to_kima.get(norm_word)
                if kima_id is None:
                    continue
                norm_zura = KimaIndex.normalize_name(zura)
                if norm_zura:
                    index.insert_name_variant(norm_zura, kima_id, "heb")
                    total_maagarim += 1

        index.conn.commit()
        if verbose:
            logger.info("Phase 3 complete: %d Maagarim forms linked", total_maagarim)

    if progress_cb:
        progress_cb(100)

    st = index.stats()
    if verbose:
        logger.info(
            "KIMA index complete — %d places, %d name variants → %s",
            st["places"],
            st["name_variants"],
            db_path,
        )
    index.close()
