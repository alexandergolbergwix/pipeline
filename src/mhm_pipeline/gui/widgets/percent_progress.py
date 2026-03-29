"""Simple progress bar widget for showing percentage completion."""

from __future__ import annotations

from PyQt6.QtWidgets import QProgressBar, QWidget


class PercentProgressWidget(QProgressBar):
    """A simple percentage-based progress bar.

    Shows 0-100% progress with text label.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(0)
        self.setTextVisible(True)
        self.setFormat("%p%")
        self.setMaximumHeight(20)

    def set_progress(self, pct: int) -> None:
        """Set the progress value (0-100)."""
        self.setValue(max(0, min(100, pct)))
