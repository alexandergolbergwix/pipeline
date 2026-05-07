"""Compute Stage 3 org-routing audit deltas between two AuthorityWorker outputs.

Usage:
    python scripts/verify_org_routing.py BEFORE.json AFTER.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(path: str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _is_no_match(m: dict) -> bool:
    return not (m.get("mazal_id") or m.get("viaf_uri") or m.get("wikidata_qid"))


def _resolve_is_institutional() -> object:
    """Resolve `is_institutional_name` (public) or fall back to the private alias.

    Agent A renames the helper from ``_is_institutional_name`` to
    ``is_institutional_name``. Until that lands, fall back to the private
    name so this script still runs against the BEFORE corpus.
    """
    sys.path.insert(0, "src")
    sys.path.insert(0, ".")
    from converter.wikidata import item_builder

    fn = getattr(item_builder, "is_institutional_name", None)
    if fn is None:
        fn = getattr(item_builder, "_is_institutional_name")
    return fn


def _stats(records: list[dict]) -> dict[str, int]:
    is_institutional_name = _resolve_is_institutional()

    total_matches = 0
    no_match_total = 0
    no_match_org = 0
    no_match_person = 0
    by_field = {"100/110/111": 0, "700/710/711": 0, "other": 0}
    wikidata_source = 0
    matched_one = 0
    person_side = 0

    for r in records:
        for m in r.get("marc_authority_matches") or []:
            total_matches += 1
            field = m.get("field", "other")
            if field in by_field:
                by_field[field] += 1
            else:
                by_field["other"] += 1
            if m.get("source") == "wikidata":
                wikidata_source += 1
            if m.get("matched") == 1:
                matched_one += 1
            if _is_no_match(m):
                no_match_total += 1
                name = str(m.get("name") or "")
                if is_institutional_name(name):
                    no_match_org += 1
                else:
                    no_match_person += 1
            if m.get("entity_kind") != "organization":
                person_side += 1

    return {
        "total_matches": total_matches,
        "by_field_100_110_111": by_field["100/110/111"],
        "by_field_700_710_711": by_field["700/710/711"],
        "by_field_other": by_field["other"],
        "no_match_total": no_match_total,
        "no_match_org": no_match_org,
        "no_match_person": no_match_person,
        "source_wikidata": wikidata_source,
        "matched_one": matched_one,
        "person_side_rows": person_side,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    before = _load(sys.argv[1])
    after = _load(sys.argv[2])
    sb = _stats(before)
    sa = _stats(after)
    label_w = max(len(k) for k in sb)
    print(f"{'metric':<{label_w}}  {'before':>8}  {'after':>8}  {'delta':>8}")
    print("-" * (label_w + 30))
    for k in sb:
        b, a = sb[k], sa[k]
        d = a - b
        sign = "+" if d > 0 else ""
        print(f"{k:<{label_w}}  {b:>8}  {a:>8}  {sign}{d:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
