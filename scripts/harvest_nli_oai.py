"""Harvest NLI Hebrew manuscript MARC records via OAI-PMH.

Downloads MARC 21 XML records from the NLI OAI-PMH feed and writes them as
TSV files that plug directly into ner/train_genre_classifier.py as additional
distant-supervision training data.

Each output row contains:
  - record_id   (MARC 001 control number)
  - title       (MARC 245$a$b joined)
  - notes       (MARC 500$a pipe-separated, first 5)
  - genres      (MARC 655$a pipe-separated)

Only records with at least one MARC 655 genre heading are written to the genre
TSV, making the file ready for training without further filtering.

Usage:
    # Harvest (dry run — preview first 100 records):
    python scripts/harvest_nli_oai.py --base-url <OAI_URL> --dry-run

    # Full harvest (slow — may take hours for 100k records):
    python scripts/harvest_nli_oai.py \\
        --base-url <OAI_URL> \\
        --set manuscripts \\
        --output data/tsvs/nli_harvested.tsv \\
        --max-records 100000

NLI OAI-PMH access notes:
    The NLI runs Ex Libris Alma. Their OAI-PMH endpoint is NOT publicly accessible —
    it requires institutional IP whitelisting or a special integration profile.

    Steps to obtain access:
    1. Contact NLI technical team at digital@nli.org.il or via:
       https://www.nli.org.il/en/at-your-service/librarians
    2. Request the Alma OAI-PMH base URL and your institutional access code.
    3. Ask whether the "manuscripts" set name is correct for MARC~655 genre records.

    Expected URL format once authorized:
        https://nli.alma.exlibrisgroup.com/view/oai/<INST_CODE>/request

    Alternative: the NLI offers a modern REST API at https://api.nli.org.il/ —
    see also https://api2.nli.org.il/docs/ for IIIF/search endpoints that may
    expose MARC metadata without OAI-PMH access.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree as ET

import requests

# XML namespaces used in OAI-PMH + MARC 21 XML responses
_OAI_NS = "http://www.openarchives.org/OAI/2.0/"
_MARC_NS = "http://www.loc.gov/MARC21/slim"

_NS = {
    "oai": _OAI_NS,
    "marc": _MARC_NS,
}


# ── MARC XML parsing ──────────────────────────────────────────────────


def _get_subfields(record_el: ET.Element, tag: str, codes: str) -> list[str]:
    """Extract subfield values from a MARC datafield."""
    values: list[str] = []
    for df in record_el.findall(f"marc:datafield[@tag='{tag}']", _NS):
        for code in codes:
            for sf in df.findall(f"marc:subfield[@code='{code}']", _NS):
                if sf.text and sf.text.strip():
                    values.append(sf.text.strip())
    return values


def _get_controlfield(record_el: ET.Element, tag: str) -> str:
    cf = record_el.find(f"marc:controlfield[@tag='{tag}']", _NS)
    return (cf.text or "").strip() if cf is not None else ""


def parse_marc_record(record_el: ET.Element) -> dict[str, str] | None:
    """Parse a MARC 21 XML record element into a flat dict."""
    record_id = _get_controlfield(record_el, "001")
    if not record_id:
        return None

    title_parts = _get_subfields(record_el, "245", "ab")
    title = " ".join(title_parts).strip().rstrip("/: .,")

    notes = _get_subfields(record_el, "500", "a")[:5]
    genres = _get_subfields(record_el, "655", "a")

    return {
        "record_id": record_id,
        "title": title,
        "notes": "|".join(notes),
        "genres": "|".join(genres),
    }


# ── OAI-PMH harvesting ────────────────────────────────────────────────


def _oai_request(session: requests.Session, base_url: str, params: dict) -> ET.Element:
    """Make a single OAI-PMH request and return the parsed XML root."""
    resp = session.get(base_url, params=params, timeout=60)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    # Check for OAI error
    error_el = root.find("oai:error", _NS)
    if error_el is not None:
        code = error_el.get("code", "unknown")
        msg = error_el.text or ""
        raise RuntimeError(f"OAI error {code}: {msg}")
    return root


def harvest_records(
    base_url: str,
    metadata_prefix: str = "marc21",
    oai_set: str | None = None,
    max_records: int | None = None,
    sleep_between: float = 1.0,
) -> Iterator[dict[str, str]]:
    """Yield parsed MARC record dicts from an OAI-PMH feed.

    Handles resumption tokens automatically. Sleeps between requests to
    avoid overwhelming the server.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "MHM-Pipeline-Harvester/1.0 (research; digital@nli.org.il)"

    params: dict[str, str] = {
        "verb": "ListRecords",
        "metadataPrefix": metadata_prefix,
    }
    if oai_set:
        params["set"] = oai_set

    total_yielded = 0

    while True:
        root = _oai_request(session, base_url, params)

        list_records = root.find("oai:ListRecords", _NS)
        if list_records is None:
            break

        for oai_record in list_records.findall("oai:record", _NS):
            # Skip deleted records
            header = oai_record.find("oai:header", _NS)
            if header is not None and header.get("status") == "deleted":
                continue

            metadata = oai_record.find("oai:metadata", _NS)
            if metadata is None:
                continue

            marc_record = metadata.find("marc:record", _NS)
            if marc_record is None:
                continue

            parsed = parse_marc_record(marc_record)
            if parsed:
                yield parsed
                total_yielded += 1
                if max_records and total_yielded >= max_records:
                    return

        # Check for resumption token
        token_el = list_records.find("oai:resumptionToken", _NS)
        if token_el is None or not (token_el.text or "").strip():
            break

        token = token_el.text.strip()
        complete_list_size = token_el.get("completeListSize")
        if complete_list_size:
            print(
                f"  Progress: {total_yielded} / {complete_list_size} records harvested",
                flush=True,
            )

        # Next request uses only the resumption token
        params = {"verb": "ListRecords", "resumptionToken": token}
        time.sleep(sleep_between)


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest NLI manuscript records via OAI-PMH")
    parser.add_argument(
        "--base-url", required=True,
        help="OAI-PMH base URL (e.g. https://nli.alma.exlibrisgroup.com/view/oai/972NLI_INST/request)",
    )
    parser.add_argument("--set", default=None, help="OAI-PMH set name (e.g. 'manuscripts')")
    parser.add_argument("--metadata-prefix", default="marc21", help="OAI metadata prefix")
    parser.add_argument(
        "--output", default="data/tsvs/nli_harvested.tsv",
        help="Output TSV path (all records)",
    )
    parser.add_argument(
        "--genres-output", default="data/tsvs/nli_harvested_with_genres.tsv",
        help="Output TSV path (only records with MARC 655 genre headings)",
    )
    parser.add_argument("--max-records", type=int, default=None, help="Stop after N records")
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between requests")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print first 10 parsed records and exit without writing",
    )
    args = parser.parse_args()

    print(f"OAI-PMH harvest from: {args.base_url}")
    if args.set:
        print(f"Set filter: {args.set}")
    print(f"Metadata prefix: {args.metadata_prefix}")
    if args.max_records:
        print(f"Max records: {args.max_records}")
    print()

    out_path = Path(args.output)
    genres_path = Path(args.genres_output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["record_id", "title", "notes", "genres"]

    if args.dry_run:
        print("--- DRY RUN: first 10 records ---")
        for i, rec in enumerate(harvest_records(
            args.base_url, args.metadata_prefix, args.set,
            max_records=10, sleep_between=args.sleep,
        )):
            print(f"\nRecord {i + 1}: {rec['record_id']}")
            print(f"  Title:  {rec['title'][:80]}")
            print(f"  Notes:  {rec['notes'][:80]}")
            print(f"  Genres: {rec['genres']}")
        return

    total = 0
    with_genres = 0

    with (
        open(out_path, "w", newline="", encoding="utf-8") as f_all,
        open(genres_path, "w", newline="", encoding="utf-8") as f_genres,
    ):
        writer_all = csv.DictWriter(f_all, fieldnames=fieldnames, delimiter="\t")
        writer_genres = csv.DictWriter(f_genres, fieldnames=fieldnames, delimiter="\t")
        writer_all.writeheader()
        writer_genres.writeheader()

        for rec in harvest_records(
            args.base_url, args.metadata_prefix, args.set,
            max_records=args.max_records, sleep_between=args.sleep,
        ):
            writer_all.writerow(rec)
            total += 1

            if rec["genres"]:
                writer_genres.writerow(rec)
                with_genres += 1

            if total % 1000 == 0:
                print(f"  {total} records total, {with_genres} with genres", flush=True)

    print(f"\nDone.")
    print(f"  Total records:        {total}")
    print(f"  Records with genres:  {with_genres} ({100 * with_genres / max(total, 1):.1f}%)")
    print(f"  All records:          {out_path}")
    print(f"  Genre-labeled only:   {genres_path}")
    print(f"\nTo retrain the genre classifier with harvested data:")
    print(f"  PYTHONPATH=src:. .venv/bin/python ner/train_genre_classifier.py \\")
    print(f"    --tsv-files {genres_path} data/tsvs/17th_century_samples.tsv data/tsvs/top100_richest.tsv")


if __name__ == "__main__":
    main()
