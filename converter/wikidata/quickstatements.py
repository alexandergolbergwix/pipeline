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
    # Rule 42: somevalue/novalue are native QS v2 tokens (no quotes).
    if stmt.value_type == "somevalue":
        return "somevalue"
    if stmt.value_type == "novalue":
        return "novalue"

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


def _format_qualifier(qual: dict[str, object]) -> str:
    """Format a single qualifier snak for QuickStatements.

    Qualifiers use the P-prefix (unlike references which use S-prefix).

    Args:
        qual: Dict with 'property', 'value', 'type' keys.

    Returns:
        Tab-separated qualifier components.
    """
    pid = str(qual.get("property", ""))
    value = qual.get("value", "")
    vtype = qual.get("type", "string")
    if vtype == "item":
        return f"{pid}\t{value}"
    if vtype == "time":
        prec = qual.get("precision", 11)
        return f"{pid}\t{value}/{prec}"
    return f'{pid}\t"{_escape_qs(str(value))}"'


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
        # Bug fix 2026-04-19: notability-filtered persons have no labels or
        # statements. Emitting a lone CREATE line for them is invalid QS syntax.
        if not item.existing_qid and not item.labels and not item.statements:
            return ""

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
            # Rule 42: QS v2 has no native rank syntax. Emit a leading
            # comment line documenting the intended rank; rank is actually
            # persisted by the WBI uploader. Reviewers reading the QS file
            # can still see the intended rank.
            if stmt.rank != "normal":
                lines.append(
                    f"/* RANK: {stmt.rank} "
                    f"(set via WBI; not expressible in QS v2) */"
                )

            value_str = _format_value(stmt)
            line_parts = [qid, stmt.property_id, value_str]

            # Qualifiers must appear before references per QS v2 format
            for qual in stmt.qualifiers or []:
                line_parts.append(_format_qualifier(qual))

            # References
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

        # Separate by type and ORDER OF DEPENDENCY:
        #   persons first  — work items reference them via P50 (author).
        #   works second   — manuscript items reference them via P1574 (exemplar of).
        #   manuscripts last.
        #
        # Bug fix 2026-05-18: works were previously dropped entirely from
        # QS export (only `persons` + `manuscripts` were iterated). Work
        # items now appear as their own CREATE blocks so the en label
        # "Takanut rivno gereshem meor hagola (NLI 990001801390205171)"
        # composed in `_get_or_create_work` reaches Wikidata.
        persons = [i for i in items if i.entity_type == "person" and not i.existing_qid]
        works = [i for i in items if i.entity_type == "work" and not i.existing_qid]
        manuscripts = [i for i in items if i.entity_type == "manuscript"]

        blocks.append(
            "/* MHM Pipeline — Wikidata QuickStatements Export */\n"
            "/* Persons (create first; works and manuscripts reference them) */\n"
        )

        for person in persons:
            if block := self.export_item(person):
                blocks.append(block)
                blocks.append("")  # Blank line between items

        blocks.append(
            "\n/* Works (create second; manuscripts P1574 them) */\n"
        )

        for work in works:
            if block := self.export_item(work):
                blocks.append(block)
                blocks.append("")

        blocks.append("\n/* Manuscripts */\n")

        for ms in manuscripts:
            blocks.append(self.export_item(ms))
            blocks.append("")

        return "\n".join(blocks)

    def export_to_file(
        self,
        items: list[WikidataItem],
        output_path: Path,
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
            len(items),
            output_path,
        )
        return output_path
