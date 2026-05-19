"""Offline Wikibase draft models for full HMO RDF exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

StatementValue = str | int | float | bool
StatementValueType = Literal["entity", "literal", "uri", "blank"]


@dataclass(frozen=True)
class WikibaseStatementDraft:
    """A local Wikibase statement draft produced from one RDF triple."""

    property_name: str
    property_uri: str
    value: StatementValue
    value_type: StatementValueType
    value_entity_id: str | None = None
    datatype: str | None = None
    language: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation of this statement."""
        data: dict[str, object] = {
            "property_name": self.property_name,
            "property_uri": self.property_uri,
            "value": self.value,
            "value_type": self.value_type,
        }
        if self.value_entity_id is not None:
            data["value_entity_id"] = self.value_entity_id
        if self.datatype is not None:
            data["datatype"] = self.datatype
        if self.language is not None:
            data["language"] = self.language
        return data


@dataclass(frozen=True)
class WikibaseEntityDraft:
    """A local Wikibase entity draft for a typed HMO RDF node."""

    local_id: str
    labels: dict[str, str]
    descriptions: dict[str, str]
    entity_type: str
    class_uri: str
    source_uri: str
    statements: list[WikibaseStatementDraft] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation of this entity draft."""
        return {
            "local_id": self.local_id,
            "labels": self.labels,
            "descriptions": self.descriptions,
            "entity_type": self.entity_type,
            "class_uri": self.class_uri,
            "source_uri": self.source_uri,
            "statements": [statement.to_dict() for statement in self.statements],
        }
