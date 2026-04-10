"""Export Wikidata items as QuickStatements v2 format.

QuickStatements is a batch-editing tool for Wikidata that accepts
tab-separated commands. This module generates QuickStatements text
for dry-run review and manual upload via the web tool.

See: https://www.wikidata.org/wiki/Help:QuickStatements
"""

from __future__ import annotations

import logging
from pathlib import Path

from converter.wikidata.item_builder import WikidataItem, WikidataStatement

logger = logging.getLogger(__name__)


def _escape_qs(value: str) -> str:
    """Escape a string for QuickStatements format."""
    return value.replace('"', '\\"').replace("\n", " ").replace("\t", " ")


def _format_value(stmt: WikidataStatement) -> str:
    """Format a statement value for QuickStatements v2.

    Args:
        stmt: The WikidataStatement to format.

    Returns:
        QuickStatements-compatible value string.
    """
    if stmt.value_type == "item":
        value = str(stmt.value)
        if value.startswith("__LOCAL:"):
            return f'"{_escape_qs(value)}"'
        return value

    if stmt.value_type == "string":
        return f'"{_escape_qs(str(stmt.value))}"'

    if stmt.value_type == "external-id":
        return f'"{_escape_qs(str(stmt.value))}"'

    if stmt.value_type == "time":
        precision = stmt.precision
        return f"{stmt.value}/{precision}"

    if stmt.value_type == "quantity":
        if stmt.unit:
            return f"{stmt.value}U{stmt.unit}"
        return str(stmt.value)

    if stmt.value_type == "url":
        return f'"{_escape_qs(str(stmt.value))}"'

    if stmt.value_type == "monolingualtext":
        lang = stmt.language or "he"
        return f'{lang}:"{_escape_qs(str(stmt.value))}"'

    return f'"{_escape_qs(str(stmt.value))}"'


def _format_reference(ref_snak: dict[str, str]) -> str:
    """Format a single reference snak for QuickStatements.

    Reference snaks use S-prefix (e.g., S248 instead of P248).

    Args:
        ref_snak: Dict with 'property', 'value', 'type' keys.

    Returns:
        Tab-separated reference components.
    """
    pid = str(ref_snak.get("property", "")).replace("P", "S")
    value = ref_snak.get("value", "")
    value_type = ref_snak.get("type", "string")

    if value_type == "item":
        return f"{pid}\t{value}"
    if value_type == "url":
        return f'{pid}\t"{_escape_qs(value)}"'
    if value_type == "time":
        precision = ref_snak.get("precision", 11)
        return f"{pid}\t{value}/{precision}"
    return f'{pid}\t"{_escape_qs(value)}"'


class QuickStatementsExporter:
    """Export WikidataItem instances to QuickStatements v2 text format.

    Usage::

        exporter = QuickStatementsExporter()
        text = exporter.export(items)
        exporter.export_to_file(items, Path("quickstatements.txt"))
    """

    def export_item(self, item: WikidataItem) -> str:
        """Export a single WikidataItem to QuickStatements lines.

        Args:
            item: The WikidataItem to export.

        Returns:
            Multi-line QuickStatements text for this item.
        """
        lines: list[str] = []

        if item.existing_qid:
            qid = item.existing_qid
        else:
            lines.append("CREATE")
            qid = "LAST"

        # Labels
        for lang, label in item.labels.items():
            lines.append(f'{qid}\tL{lang}\t"{_escape_qs(label)}"')

        # Descriptions
        for lang, desc in item.descriptions.items():
            lines.append(f'{qid}\tD{lang}\t"{_escape_qs(desc)}"')

        # Aliases
        for lang, alias_list in item.aliases.items():
            for alias in alias_list:
                lines.append(f'{qid}\tA{lang}\t"{_escape_qs(alias)}"')

        # Statements
        for stmt in item.statements:
            value_str = _format_value(stmt)
            line_parts = [qid, stmt.property_id, value_str]

            # Add references
            for ref_snak in stmt.references:
                line_parts.append(_format_reference(ref_snak))

            lines.append("\t".join(line_parts))

        return "\n".join(lines)

    def export(self, items: list[WikidataItem]) -> str:
        """Export all items to QuickStatements v2 format.

        Args:
            items: List of WikidataItem instances.

        Returns:
            Complete QuickStatements text.
        """
        blocks: list[str] = []

        # Separate by type: persons first (they need QIDs before manuscripts reference them)
        persons = [i for i in items if i.entity_type == "person" and not i.existing_qid]
        manuscripts = [i for i in items if i.entity_type == "manuscript"]

        # Add header comment
        blocks.append(
            "/* MHM Pipeline — Wikidata QuickStatements Export */\n"
            "/* Persons (create first, then manuscripts reference them) */\n"
        )

        for person in persons:
            blocks.append(self.export_item(person))
            blocks.append("")  # Blank line between items

        blocks.append("\n/* Manuscripts */\n")

        for ms in manuscripts:
            blocks.append(self.export_item(ms))
            blocks.append("")

        return "\n".join(blocks)

    def export_to_file(
        self, items: list[WikidataItem], output_path: Path,
    ) -> Path:
        """Write QuickStatements text to a file.

        Args:
            items: List of WikidataItem instances.
            output_path: Destination file path.

        Returns:
            The output path written to.
        """
        text = self.export(items)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        logger.info(
            "Exported %d items to QuickStatements: %s",
            len(items), output_path,
        )
        return output_path
