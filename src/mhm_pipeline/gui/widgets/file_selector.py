"""Reusable file / directory selector widget."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)


class FileSelector(QWidget):
    """Label + line-edit + browse button for selecting a file or directory."""

    path_changed = pyqtSignal(Path)

    def __init__(
        self,
        label: str,
        mode: str = "open",
        filter: str = "All Files (*)",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._filter = filter

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(label)
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("No file selected")
        self._edit.textChanged.connect(self._on_text_changed)

        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._browse)

        layout.addWidget(self._label)
        layout.addWidget(self._edit, stretch=1)
        layout.addWidget(self._browse_btn)

    # ── Properties ────────────────────────────────────────────────────

    @property
    def path(self) -> Path | None:
        """Return the currently selected path, or *None* if empty."""
        text = self._edit.text().strip()
        if not text:
            return None
        return Path(text)

    @path.setter
    def path(self, value: Path | None) -> None:
        self._edit.setText(str(value) if value else "")

    # ── Slots ─────────────────────────────────────────────────────────

    def _browse(self) -> None:
        """Open a native file dialog matching the configured mode."""
        if self._mode == "directory":
            result = QFileDialog.getExistingDirectory(self, "Select Directory")
        else:
            result, _ = QFileDialog.getOpenFileName(self, "Open File", "", self._filter)

        if result:
            self._edit.setText(result)

    def _on_text_changed(self, text: str) -> None:
        text = text.strip()
        if text:
            self.path_changed.emit(Path(text))
