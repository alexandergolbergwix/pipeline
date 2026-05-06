"""Toggle switch — a painted on/off control with a sliding knob.

Visual convention matches macOS / iOS / modern web switches (Linear, Notion,
Stripe Dashboard): a pill-shaped track that fills with the accent colour
when on, empty when off, with an animated knob that slides between the two
positions.

Used where a true **binary-state** setting is needed — "enable model X",
"dark mode", etc. Preferable to ``QCheckBox`` when the label describes an
action state (adjective) rather than an item selection. Apple HIG
specifically recommends switches for "on/off", checkboxes for "included in
a group".

Pure ``QPainter`` — works identically on macOS and Windows, no ctypes.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import QAbstractButton, QWidget


class ToggleSwitch(QAbstractButton):
    """A painted on/off switch that behaves like a ``QAbstractButton``.

    API intentionally mirrors ``QCheckBox``: ``isChecked()``,
    ``setChecked(bool)``, ``toggled(bool)`` signal, so it's a drop-in
    replacement in most contexts.

    Args:
        parent: Parent widget.
        width: Total track width in px. 40 is standard; 36 on dense UIs.
        height: Track height in px (and diameter of the knob).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        width: int = 40,
        height: int = 22,
    ) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._track_w = width
        self._track_h = height
        # Knob position is animated from 0.0 (off / left) to 1.0 (on / right)
        self._knob_pos: float = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(140)  # snappy — HIG 120–160ms
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._on_toggled)

    # ── Animated property ────────────────────────────────────────────────

    def _get_knob_pos(self) -> float:
        return self._knob_pos

    def _set_knob_pos(self, pos: float) -> None:
        self._knob_pos = max(0.0, min(1.0, float(pos)))
        self.update()

    knobPos = pyqtProperty(float, fget=_get_knob_pos, fset=_set_knob_pos)  # type: ignore[call-arg, misc]

    def _on_toggled(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        self._anim.stop()
        self._anim.setStartValue(self._knob_pos)
        self._anim.setEndValue(target)
        self._anim.start()

    # ── Sizing ───────────────────────────────────────────────────────────

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._track_w, self._track_h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    # ── Interaction — make any click toggle ────────────────────────────

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.setChecked(not self.isChecked())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setChecked(not self.isChecked())
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Paint ────────────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        dark = theme.is_dark()
        w = self._track_w
        h = self._track_h
        r = h / 2.0
        enabled = self.isEnabled()

        # Track colours interpolate between off-bg and accent-on based on pos
        if dark:
            off_bg = QColor(255, 255, 255, 30)
            on_bg = QColor(99, 102, 241, 230)     # indigo accent
            rim = QColor(255, 255, 255, 40)
            knob_off = QColor(230, 232, 238)
            knob_on = QColor(255, 255, 255)
        else:
            off_bg = QColor(0, 0, 0, 30)
            on_bg = QColor(79, 91, 213, 230)
            rim = QColor(0, 0, 0, 30)
            knob_off = QColor(255, 255, 255)
            knob_on = QColor(255, 255, 255)

        # Blend track bg from off→on via knob_pos
        def _lerp(a: int, b: int, t: float) -> int:
            return int(a + (b - a) * t)

        t = self._knob_pos
        if not enabled:
            t *= 0.35
        track_col = QColor(
            _lerp(off_bg.red(), on_bg.red(), t),
            _lerp(off_bg.green(), on_bg.green(), t),
            _lerp(off_bg.blue(), on_bg.blue(), t),
            _lerp(off_bg.alpha(), on_bg.alpha(), t),
        )
        if not enabled:
            track_col.setAlpha(int(track_col.alpha() * 0.6))

        # Track body
        p.setPen(QPen(rim, 1.0))
        p.setBrush(QBrush(track_col))
        track_rect = QRectF(0.5, 0.5, w - 1, h - 1)
        p.drawRoundedRect(track_rect, r, r)

        # Knob
        knob_pad = 2.0
        knob_d = h - knob_pad * 2
        x_off = knob_pad + (w - knob_d - knob_pad * 2) * self._knob_pos
        knob_col = knob_on if self._knob_pos > 0.5 else knob_off
        # Soft shadow under the knob
        shadow = QColor(0, 0, 0, 60 if dark else 40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(shadow))
        p.drawEllipse(
            QPointF(x_off + knob_d / 2 + 0.5, knob_pad + knob_d / 2 + 1.2),
            knob_d / 2,
            knob_d / 2,
        )
        p.setBrush(QBrush(knob_col))
        p.drawEllipse(
            QPointF(x_off + knob_d / 2, knob_pad + knob_d / 2),
            knob_d / 2,
            knob_d / 2,
        )

        # Focus ring (keyboard focus only)
        if self.hasFocus():
            focus_col = QColor(99, 102, 241, 120) if dark else QColor(79, 91, 213, 140)
            p.setPen(QPen(focus_col, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(
                QRectF(-2, -2, w + 4, h + 4), r + 2, r + 2,
            )
        p.end()
