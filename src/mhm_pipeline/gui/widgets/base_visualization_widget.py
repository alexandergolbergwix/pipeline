"""Abstract base class for pipeline visualization widgets.

Provides common interface and placeholder handling for all stage
visualization widgets in the MHM Pipeline GUI.
"""

from __future__ import annotations

from abc import abstractmethod

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)


def is_dark_mode(widget: QWidget | None = None) -> bool:
    """Detect if the application is running in dark mode.

    Checks the application palette to determine if the system
    is using a dark theme. This can be used to adjust colors
    for proper contrast in both light and dark modes.

    Args:
        widget: Optional widget to check the palette from.
               If None, uses the application palette.

    Returns:
        True if dark mode is detected, False otherwise.
    """
    if widget is not None:
        palette = widget.palette()
    else:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return False
        palette = app.palette()  # type: ignore[attr-defined]

    # Check window background brightness
    bg_color = palette.color(palette.ColorRole.Window)
    # Calculate luminance - if below threshold, it's dark mode
    luminance = (0.299 * bg_color.red() + 0.587 * bg_color.green() + 0.114 * bg_color.blue()) / 255
    return luminance < 0.5


class BaseVisualizationWidget(QWidget):
    """Abstract base class for all pipeline visualization widgets.

    Provides common interface for:
    - Placeholder display when no data is loaded
    - Clearing data
    - Checking if widget has data

    Subclasses must implement clear_data() and can override
    the placeholder text.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the base visualization widget.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._has_data = False
        self._placeholder_text = self._get_placeholder_text()

        # Main layout
        self._layout: QVBoxLayout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)

        # Placeholder label shown when no data
        self._placeholder_label = QLabel(self._placeholder_text)
        self._placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Use palette color for proper dark mode support
        self._placeholder_label.setStyleSheet("""
            QLabel {
                font-style: italic;
                padding: 40px;
            }
        """)
        self._layout.addWidget(self._placeholder_label)

    def _get_placeholder_text(self) -> str:
        """Return the placeholder text shown when no data is loaded.

        Subclasses can override this to provide stage-specific text.

        Returns:
            Placeholder text string.
        """
        return "No data loaded. Run the pipeline stage to see results."

    @property
    def has_data(self) -> bool:
        """Return True if the widget currently has data loaded.

        Returns:
            Boolean indicating if data has been loaded.
        """
        return self._has_data

    def _show_placeholder(self) -> None:
        """Show the placeholder label.

        Should be called by subclasses when clearing data.
        """
        self._placeholder_label.setVisible(True)

    def _hide_placeholder(self) -> None:
        """Hide the placeholder label.

        Should be called by subclasses when loading data.
        """
        self._placeholder_label.setVisible(False)

    @abstractmethod
    def clear_data(self) -> None:
        """Clear all displayed data and reset to initial state.

        This method must be implemented by subclasses to clear
        their specific data views and reset the widget state.
        """
        ...
