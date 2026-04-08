"""Authority matching visualization widget for displaying entity-to-authority matches.

This module provides a QTableWidget-based view for showing side-by-side comparisons
of extracted entity names against matched authority records, with confidence scores
and source attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass
class AuthorityMatch:
    """Data class representing an authority match result.

    Attributes:
        source: The authority source (mazal, viaf, kima)
        id: The authority identifier (e.g., "M12345", "281184")
        preferred_name: The preferred/canonical name from the authority
        confidence: Match confidence score between 0.0 and 1.0
        found: Whether a match was found in the authority database

    """

    source: str
    id: str
    preferred_name: str
    confidence: float
    found: bool


class AuthorityMatcherView(QWidget):
    """Widget showing entity-to-authority matching results.

    Displays a table with extracted entity names alongside their matched
    authority records, including source attribution and confidence scores.
    Supports multiple authority sources: Mazal (NLI), VIAF, and KIMA.

    Attributes:
        SOURCE_ICONS: Mapping of source names to their visual icons

    """

    SOURCE_ICONS: ClassVar[dict[str, str]] = {
        "mazal": "🏛️",
        "viaf": "🌐",
        "kima": "📚",
    }

    # Confidence thresholds for color coding
    _HIGH_CONFIDENCE = 0.9
    _MEDIUM_CONFIDENCE = 0.7

    # Color definitions
    _COLOR_MATCH_FOUND = QColor("#059669")  # Green for found matches
    _COLOR_NO_MATCH = QColor("#d97706")  # Orange for no match
    _COLOR_HIGH_CONFIDENCE = QColor("#dcfce7")  # Light green
    _COLOR_MEDIUM_CONFIDENCE = QColor("#fef3c7")  # Light yellow
    _COLOR_LOW_CONFIDENCE = QColor("#fee2e2")  # Light red

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the authority matcher view.

        Args:
            parent: Optional parent widget

        """
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the UI components and layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create the table widget
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            [
                "Extracted Name",
                "",
                "Authority Match",
                "Source",
                "Confidence",
            ]
        )

        # Configure column widths - wider for better visibility
        self._table.setColumnWidth(0, 180)  # Extracted Name
        self._table.setColumnWidth(1, 30)  # Arrow
        self._table.setColumnWidth(2, 250)  # Authority Match
        self._table.setColumnWidth(3, 100)  # Source
        self._table.setColumnWidth(4, 90)  # Confidence

        # Configure table behavior - interactive resize
        header = self._table.horizontalHeader()
        assert header is not None, "horizontalHeader() should not return None"
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)

        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(100)

        layout.addWidget(self._table)

    def add_match(self, extracted: str, authority: AuthorityMatch) -> None:
        """Add a match result row to the table.

        Args:
            extracted: The extracted entity name from the source text
            authority: The authority match result containing source, ID,
                      preferred name, confidence, and found status

        Example:
            >>> match = AuthorityMatch(
            ...     source="mazal",
            ...     id="M12345",
            ...     preferred_name="שְׁלֹמֹה בֶּן דָּוִד",
            ...     confidence=0.95,
            ...     found=True
            ... )
            >>> view.add_match("Shlomo ben David", match)

        """
        row = self._table.rowCount()
        self._table.insertRow(row)

        # Column 0: Extracted name
        extracted_item = QTableWidgetItem(extracted)
        extracted_item.setToolTip(extracted)
        self._table.setItem(row, 0, extracted_item)

        # Column 1: Arrow indicator
        arrow = QTableWidgetItem("→")
        arrow.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setFlags(Qt.ItemFlag.ItemIsEnabled)  # Read-only
        self._table.setItem(row, 1, arrow)

        # Column 2: Authority match
        if authority.found:
            match_text = f"{authority.id}\n{authority.preferred_name}"
            match_item = QTableWidgetItem(match_text)
            match_item.setForeground(self._COLOR_MATCH_FOUND)
            match_item.setToolTip(f"Authority ID: {authority.id}")
        else:
            match_item = QTableWidgetItem("No match")
            match_item.setForeground(self._COLOR_NO_MATCH)
            match_item.setToolTip("No matching authority record found")
        self._table.setItem(row, 2, match_item)

        # Column 3: Source with icon
        icon = self.SOURCE_ICONS.get(authority.source, "❓")
        source_text = f"{icon} {authority.source.title()}"
        source_item = QTableWidgetItem(source_text)
        source_item.setToolTip(f"Source: {authority.source}")
        self._table.setItem(row, 3, source_item)

        # Column 4: Confidence with color coding
        if authority.found:
            conf_text = f"{authority.confidence:.0%}"
            conf_item = QTableWidgetItem(conf_text)
            conf_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Color code confidence: green ≥90%, yellow ≥70%, red <70%
            # Always set dark foreground — light backgrounds are unreadable with white text
            conf_item.setForeground(QColor("#1f2937"))
            if authority.confidence >= self._HIGH_CONFIDENCE:
                conf_item.setBackground(self._COLOR_HIGH_CONFIDENCE)
                conf_item.setToolTip("High confidence match (≥90%)")
            elif authority.confidence >= self._MEDIUM_CONFIDENCE:
                conf_item.setBackground(self._COLOR_MEDIUM_CONFIDENCE)
                conf_item.setToolTip("Medium confidence match (70-89%)")
            else:
                conf_item.setBackground(self._COLOR_LOW_CONFIDENCE)
                conf_item.setToolTip("Low confidence match (<70%)")
        else:
            conf_item = QTableWidgetItem("—")
            conf_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            conf_item.setForeground(QColor("#6b7280"))
            conf_item.setToolTip("No confidence score (no match)")

        self._table.setItem(row, 4, conf_item)

        # Adjust row height for multi-line content
        self._table.resizeRowsToContents()

    def clear(self) -> None:
        """Clear all rows from the table."""
        self._table.setRowCount(0)

    def get_match_count(self) -> int:
        """Get the total number of matches displayed.

        Returns:
            Number of rows in the table

        """
        return self._table.rowCount()

    def get_found_count(self) -> int:
        """Get the number of successful authority matches.

        Returns:
            Number of rows where a match was found

        """
        count = 0
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 2)
            if item and item.text() != "No match":
                count += 1
        return count

    def set_match_data(self, matches: list[tuple[str, AuthorityMatch]]) -> None:
        """Set multiple match results at once.

        Clears existing data and populates the table with the provided matches.

        Args:
            matches: List of (extracted_name, authority_match) tuples

        """
        self.clear()
        for extracted, authority in matches:
            self.add_match(extracted, authority)
