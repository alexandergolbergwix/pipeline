"""SHACL validation for Hebrew Manuscripts Ontology."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pyshacl
from rdflib import Graph


@dataclass
class ValidationViolation:
    """Represents a single SHACL validation violation."""

    focus_node: str
    path: str
    message: str
    severity: str = "Violation"
    value: str | None = None

    def __str__(self) -> str:
        base = f"[{self.severity}] {self.focus_node}"
        if self.path:
            base += f" -> {self.path}"
        base += f": {self.message}"
        if self.value:
            base += f" (value: {self.value})"
        return base


@dataclass
class ValidationResult:
    """Result of SHACL validation."""

    conforms: bool
    violations: list[ValidationViolation] = field(default_factory=list)
    results_graph: Graph | None = None
    results_text: str = ""

    @property
    def violation_count(self) -> int:
        """Number of violations found."""
        return len(self.violations)

    @property
    def is_valid(self) -> bool:
        """Whether the data conforms to all shapes."""
        return self.conforms

    def get_violations_by_severity(self, severity: str) -> list[ValidationViolation]:
        """Get violations filtered by severity.

        Args:
            severity: Severity level ('Violation', 'Warning', 'Info')

        Returns:
            List of violations matching severity
        """
        return [v for v in self.violations if v.severity == severity]

    def to_report(self) -> str:
        """Generate human-readable validation report.

        Returns:
            Formatted report string
        """
        lines = ["=" * 60]
        lines.append("SHACL VALIDATION REPORT")
        lines.append("=" * 60)
        lines.append("")

        errors = self.get_violations_by_severity("Violation")
        warnings = self.get_violations_by_severity("Warning")

        if self.conforms and not errors:
            lines.append("✓ Data conforms to all SHACL shapes")
            if warnings:
                lines.append(f"  ({len(warnings)} warning(s) - data quality notes)")
        elif not errors and warnings:
            lines.append(f"✓ Validation PASSED with {len(warnings)} warning(s)")
            lines.append("")
            lines.append("Warnings (data quality notes, not errors):")
            for i, w in enumerate(warnings[:10], 1):  # Show first 10
                lines.append(f"  {i}. {w}")
            if len(warnings) > 10:
                lines.append(f"  ... and {len(warnings) - 10} more warnings")
        else:
            lines.append(f"✗ Validation failed with {len(errors)} error(s)")
            if warnings:
                lines.append(f"  (plus {len(warnings)} warning(s))")
            lines.append("")

            lines.append("Errors:")
            for i, violation in enumerate(errors, 1):
                lines.append(f"{i}. {violation}")

            if warnings:
                lines.append("")
                lines.append("Warnings:")
                for i, w in enumerate(warnings[:5], 1):
                    lines.append(f"  {i}. {w}")
                if len(warnings) > 5:
                    lines.append(f"  ... and {len(warnings) - 5} more warnings")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)

    def to_detailed_report(
        self,
        output_path: str | None = None,
        input_file: str | None = None,
        stats: dict | None = None,
    ) -> str:
        """Generate a detailed validation report with all issues.

        Args:
            output_path: TTL output file path
            input_file: Input file path
            stats: Conversion statistics

        Returns:
            Path to the generated report file
        """
        errors = self.get_violations_by_severity("Violation")
        warnings = self.get_violations_by_severity("Warning")
        info = self.get_violations_by_severity("Info")

        # Group by issue type
        warnings_by_message = {}
        for w in warnings:
            key = w.message.split(":")[0] if ":" in w.message else w.message
            if key not in warnings_by_message:
                warnings_by_message[key] = []
            warnings_by_message[key].append(w)

        errors_by_message = {}
        for e in errors:
            key = e.message.split(":")[0] if ":" in e.message else e.message
            if key not in errors_by_message:
                errors_by_message[key] = []
            errors_by_message[key].append(e)

        lines = []
        lines.append("# SHACL Validation Report")
        lines.append("")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if input_file:
            lines.append(f"**Input File:** `{input_file}`")
        if output_path:
            lines.append(f"**Output File:** `{output_path}`")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        if self.conforms and not errors:
            lines.append("✅ **VALIDATION PASSED**")
        else:
            lines.append("❌ **VALIDATION FAILED**")
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|----------|-------|")
        lines.append(f"| Errors | {len(errors)} |")
        lines.append(f"| Warnings | {len(warnings)} |")
        lines.append(f"| Info | {len(info)} |")
        lines.append("")

        # Statistics if provided
        if stats:
            lines.append("## Conversion Statistics")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| Records Processed | {stats.get('records_processed', 'N/A'):,} |")
            lines.append(f"| Total Triples | {stats.get('total_triples', 'N/A'):,} |")
            lines.append(f"| Output Size | {stats.get('output_size_mb', 0):.2f} MB |")

            entities = stats.get("entity_counts", {})
            if entities:
                lines.append(f"| Manuscripts | {entities.get('manuscripts', 0):,} |")
                lines.append(f"| Persons | {entities.get('persons', 0):,} |")
                lines.append(f"| Works | {entities.get('works', 0):,} |")
                lines.append(f"| Expressions | {entities.get('expressions', 0):,} |")
                lines.append(f"| Places | {entities.get('places', 0):,} |")
            lines.append("")

        # Errors section
        if errors:
            lines.append("## Errors (Must Fix)")
            lines.append("")
            for msg_type, error_list in errors_by_message.items():
                lines.append(f"### {msg_type}")
                lines.append(f"**Count:** {len(error_list)}")
                lines.append("")
                lines.append("| Entity | Path | Value |")
                lines.append("|--------|------|-------|")
                for e in error_list[:50]:  # Limit to 50
                    entity_short = (
                        e.focus_node.split("#")[-1]
                        if "#" in e.focus_node
                        else e.focus_node.split("/")[-1]
                    )
                    path_short = e.path.split("#")[-1] if "#" in e.path else e.path.split("/")[-1]
                    value_safe = str(e.value).replace("|", "\\|") if e.value else ""
                    lines.append(f"| `{entity_short}` | `{path_short}` | {value_safe} |")
                if len(error_list) > 50:
                    lines.append(f"| ... | ... | *{len(error_list) - 50} more* |")
                lines.append("")

        # Warnings section
        if warnings:
            lines.append("## Warnings (Data Quality)")
            lines.append("")
            for msg_type, warning_list in warnings_by_message.items():
                lines.append(f"### {msg_type}")
                lines.append(f"**Count:** {len(warning_list)}")
                lines.append("")

                # Show sample
                lines.append("<details>")
                lines.append(
                    f"<summary>Show affected entities ({min(len(warning_list), 20)} of {len(warning_list)})</summary>"
                )
                lines.append("")
                for w in warning_list[:20]:
                    entity_short = (
                        w.focus_node.split("#")[-1]
                        if "#" in w.focus_node
                        else w.focus_node.split("/")[-1]
                    )
                    lines.append(f"- `{entity_short}`")
                if len(warning_list) > 20:
                    lines.append(f"- *... and {len(warning_list) - 20} more*")
                lines.append("")
                lines.append("</details>")
                lines.append("")

        # Recommendations
        lines.append("## Recommendations")
        lines.append("")

        if not errors and not warnings:
            lines.append("✅ No issues found. The data is valid and complete.")
        else:
            if errors:
                lines.append("### To fix errors:")
                lines.append("")
                for msg_type in errors_by_message.keys():
                    if "Height" in msg_type or "Width" in msg_type:
                        lines.append(
                            "- **Dimension errors**: Check source data for unrealistic measurements"
                        )
                    elif "label" in msg_type.lower():
                        lines.append(
                            "- **Missing labels**: Ensure all entities have descriptive labels"
                        )
                    else:
                        lines.append(f"- Review entities with: {msg_type}")
                lines.append("")

            if warnings:
                lines.append("### To address warnings:")
                lines.append("")
                if any("Expression" in k for k in warnings_by_message.keys()):
                    lines.append(
                        "- **Missing Expressions**: Some manuscripts lack linked content. This often indicates incomplete bibliographic data in the source MARC records."
                    )
                if any("Work" in k for k in warnings_by_message.keys()):
                    lines.append(
                        "- **Missing Works**: Some expressions lack work references. Use scholarly annotations to add work information."
                    )
                lines.append("")

        report_content = "\n".join(lines)

        # Save to file if output path provided
        if output_path:
            report_path = Path(output_path).with_suffix(".validation-report.md")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_content)
            return str(report_path)

        return report_content

    def to_json(self) -> dict:
        """Export validation results as JSON-serializable dict."""
        errors = self.get_violations_by_severity("Violation")
        warnings = self.get_violations_by_severity("Warning")

        return {
            "conforms": self.conforms,
            "summary": {
                "total_issues": len(self.violations),
                "errors": len(errors),
                "warnings": len(warnings),
            },
            "errors": [
                {"focus_node": v.focus_node, "path": v.path, "message": v.message, "value": v.value}
                for v in errors
            ],
            "warnings": [
                {"focus_node": v.focus_node, "path": v.path, "message": v.message, "value": v.value}
                for v in warnings
            ],
        }


class ShaclValidator:
    """Validates RDF data against SHACL shapes."""

    DEFAULT_SHAPES_PATH = Path(__file__).parent.parent.parent / "ontology" / "shacl-shapes.ttl"

    def __init__(self, shapes_path: Path | None = None):
        """Initialize the validator.

        Args:
            shapes_path: Path to SHACL shapes file. Uses default if not provided.
        """
        self.shapes_path = shapes_path or self.DEFAULT_SHAPES_PATH
        self._shapes_graph: Graph | None = None

    @property
    def shapes_graph(self) -> Graph:
        """Lazily load and cache shapes graph."""
        if self._shapes_graph is None:
            self._shapes_graph = Graph()
            if self.shapes_path.exists():
                self._shapes_graph.parse(str(self.shapes_path), format="turtle")
        return self._shapes_graph

    def validate(
        self, data_graph: Graph, inference: str = "none", abort_on_first: bool = False
    ) -> ValidationResult:
        """Validate data graph against SHACL shapes.

        Args:
            data_graph: RDF graph to validate
            inference: Inference mode ('none', 'rdfs', 'owlrl')
            abort_on_first: Stop after first violation

        Returns:
            ValidationResult with conformance status and violations
        """
        try:
            conforms, results_graph, results_text = pyshacl.validate(
                data_graph,
                shacl_graph=self.shapes_graph,
                inference=inference,
                abort_on_first=abort_on_first,
                meta_shacl=False,
                advanced=True,
                js=False,
                debug=False,
            )

            violations = self._parse_results(results_graph) if not conforms else []

            return ValidationResult(
                conforms=conforms,
                violations=violations,
                results_graph=results_graph,
                results_text=results_text,
            )

        except Exception as e:
            return ValidationResult(
                conforms=False,
                violations=[
                    ValidationViolation(
                        focus_node="",
                        path="",
                        message=f"Validation error: {str(e)}",
                        severity="Error",
                    )
                ],
                results_text=str(e),
            )

    def validate_file(self, file_path: Path, format: str = "turtle") -> ValidationResult:
        """Validate an RDF file against SHACL shapes.

        Args:
            file_path: Path to RDF file
            format: RDF format ('turtle', 'xml', 'n3', etc.)

        Returns:
            ValidationResult
        """
        data_graph = Graph()
        data_graph.parse(str(file_path), format=format)
        return self.validate(data_graph)

    def _parse_results(self, results_graph: Graph) -> list[ValidationViolation]:
        """Parse SHACL results graph into violation objects.

        Args:
            results_graph: SHACL validation results graph

        Returns:
            List of ValidationViolation objects
        """
        violations = []

        SH = "http://www.w3.org/ns/shacl#"

        query = f"""
        PREFIX sh: <{SH}>
        
        SELECT ?focusNode ?path ?message ?severity ?value
        WHERE {{
            ?result a sh:ValidationResult ;
                    sh:focusNode ?focusNode .
            OPTIONAL {{ ?result sh:resultPath ?path }}
            OPTIONAL {{ ?result sh:resultMessage ?message }}
            OPTIONAL {{ ?result sh:resultSeverity ?severity }}
            OPTIONAL {{ ?result sh:value ?value }}
        }}
        """

        try:
            results = results_graph.query(query)

            for row in results:
                focus = str(row.focusNode) if row.focusNode else ""
                path = str(row.path) if row.path else ""
                message = str(row.message) if row.message else "Validation failed"

                severity_str = "Violation"
                if row.severity:
                    sev = str(row.severity)
                    if "Warning" in sev:
                        severity_str = "Warning"
                    elif "Info" in sev:
                        severity_str = "Info"

                # Treat data quality messages as warnings (SPARQL constraints
                # don't always report severity correctly)
                if message and any(
                    msg in message
                    for msg in [
                        "should embody at least one Expression",
                        "Expression should realize a Work",
                        "should have at least one",
                    ]
                ):
                    severity_str = "Warning"

                value = str(row.value) if row.value else None

                violations.append(
                    ValidationViolation(
                        focus_node=focus,
                        path=path,
                        message=message,
                        severity=severity_str,
                        value=value,
                    )
                )

        except Exception:
            pass

        return violations

    def reload_shapes(self, shapes_path: Path | None = None):
        """Reload shapes from file.

        Args:
            shapes_path: Optional new path to shapes file
        """
        if shapes_path:
            self.shapes_path = shapes_path
        self._shapes_graph = None
        _ = self.shapes_graph


def validate_graph(data_graph: Graph, shapes_path: Path | None = None) -> ValidationResult:
    """Convenience function to validate a graph.

    Args:
        data_graph: RDF graph to validate
        shapes_path: Optional path to SHACL shapes

    Returns:
        ValidationResult
    """
    validator = ShaclValidator(shapes_path)
    return validator.validate(data_graph)
