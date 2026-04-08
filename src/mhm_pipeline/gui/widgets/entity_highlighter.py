"""Widget for displaying text with highlighted Named Entity Recognition (NER) spans.

This widget visualizes entities extracted from text, showing both the highlighted
original text and a list of extracted entities with their roles.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# =============================================================================
# Pure Functions for Entity Highlighting
# =============================================================================


def get_entity_colors(
    entity: Entity,
    role_colors: dict[str, tuple[str, str]],
    entity_colors: dict[str, tuple[str, str]],
) -> tuple[str, str]:
    """Get background and text colors for an entity based on role or type.

    Args:
        entity: The entity to get colors for
        role_colors: Mapping of role names to (bg_color, text_color) tuples
        entity_colors: Mapping of entity types to (bg_color, text_color) tuples

    Returns:
        Tuple of (background_color, text_color)
    """
    if entity.role and entity.role in role_colors:
        return role_colors[entity.role]
    return entity_colors.get(entity.type, ("#e5e7eb", "#374151"))


def build_highlighted_span(text: str, bg_color: str, text_color: str) -> str:
    """Build HTML span with highlighting styles.

    Args:
        text: The text content (should already be escaped)
        bg_color: Background color for the highlight
        text_color: Text color for the highlight

    Returns:
        HTML span element with inline styles
    """
    return (
        f'<span style="background-color: {bg_color}; '
        f'color: {text_color}; padding: 2px 4px; '
        f'border-radius: 3px; font-weight: 500;">'
        f"{text}</span>"
    )


def sort_entities_by_position(entities: list[Entity]) -> list[Entity]:
    """Sort entities by their start position in ascending order.

    Args:
        entities: List of entities to sort

    Returns:
        New list sorted by entity.start
    """
    return sorted(entities, key=lambda e: e.start)


def calculate_text_segments(
    text: str, entities: list[Entity]
) -> list[tuple[str, Entity | None]]:
    """Break text into segments, pairing each with its entity (or None for plain text).

    Args:
        text: Original text content
        entities: Sorted list of entities (must be sorted by position)

    Returns:
        List of (segment_text, entity) tuples where entity is None for plain text
    """
    segments: list[tuple[str, Entity | None]] = []
    last_end = 0

    for entity in entities:
        if entity.start > last_end:
            segments.append((text[last_end : entity.start], None))
        segments.append((text[entity.start : entity.end], entity))
        last_end = entity.end

    if last_end < len(text):
        segments.append((text[last_end:], None))

    return segments


def build_highlighted_html(
    text: str,
    entities: list[Entity],
    role_colors: dict[str, tuple[str, str]],
    entity_colors: dict[str, tuple[str, str]],
) -> str:
    """Build HTML string with highlighted entity spans.

    Args:
        text: Original text
        entities: List of entities to highlight
        role_colors: Mapping of role names to color tuples
        entity_colors: Mapping of entity types to color tuples

    Returns:
        HTML string with styled span elements around entities
    """
    if not entities:
        return escape(text)

    sorted_entities = sort_entities_by_position(entities)
    segments = calculate_text_segments(text, sorted_entities)

    html_parts: list[str] = []
    for segment_text, entity in segments:
        if entity is None:
            html_parts.append(escape(segment_text))
        else:
            bg_color, text_color = get_entity_colors(
                entity, role_colors, entity_colors
            )
            escaped_entity_text = escape(segment_text)
            html_parts.append(
                build_highlighted_span(escaped_entity_text, bg_color, text_color)
            )

    return "".join(html_parts)


def wrap_in_div(html_content: str) -> str:
    """Wrap HTML content in a styled div container.

    Args:
        html_content: The inner HTML content

    Returns:
        HTML wrapped in a styled div
    """
    return (
        '<div style="line-height: 1.6; font-family: '
        "'Segoe UI', Arial, sans-serif; font-size: 13px; color: #1f2937;\">\n"
        f"{html_content}\n"
        "</div>"
    )


def get_entity_icon(entity: Entity, role_icons: dict[str, str], default_icon: str) -> str:
    """Get the appropriate icon for an entity based on its role.

    Args:
        entity: The entity to get an icon for
        role_icons: Mapping of role names to icon strings
        default_icon: Default icon to use if no role matches

    Returns:
        Icon string for the entity
    """
    if entity.role:
        return role_icons.get(entity.role, default_icon)
    return default_icon


def build_entity_display_text(entity: Entity) -> str:
    """Build the display text for an entity list item.

    Args:
        entity: The entity to build display text for

    Returns:
        Formatted display string with icon, text, type, and optional role
    """
    return (
        f"{entity.text}  →  {entity.type}"
        + (f" ({entity.role})" if entity.role else "")
    )


def build_entity_tooltip(entity: Entity) -> str:
    """Build the tooltip text for an entity list item.

    Args:
        entity: The entity to build a tooltip for

    Returns:
        Multi-line tooltip string with entity details
    """
    parts: list[str] = []
    if entity.role:
        parts.append(f"Role: {entity.role}")
    parts.extend(
        [
            f"Type: {entity.type}",
            f"Position: {entity.start}-{entity.end}",
        ]
    )
    if entity.confidence is not None:
        parts.append(f"Confidence: {entity.confidence:.2%}")

    return "\n".join(parts)


def filter_entities_by_roles(
    entities: list[Entity], selected_roles: set[str]
) -> list[Entity]:
    """Filter entities to only include those matching selected roles.

    Args:
        entities: List of entities to filter
        selected_roles: Set of role names to include. Empty set means include all.

    Returns:
        Filtered list of entities
    """
    if not selected_roles:
        return list(entities)

    return [
        entity
        for entity in entities
        if not entity.role or entity.role in selected_roles
    ]


def create_entity_from_record(ent: dict) -> Entity | None:
    """Create an Entity from a raw dictionary extracted from a record.

    Supports both person NER format (``person`` key) and provenance/contents
    NER format (``text`` + ``type`` keys).

    Args:
        ent: Dictionary containing entity data.

    Returns:
        Entity object or None if input is not a dict.
    """
    if not isinstance(ent, dict):
        return None

    text = ent.get("person", ent.get("text", ""))
    entity_type = ent.get("type", "PERSON")

    return Entity(
        text=text,
        type=entity_type,
        start=ent.get("start", 0),
        end=ent.get("end", 0),
        role=ent.get("role"),
        confidence=ent.get("confidence"),
    )


def build_record_header_html(control_number: str) -> str:
    """Build HTML for a record header showing control number.

    Args:
        control_number: The control number to display

    Returns:
        HTML div element for the record header
    """
    return (
        f'<div style="background-color: #6b7280; color: white; '
        f'padding: 4px 8px; margin-top: 8px; margin-bottom: 4px; '
        f'font-weight: bold; font-size: 12px; border-radius: 3px;">'
        f'Control Number: {escape(str(control_number))}'
        f'</div>\n'
    )


def build_record_content_html(html_content: str) -> str:
    """Build HTML wrapper for record content with highlighting.

    Args:
        html_content: Pre-highlighted HTML content

    Returns:
        HTML div element wrapping the content
    """
    return (
        f'<div style="margin-bottom: 16px; padding: 8px; '
        f'background-color: #f9fafb; color: #1f2937; border-radius: 4px;">'
        f'{html_content}</div>\n'
    )


def build_no_text_message(entity_count: int) -> str:
    """Build HTML message for records without text but with entities.

    Args:
        entity_count: Number of entities extracted

    Returns:
        HTML div element with the message
    """
    return (
        f'<div style="margin-bottom: 16px; padding: 8px; '
        f'background-color: #f9fafb; border-radius: 4px; color: #4b5563;">'
        f'[No text available - {entity_count} entities extracted]'
        f'</div>\n'
    )


def build_no_entities_message() -> str:
    """Build HTML message for records with no entities found.

    Returns:
        HTML div element with the message
    """
    return (
        f'<div style="margin-bottom: 16px; padding: 8px; '
        f'background-color: #f9fafb; border-radius: 4px; color: #6b7280;">'
        f'[No entities found]'
        f'</div>\n'
    )


def process_record_entities(
    record: dict, all_entities: list[Entity]
) -> tuple[list[Entity], list[Entity]]:
    """Process entities from a record and update the aggregate list.

    Args:
        record: Dictionary containing entity data
        all_entities: Aggregate list to append to

    Returns:
        Tuple of (record_entities, updated_all_entities)
    """
    entities_data = record.get("entities", [])
    record_entities: list[Entity] = []

    for ent in entities_data:
        entity = create_entity_from_record(ent)
        if entity:
            record_entities.append(entity)
            all_entities.append(entity)

    return record_entities, all_entities


@dataclass
class Entity:
    """Represents a named entity extracted from text.

    Attributes:
        text: The entity text content
        type: The entity type (PERSON, DATE, PLACE, WORK, ORG)
        start: Start position in the original text
        end: End position in the original text
        role: Optional role classification (e.g., AUTHOR, SCRIBE, OWNER)
        confidence: Optional confidence score from the NER model
    """

    text: str
    type: str
    start: int
    end: int
    role: str | None = None
    confidence: float | None = None


class EntityHighlighter(QWidget):
    """Widget for displaying text with highlighted entity spans.

    Displays the original text with colored highlights around identified entities,
    along with a list of extracted entities showing their types and roles.

    Example:
        >>> highlighter = EntityHighlighter()
        >>> entities = [
        ...     Entity("Shlomo ben David", "PERSON", 22, 40, role="SCRIBE")
        ... ]
        >>> highlighter.load_entities("Written by scribe Shlomo ben David", entities)
    """

    # Color mapping for entity types: (background_color, text_color)
    ENTITY_COLORS: dict[str, tuple[str, str]] = {
        "PERSON": ("#c7d2fe", "#3730a3"),  # Light purple, dark text
        "DATE": ("#fed7aa", "#9a3412"),  # Light orange
        "PLACE": ("#bbf7d0", "#166534"),  # Light green
        "WORK": ("#fecaca", "#991b1b"),  # Light red
        "ORG": ("#e5e7eb", "#374151"),  # Light gray
        "OWNER": ("#bbf7d0", "#166534"),  # Green (ownership)
        "COLLECTION": ("#dbeafe", "#1e40af"),  # Blue (institutional)
        "FOLIO": ("#fef3c7", "#92400e"),  # Yellow (reference)
        "WORK_AUTHOR": ("#e9d5ff", "#6b21a8"),  # Purple (authorship)
    }

    # Color mapping for entity roles: (background_color, text_color)
    ROLE_COLORS: dict[str, tuple[str, str]] = {
        "AUTHOR": ("#c7d2fe", "#3730a3"),  # Purple
        "SCRIBE": ("#fed7aa", "#9a3412"),  # Orange
        "TRANSCRIBER": ("#fed7aa", "#9a3412"),  # Orange
        "OWNER": ("#bbf7d0", "#166534"),  # Green
        "CENSOR": ("#fecaca", "#991b1b"),  # Red
        "TRANSLATOR": ("#e5e7eb", "#374151"),  # Gray
        "COMMENTATOR": ("#dbeafe", "#1e40af"),  # Blue
    }

    # Icon mapping for entity roles
    ROLE_ICONS: dict[str, str] = {
        "AUTHOR": "✍️",
        "SCRIBE": "🖊️",
        "TRANSCRIBER": "🖊️",
        "OWNER": "👤",
        "CENSOR": "✂️",
        "TRANSLATOR": "🌐",
        "COMMENTATOR": "💬",
    }

    # Default icon for entities without a role
    DEFAULT_ICON: str = "🏷️"

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the EntityHighlighter widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the widget UI components and layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # Header label showing entity count
        self._header_label = QLabel("Entities Found (0 entities)")
        self._header_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self._header_label)

        # Original text section
        text_section = QVBoxLayout()
        text_section.setSpacing(4)

        text_label = QLabel("Original Text:")
        text_label.setStyleSheet("font-weight: bold;")
        text_section.addWidget(text_label)

        # QTextEdit for displaying highlighted text
        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setMinimumHeight(100)
        self._text_edit.setPlaceholderText("No text loaded...")
        text_section.addWidget(self._text_edit)

        layout.addLayout(text_section)

        # Legend showing color meanings
        legend_layout = self._build_legend()
        layout.addLayout(legend_layout)

        # Entity list section
        entity_section = QVBoxLayout()
        entity_section.setSpacing(4)

        entity_label = QLabel("Extracted Entities:")
        entity_label.setStyleSheet("font-weight: bold;")
        entity_section.addWidget(entity_label)

        # QListWidget for entity list
        self._entity_list = QListWidget()
        self._entity_list.setMinimumHeight(120)
        self._entity_list.setAlternatingRowColors(True)
        entity_section.addWidget(self._entity_list)

        layout.addLayout(entity_section)

    def _build_legend(self) -> QHBoxLayout:
        """Build the color legend widget showing entity type colors.

        Returns:
            Horizontal layout with color legend labels
        """
        legend_layout = QHBoxLayout()
        legend_layout.setSpacing(16)
        legend_layout.addStretch()

        legend_label = QLabel("Legend:")
        legend_label.setStyleSheet("font-weight: bold;")
        legend_layout.addWidget(legend_label)

        # Add legend items for each entity type
        for entity_type, (bg_color, text_color) in self.ENTITY_COLORS.items():
            legend_item = QLabel(
                f'<span style="background-color: {bg_color}; '
                f"color: {text_color}; padding: 2px 6px; "
                f'border-radius: 3px; font-size: 11px;">'
                f"{entity_type}</span>"
            )
            legend_layout.addWidget(legend_item)

        legend_layout.addStretch()
        return legend_layout

    def load_entities(self, text: str, entities: list[Entity]) -> None:
        """Load text and highlight entity spans.

        Builds HTML with highlighted spans for each entity and populates
        the entity list with icons and role information.

        Args:
            text: Original note text
            entities: List of Entity objects with start/end positions
        """
        self._current_text = text
        self._current_entities = entities

        # Build HTML with highlighted spans
        html_content = self._build_highlighted_html(text, entities)
        self._text_edit.setHtml(html_content)

        # Populate entity list
        self._populate_entity_list(entities)

        # Update header with entity count
        self._header_label.setText(f"Entities Found ({len(entities)} entities)")

    def _build_highlighted_html(self, text: str, entities: list[Entity]) -> str:
        """Build HTML string with highlighted entity spans.

        Args:
            text: Original text
            entities: List of entities to highlight

        Returns:
            HTML string with styled span elements around entities
        """
        html_content = build_highlighted_html(
            text, entities, self.ROLE_COLORS, self.ENTITY_COLORS
        )
        return wrap_in_div(html_content)

    def _build_highlighted_html_inner(self, text: str, entities: list[Entity]) -> str:
        """Build HTML string with highlighted entity spans (no wrapper div)."""
        return build_highlighted_html(
            text, entities, self.ROLE_COLORS, self.ENTITY_COLORS
        )

    def _populate_entity_list(self, entities: list[Entity]) -> None:
        """Populate the entity list widget with formatted items.

        Args:
            entities: List of entities to display
        """
        self._entity_list.clear()

        for entity in entities:
            icon = get_entity_icon(entity, self.ROLE_ICONS, self.DEFAULT_ICON)
            display_text = f"{icon} {build_entity_display_text(entity)}"
            tooltip = build_entity_tooltip(entity)
            bg_color, _ = get_entity_colors(
                entity, self.ROLE_COLORS, self.ENTITY_COLORS
            )

            item = QListWidgetItem(display_text)
            item.setToolTip(tooltip)
            item.setBackground(QColor(bg_color))

            self._entity_list.addItem(item)

    def clear(self) -> None:
        """Clear the widget, removing all text and entities."""
        self._text_edit.clear()
        self._entity_list.clear()
        self._header_label.setText("Entities Found (0 entities)")
        self._current_text = ""
        self._current_entities = []

    def get_entities(self) -> list[Entity]:
        """Get the currently loaded entities.

        Returns:
            List of Entity objects currently displayed
        """
        return getattr(self, "_current_entities", [])

    def get_text(self) -> str:
        """Get the currently loaded text.

        Returns:
            The original text currently displayed
        """
        return getattr(self, "_current_text", "")

    def get_all_roles(self) -> list[str]:
        """Get all unique roles from current entities.

        Returns:
            Sorted list of unique role names
        """
        roles = set()
        for entity in getattr(self, "_current_entities", []):
            if entity.role:
                roles.add(entity.role)
        return sorted(roles)

    def filter_by_roles(self, selected_roles: set[str]) -> None:
        """Filter the entity list and text highlighting by roles.

        Args:
            selected_roles: Set of role names to display. Empty set means show all.
        """
        if not hasattr(self, "_current_entities"):
            return

        self._role_filter = selected_roles

        # Re-populate entity list with filtered entities
        self._entity_list.clear()

        filtered_entities = filter_entities_by_roles(
            self._current_entities, selected_roles
        )
        self._populate_entity_list(filtered_entities)

        # Update header with filter info
        if selected_roles:
            self._header_label.setText(
                f"Entities Found ({len(filtered_entities)} of {len(self._current_entities)} entities)"
            )
        else:
            self._header_label.setText(
                f"Entities Found ({len(self._current_entities)} entities)"
            )

        # Rebuild text highlighting with filtered entities dimmed
        if hasattr(self, "_current_text") and self._current_text:
            self._rebuild_text_highlighting(dim_roles=selected_roles)

    def _rebuild_text_highlighting(self, dim_roles: set[str] | None = None) -> None:
        """Rebuild the text HTML with optional role filtering.

        Args:
            dim_roles: Set of roles to dim/hide. If None or empty, show all normally.
        """
        if not self._current_text or not self._current_entities:
            return

        sorted_entities = sort_entities_by_position(self._current_entities)
        segments = calculate_text_segments(self._current_text, sorted_entities)

        html_parts: list[str] = []
        for segment_text, entity in segments:
            if entity is None:
                html_parts.append(escape(segment_text))
                continue

            is_dimmed = dim_roles and entity.role and entity.role not in dim_roles
            escaped_text = escape(segment_text)

            if is_dimmed:
                html_parts.append(escaped_text)
            else:
                bg_color, text_color = get_entity_colors(
                    entity, self.ROLE_COLORS, self.ENTITY_COLORS
                )
                html_parts.append(
                    build_highlighted_span(escaped_text, bg_color, text_color)
                )

        self._text_edit.setHtml(wrap_in_div("".join(html_parts)))

    def display_records(self, records: list[dict]) -> None:
        """Display multiple records, each with its control number and highlighted text.

        Creates a scrollable view with each record displayed in its own section
        with a header showing the control number.

        Args:
            records: List of record dicts with "_control_number", "text", and "entities" keys
        """
        self._text_edit.clear()
        self._entity_list.clear()

        all_entities: list[Entity] = []
        html_parts: list[str] = [
            '<div style="line-height: 1.6; font-family: '
            "'Segoe UI', Arial, sans-serif; font-size: 13px; color: #1f2937;\">\n"
        ]

        for record in records:
            if not isinstance(record, dict):
                continue

            control_number = record.get("_control_number", "Unknown")
            text = record.get("text", "")
            entities = record.get("entities", [])

            html_parts.append(build_record_header_html(control_number))

            if entities:
                record_entities, all_entities = process_record_entities(
                    record, all_entities
                )

                if text:
                    html_content = self._build_highlighted_html_inner(text, record_entities)
                    html_parts.append(build_record_content_html(html_content))
                else:
                    html_parts.append(build_no_text_message(len(entities)))
            else:
                html_parts.append(build_no_entities_message())

        html_parts.append("</div>")

        self._text_edit.setHtml("".join(html_parts))
        self._populate_entity_list(all_entities)
        self._header_label.setText(
            f"Entities Found ({len(records)} records, {len(all_entities)} entities)"
        )

        self._current_entities = all_entities
