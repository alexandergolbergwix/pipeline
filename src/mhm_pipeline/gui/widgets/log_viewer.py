"""Read-only log viewer widget with auto-scroll."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LogViewer(QWidget):
    """QPlainTextEdit (read-only, monospace, auto-scroll) with a clear button."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setFont(QFont("Menlo, Consolas, monospace", 10))
        self._text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        toolbar = QHBoxLayout()
        toolbar.addStretch()
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self.clear_log)
        toolbar.addWidget(self._clear_btn)

        layout.addWidget(self._text_edit, stretch=1)
        layout.addLayout(toolbar)

    # ── Public API ────────────────────────────────────────────────────

    def append_line(self, line: str) -> None:
        """Append *line* and scroll to the bottom."""
        self._text_edit.appendPlainText(line)
        scrollbar = self._text_edit.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def clear_log(self) -> None:
        """Remove all text from the viewer."""
        self._text_edit.clear()

    # ── Convenience ───────────────────────────────────────────────────

    def setMaximumBlockCount(self, count: int) -> None:  # noqa: N802
        """Limit the number of lines kept in the viewer."""
        self._text_edit.setMaximumBlockCount(count)
