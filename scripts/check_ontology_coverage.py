#!/usr/bin/env python3
"""Check HMO ontology class/property coverage for an RDF graph or golden fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from converter.rdf.ontology_coverage import (  # noqa: E402
    build_coverage_report,
    check_against_baseline,
    write_coverage_report,
)
from converter.transformer.mapper import MarcToRdfMapper  # noqa: E402


def _build_graph_from_fixture(fixture_dir: Path):
    from converter.config.namespaces import bind_namespaces
    from rdflib import Graph

    manifest_path = fixture_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records_path = fixture_dir / str(manifest.get("records_file", "records.json"))
    if not records_path.exists():
        raise FileNotFoundError(f"Missing {records_path}")

    records = json.loads(records_path.read_text(encoding="utf-8"))
    mapper = MarcToRdfMapper()
    combined = Graph()
    bind_namespaces(combined)
    for rec in records:
        g = mapper.map_json_record(rec)
        for triple in g:
            combined.add(triple)
    return combined


def main() -> int:
    parser = argparse.ArgumentParser(description="Check HMO ontology RDF coverage")
    parser.add_argument("--ttl", type=Path, help="Path to manuscripts TTL")
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Path to ontology golden fixture directory",
    )
    parser.add_argument(
        "--ontology",
        type=Path,
        default=REPO_ROOT / "ontology" / "hebrew-manuscripts.ttl",
    )
    parser.add_argument("--output", type=Path, help="Write ontology_coverage.json here")
    parser.add_argument("--baseline", type=Path, help="Baseline JSON for regression check")
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 1 when coverage is below baseline",
    )
    args = parser.parse_args()

    if bool(args.ttl) == bool(args.fixture):
        if not args.ttl and not args.fixture:
            parser.error("Provide exactly one of --ttl or --fixture")
        if args.ttl and args.fixture:
            parser.error("Provide only one of --ttl or --fixture")

    if args.fixture:
        graph = _build_graph_from_fixture(args.fixture)
        report = build_coverage_report(None, args.ontology, graph=graph)
    else:
        report = build_coverage_report(args.ttl, args.ontology)

    summary = report.to_dict()
    print(
        f"Classes: {summary['classes_covered']}/{summary['classes_total']} "
        f"({100 * summary['classes_covered'] / max(summary['classes_total'], 1):.1f}%)"
    )
    print(
        f"Properties: {summary['properties_covered']}/{summary['properties_total']} "
        f"({100 * summary['properties_covered'] / max(summary['properties_total'], 1):.1f}%)"
    )
    if report.missing_classes:
        print(f"Missing classes ({len(report.missing_classes)}): {', '.join(report.missing_classes)}")
    if report.missing_properties:
        print(
            f"Missing properties ({len(report.missing_properties)}): "
            f"{', '.join(report.missing_properties[:20])}"
            + (" ..." if len(report.missing_properties) > 20 else "")
        )

    if args.output:
        write_coverage_report(report, args.output)
        print(f"Wrote {args.output}")

    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        errors = check_against_baseline(report, baseline)
        if errors:
            for err in errors:
                print(f"REGRESSION: {err}", file=sys.stderr)
            if args.fail_on_regression:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
