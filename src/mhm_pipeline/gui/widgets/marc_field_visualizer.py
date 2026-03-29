"""MARC field visualization widget for Stage 1.

Displays extracted MARC fields in a tree view with color-coded field types,
making it immediately clear which bibliographic elements were successfully parsed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from mhm_pipeline.gui.widgets.base_visualization_widget import (
    BaseVisualizationWidget,
)

if TYPE_CHECKING:
    from converter.transformer.field_handlers import ExtractedData


class MarcFieldVisualizer(BaseVisualizationWidget):
    """Tree widget showing extracted MARC fields with color coding.

    Color coding by field type:
    - 001-099: Gray (Control fields)
    - 100-199: Blue (Names - authors, contributors)
    - 245-246: Cyan (Titles)
    - 260-264: Orange (Publication/Production)
    - 300-399: Green (Physical description)
    - 500-599: Yellow (Notes - NER targets)
    - 600-699: Purple (Subjects)
    - 700-899: Pink (Added entries)
    - 957: Red (Local notes - colophon)
    """

    # Color mapping for field ranges (background, text)
    FIELD_COLORS: dict[str, tuple[str, str]] = {
        "001": ("#f3f4f6", "#374151"),  # Gray
        "100": ("#dbeafe", "#1e40af"),  # Blue
        "245": ("#a5f3fc", "#155e75"),  # Cyan
        "260": ("#ffedd5", "#9a3412"),  # Orange
        "300": ("#bbf7d0", "#166534"),  # Green
        "500": ("#fef3c7", "#92400e"),  # Yellow
        "600": ("#e5dbff", "#5b21b6"),  # Purple
        "700": ("#fce7f3", "#be185d"),  # Pink
        "957": ("#fee2e2", "#991b1b"),  # Red
    }

    # Field labels for common fields
    FIELD_LABELS: dict[str, str] = {
        "001": "Control Number",
        "100": "Main Entry - Personal Name",
        "245": "Title Statement",
        "246": "Varying Form of Title",
        "260": "Publication Distribution",
        "264": "Production Statement",
        "300": "Physical Description",
        "500": "General Note",
        "561": "Ownership History",
        "600": "Subject Added Entry",
        "700": "Added Entry - Personal Name",
        "710": "Added Entry - Corporate Name",
        "957": "Local Note",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the MARC field visualizer.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._setup_ui()

    def _get_placeholder_text(self) -> str:
        """Return placeholder text for when no data is loaded."""
        return "No MARC data loaded. Run Stage 1 to see extracted fields."

    def _setup_ui(self) -> None:
        """Set up the tree widget UI."""
        # Remove placeholder from layout - we'll manage visibility
        self._layout.removeWidget(self._placeholder_label)

        # Create tree widget
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Field", "Tag", "Content"])
        self._tree.setColumnWidth(0, 150)
        self._tree.setColumnWidth(1, 60)
        self._tree.setColumnWidth(2, 400)
        self._tree.setAlternatingRowColors(True)

        # Add widgets to layout
        self._layout.addWidget(self._placeholder_label)
        self._layout.addWidget(self._tree)

        # Initially show placeholder, hide tree
        self._tree.setVisible(False)
        self._placeholder_label.setVisible(True)

    def _get_field_color(self, tag: str) -> tuple[str, str]:
        """Get background and text color for a field tag.

        Args:
            tag: The MARC field tag (e.g., "245", "500").

        Returns:
            Tuple of (background_color, text_color) hex strings.
        """
        # Get the base tag for color lookup (first 1-3 digits)
        if tag.startswith("0"):
            base_tag = "001"
        elif tag.startswith("1"):
            base_tag = "100"
        elif tag.startswith("2"):
            if tag in ("245", "246"):
                base_tag = tag
            else:
                base_tag = "245"
        elif tag.startswith("3"):
            base_tag = "300"
        elif tag.startswith("5"):
            base_tag = "500"
        elif tag.startswith("6"):
            base_tag = "600"
        elif tag.startswith(("7", "8")):
            base_tag = "700"
        elif tag == "957":
            base_tag = "957"
        else:
            base_tag = "001"  # Default to gray

        return self.FIELD_COLORS.get(base_tag, ("#f3f4f6", "#374151"))

    def _get_field_label(self, tag: str) -> str:
        """Get the label for a field tag.

        Args:
            tag: The MARC field tag.

        Returns:
            Human-readable label for the field.
        """
        return self.FIELD_LABELS.get(tag, f"Field {tag}")

    def _add_field_node(
        self,
        tag: str,
        content: str,
        subfield: str | None = None,
    ) -> QTreeWidgetItem:
        """Add a field node to the tree.

        Args:
            tag: The MARC field tag.
            content: The field content to display.
            subfield: Optional subfield code.

        Returns:
            The created tree widget item.
        """
        label = self._get_field_label(tag)
        if subfield:
            label = f"  ${subfield}"

        # Truncate content if too long
        display_content = content if len(content) <= 100 else content[:97] + "..."

        item = QTreeWidgetItem([label, tag, display_content])

        # Apply color coding
        bg_color, text_color = self._get_field_color(tag)
        item.setBackground(0, QColor(bg_color))
        item.setForeground(0, QColor(text_color))
        item.setForeground(1, QColor(text_color))
        item.setForeground(2, QColor(text_color))

        # Make tag column bold
        font = item.font(1)
        font.setBold(True)
        item.setFont(1, font)

        self._tree.addTopLevelItem(item)
        return item

    def _add_child_node(
        self,
        parent: QTreeWidgetItem,
        subfield: str,
        content: str,
    ) -> QTreeWidgetItem:
        """Add a child node for subfield content.

        Args:
            parent: The parent tree item.
            subfield: The subfield code.
            content: The subfield content.

        Returns:
            The created child tree widget item.
        """
        # Truncate content if too long
        display_content = content if len(content) <= 100 else content[:97] + "..."

        child = QTreeWidgetItem([f"  ${subfield}", "", display_content])
        parent.addChild(child)
        return child

    def load_from_extracted_data(self, data: ExtractedData) -> None:
        """Populate tree from parsed MARC data.

        Args:
            data: The ExtractedData container with parsed MARC fields.
        """
        self.clear_data()

        # Control number (001)
        if hasattr(data, "external_ids") and data.external_ids:
            control_no = data.external_ids.get("mms_id") or data.external_ids.get("control_number")
            if control_no:
                self._add_field_node("001", control_no)

        # Title (245)
        if hasattr(data, "title") and data.title:
            title_content = data.title
            if hasattr(data, "subtitle") and data.subtitle:
                title_content += f" | {data.subtitle}"
            self._add_field_node("245", title_content)

        # Authors (100)
        if hasattr(data, "authors") and data.authors:
            for author in data.authors:
                if isinstance(author, dict):
                    name = author.get("name", "")
                    if name:
                        self._add_field_node("100", name)
                elif isinstance(author, str):
                    self._add_field_node("100", author)

        # Place (260)
        if hasattr(data, "place") and data.place:
            self._add_field_node("260", data.place)

        # Physical description (300)
        physical_desc_parts: list[str] = []
        if hasattr(data, "extent") and data.extent:
            physical_desc_parts.append(f"{data.extent} leaves")
        if hasattr(data, "height_mm") and data.height_mm:
            physical_desc_parts.append(f"{data.height_mm} mm height")
        if hasattr(data, "width_mm") and data.width_mm:
            physical_desc_parts.append(f"{data.width_mm} mm width")
        if hasattr(data, "materials") and data.materials:
            physical_desc_parts.append(f"Materials: {', '.join(data.materials)}")
        if physical_desc_parts:
            self._add_field_node("300", " | ".join(physical_desc_parts))

        # Languages
        if hasattr(data, "languages") and data.languages:
            self._add_field_node("300", f"Languages: {', '.join(data.languages)}")

        # Script
        if hasattr(data, "script_type") and data.script_type:
            script_info = data.script_type
            if hasattr(data, "script_mode") and data.script_mode:
                script_info += f" ({data.script_mode})"
            self._add_field_node("300", f"Script: {script_info}")

        # Notes (500 series) - these are NER targets
        if hasattr(data, "notes") and data.notes:
            for note in data.notes:
                self._add_field_node("500", note)

        # Colophon text (957)
        if hasattr(data, "colophon_text") and data.colophon_text:
            self._add_field_node("957", data.colophon_text)

        # Binding info
        if hasattr(data, "binding_info") and data.binding_info:
            self._add_field_node("500", f"Binding: {data.binding_info}")

        # Provenance (561)
        if hasattr(data, "provenance") and data.provenance:
            self._add_field_node("561", data.provenance)

        # Subjects (600)
        if hasattr(data, "subjects") and data.subjects:
            for subject in data.subjects:
                if isinstance(subject, dict):
                    subject_name = subject.get("term", "") or subject.get("name", "")
                    if subject_name:
                        self._add_field_node("600", subject_name)
                elif isinstance(subject, str):
                    self._add_field_node("600", subject)

        # Contributors / Added entries (700)
        if hasattr(data, "contributors") and data.contributors:
            for contributor in data.contributors:
                if isinstance(contributor, dict):
                    name = contributor.get("name", "")
                    if name:
                        self._add_field_node("700", name)
                elif isinstance(contributor, str):
                    self._add_field_node("700", contributor)

        # Genres
        if hasattr(data, "genres") and data.genres:
            for genre in data.genres:
                self._add_field_node("500", f"Genre: {genre}")

        # Digital URL
        if hasattr(data, "digital_url") and data.digital_url:
            self._add_field_node("500", f"Digital version: {data.digital_url}")

        # Show tree, hide placeholder
        self._tree.setVisible(True)
        self._placeholder_label.setVisible(False)
        self._has_data = True

        # Expand all nodes
        self._tree.expandAll()

    def clear_data(self) -> None:
        """Clear all displayed data and reset to initial state."""
        self._tree.clear()
        self._tree.setVisible(False)
        self._placeholder_label.setVisible(True)
        self._has_data = False
