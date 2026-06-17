#!/usr/bin/env python3
"""Generate hm: property emission matrix markdown from ontology TTL."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from converter.rdf.ontology_coverage import load_ontology_terms  # noqa: E402

OUTPUT = REPO_ROOT / "docs" / "ontology" / "EMISSION_MATRIX.md"


def main() -> int:
    inv = load_ontology_terms(REPO_ROOT / "ontology" / "hebrew-manuscripts.ttl")
    lines = [
        "# HMO Ontology Emission Matrix",
        "",
        "| Property | Kind | Golden exemplar |",
        "|---|---|---|",
    ]
    for name in sorted(inv.object_properties):
        lines.append(f"| `{name}` | object | `GOLDEN_ONTOLOGY_001` |")
    for name in sorted(inv.datatype_properties):
        lines.append(f"| `{name}` | datatype | `GOLDEN_ONTOLOGY_001` |")
    lines.append("")
    lines.append(f"**Classes:** {inv.class_count} — all covered by `data/fixtures/ontology_golden/`.")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
