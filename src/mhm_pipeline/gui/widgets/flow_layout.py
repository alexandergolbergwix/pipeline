"""FlowLayout — left-to-right layout that wraps to the next line on overflow.

Based on Qt's canonical ``QLayout`` example. Used for filter-chip rows, tag
clouds, and any horizontal group whose item count is dynamic and may exceed
the container width.

Cross-platform: pure Qt, no native calls.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """A layout that arranges its children left-to-right, wrapping as needed."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margin: int = 0,
        h_spacing: int = 8,
        v_spacing: int = 8,
    ) -> None:
        super().__init__(parent)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # ── QLayout API ───────────────────────────────────────────────────────

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    # ── Internal layout math ──────────────────────────────────────────────

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            wid = item.widget()
            if wid is None or not wid.isVisible():
                hs = self._h_spacing
                vs = self._v_spacing
            else:
                sp_h = wid.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Horizontal,
                )
                sp_v = wid.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Vertical,
                )
                hs = max(self._h_spacing, sp_h)
                vs = max(self._v_spacing, sp_v)

            next_x = x + item.sizeHint().width() + hs
            if next_x - hs > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + vs
                next_x = x + item.sizeHint().width() + hs
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + m.bottom()


def make_scrollable(
    widget: QWidget,
    *,
    horizontal: bool = True,
    vertical: bool = True,
    frameless: bool = True,
) -> Any:
    """Wrap *widget* in a ``QScrollArea`` with sensible defaults.

    Cross-platform, pure-Qt approach to guarantee that overflow content is
    reachable without clipping. When a component's natural size would exceed
    its allotted space, the scroll area provides native scrollbars.

    Args:
        widget: The content widget to make scrollable.
        horizontal: Enable a horizontal scroll bar when needed.
        vertical: Enable a vertical scroll bar when needed.
        frameless: Hide the scroll area's default 1-px frame (usually correct
            because the caller provides their own glass/content surface).

    Returns:
        A configured ``QScrollArea`` ready to be added to any layout.
    """
    from PyQt6.QtWidgets import QFrame, QScrollArea  # noqa: PLC0415

    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAsNeeded if horizontal
        else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    area.setVerticalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAsNeeded if vertical
        else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    if frameless:
        area.setFrameShape(QFrame.Shape.NoFrame)
    # Transparent viewport so our content surface shows through
    area.setStyleSheet("QScrollArea { background: transparent; }")
    vp = area.viewport()
    if vp is not None:
        vp.setStyleSheet("background: transparent;")
    return area
