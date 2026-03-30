"""Visual stage-progress widget showing six labelled nodes connected by arrows."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

_STAGE_NAMES: list[str] = [
    "1. Parse",
    "2. NER",
    "3. Authority",
    "4. RDF",
    "5. Validate",
    "6. Wikidata",
]

_STATE_COLORS: dict[str, QColor] = {
    "pending": QColor(180, 180, 180),
    "running": QColor(50, 130, 240),
    "done": QColor(60, 180, 75),
    "error": QColor(220, 50, 50),
}

_NODE_W = 80
_NODE_H = 32
_SPACING = 24
_LABEL_GAP = 6


class StageProgressWidget(QWidget):
    """Draws six labelled nodes connected by arrows to visualise pipeline progress."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._states: list[str] = ["pending"] * len(_STAGE_NAMES)
        self._progress_pct: int = 0
        total_w = len(_STAGE_NAMES) * _NODE_W + (len(_STAGE_NAMES) - 1) * _SPACING
        self.setMinimumSize(total_w + 20, _NODE_H + 40)
        self.setMaximumHeight(_NODE_H + 50)

    # ── Public API ────────────────────────────────────────────────────

    def set_stage_state(self, stage_index: int, state: str) -> None:
        """Set the visual state of the stage at *stage_index*."""
        if 0 <= stage_index < len(self._states) and state in _STATE_COLORS:
            self._states[stage_index] = state
            if state != "running":
                self._progress_pct = 0
            self.update()

    def set_progress(self, pct: int) -> None:
        """Accept a progress percentage (0-100) for the currently running stage.

        Finds the first stage in *running* state and updates its label.
        """
        self._progress_pct = pct
        self.update()

    def reset_all(self) -> None:
        """Reset every stage to *pending*."""
        self._states = ["pending"] * len(_STAGE_NAMES)
        self.update()

    # ── Painting ──────────────────────────────────────────────────────

    def paintEvent(self, event: object) -> None:  # noqa: N802
        """Render the stage nodes, arrows, and labels."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        total_w = len(_STAGE_NAMES) * _NODE_W + (len(_STAGE_NAMES) - 1) * _SPACING
        x_offset = (self.width() - total_w) / 2
        y_top = 4.0

        label_font = QFont()
        label_font.setPointSize(9)
        painter.setFont(label_font)

        rects: list[QRectF] = []
        for i in range(len(_STAGE_NAMES)):
            x = x_offset + i * (_NODE_W + _SPACING)
            rects.append(QRectF(x, y_top, _NODE_W, _NODE_H))

        # draw connecting lines
        pen = QPen(QColor(160, 160, 160), 2)
        painter.setPen(pen)
        for i in range(len(rects) - 1):
            r1 = rects[i]
            r2 = rects[i + 1]
            y_mid = r1.center().y()
            painter.drawLine(int(r1.right()), int(y_mid), int(r2.left()), int(y_mid))

        # draw nodes and labels
        for i, rect in enumerate(rects):
            color = _STATE_COLORS[self._states[i]]
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(color.darker(120), 2))
            painter.drawRoundedRect(rect, 6, 6)

            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, _STAGE_NAMES[i])

            # label below
            painter.setPen(Qt.GlobalColor.black)
            label_rect = QRectF(rect.x(), rect.bottom() + _LABEL_GAP, _NODE_W, 16)
            label = self._states[i]
            if self._states[i] == "running" and self._progress_pct > 0:
                label = f"{self._progress_pct}%"
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.end()
