"""Read-only Turtle/RDF preview widget with basic syntax highlighting."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRegularExpression, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PyQt6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

_MAX_PREVIEW_LINES = 500


class _TurtleHighlighter(QSyntaxHighlighter):
    """Minimal syntax highlighter for Turtle/RDF content."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)

        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []

        # @ directives (e.g. @prefix, @base)
        fmt_directive = QTextCharFormat()
        fmt_directive.setForeground(QColor(30, 80, 200))
        self._rules.append(
            (QRegularExpression(r"^@.*$"), fmt_directive)
        )

        # URIs <http...>
        fmt_uri = QTextCharFormat()
        fmt_uri.setForeground(QColor(20, 140, 60))
        self._rules.append(
            (QRegularExpression(r"<http[^>]*>"), fmt_uri)
        )

        # Comments starting with #
        fmt_comment = QTextCharFormat()
        fmt_comment.setForeground(QColor(140, 140, 140))
        self._rules.append(
            (QRegularExpression(r"#.*$"), fmt_comment)
        )

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        """Apply highlighting rules to a single text block."""
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class TtlPreview(QWidget):
    """Read-only preview pane for Turtle/RDF files with syntax highlighting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setFont(QFont("Menlo, Consolas, monospace", 10))
        self._text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self._highlighter = _TurtleHighlighter(self._text_edit.document())

        layout.addWidget(self._text_edit)

    # ── Public API ────────────────────────────────────────────────────

    def load_file(self, path: Path) -> None:
        """Read the first 500 lines of *path* and display them."""
        try:
            with open(path, encoding="utf-8") as fh:
                lines = [fh.readline() for _ in range(_MAX_PREVIEW_LINES)]
            self._text_edit.setPlainText("".join(lines))
        except OSError:
            self._text_edit.setPlainText(f"[Could not read {path}]")

    def clear_preview(self) -> None:
        """Clear the preview pane."""
        self._text_edit.clear()
