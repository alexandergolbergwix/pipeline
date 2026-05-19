"""Offline QuickStatements-like TSV exporter for project Wikibase drafts."""

from __future__ import annotations

from pathlib import Path

from converter.wikibase.models import WikibaseEntityDraft, WikibaseStatementDraft


class LocalQuickStatementsExporter:
    """Render draft entities as simple TSV commands for future Wikibase import."""

    def export(self, entities: list[WikibaseEntityDraft]) -> str:
        """Return a local QuickStatements-like TSV representation."""
        blocks = [
            "# MHM Pipeline project-Wikibase draft export",
            "# Offline only: local IDs are placeholders, not Wikidata QIDs.",
        ]
        for entity in entities:
            blocks.extend(self.export_entity(entity))
        return "\n".join(blocks) + "\n"

    def export_entity(self, entity: WikibaseEntityDraft) -> list[str]:
        """Return TSV command lines for one draft entity."""
        lines = [f"CREATE\t{entity.local_id}"]
        for language, label in sorted(entity.labels.items()):
            lines.append(f'{entity.local_id}\tL{language}\t"{_escape(label)}"')
        for language, description in sorted(entity.descriptions.items()):
            lines.append(f'{entity.local_id}\tD{language}\t"{_escape(description)}"')

        lines.append(f"{entity.local_id}\tP31\t<{entity.class_uri}>")
        for statement in entity.statements:
            lines.append(_statement_line(entity.local_id, statement))
        return lines

    def export_to_file(
        self,
        entities: list[WikibaseEntityDraft],
        output_path: Path,
    ) -> Path:
        """Write local QuickStatements-like TSV text to a file."""
        output_path.write_text(self.export(entities), encoding="utf-8")
        return output_path


def _statement_line(local_id: str, statement: WikibaseStatementDraft) -> str:
    """Format one statement draft as a TSV row."""
    return "\t".join(
        [
            local_id,
            statement.property_name,
            _format_value(statement),
            f"<{statement.property_uri}>",
        ]
    )


def _format_value(statement: WikibaseStatementDraft) -> str:
    """Format a draft value for a local TSV import file."""
    if statement.value_type == "entity":
        return statement.value_entity_id or str(statement.value)
    if statement.value_type == "uri":
        return f"<{statement.value}>"
    suffix = ""
    if statement.language is not None:
        suffix = f"@{statement.language}"
    elif statement.datatype is not None:
        suffix = f"^^<{statement.datatype}>"
    return f'"{_escape(str(statement.value))}"{suffix}'


def _escape(value: str) -> str:
    """Escape text for TSV string cells."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\t", " ")
