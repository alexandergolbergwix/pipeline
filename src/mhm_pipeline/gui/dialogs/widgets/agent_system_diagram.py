"""Live animated system-design diagram for the AI verification dialog.

Renders the eval-agent's run as a system-architecture diagram in motion:
ten logical nodes (Inputs / Rubrics / four Evaluators / Gemini / Cache /
Results / Summary), bezier edges with arrowheads, particles flowing
along edges, animated dashed strokes on active edges, and pulsing
glows on active nodes. Drives off the live ``substep`` /
``stats_update`` / ``log_line`` / ``error`` / ``finished`` signals
from ``EvalAgentWorker``.

Reference: React Flow animated dashed edges, LangGraph Studio's live
execution view, LangSmith node-by-node tracing.
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.dialogs.widgets.friendly_copy import humanise_model

# ── Layout (scene coordinates) ──────────────────────────────────────

_NODE_W: int = 160
_NODE_H: int = 60

_LAYOUT: dict[str, tuple[int, int]] = {
    "inputs":            (40,  240),
    "rubric":            (240, 240),
    "person_ner":        (480, 130),
    "provenance_ner":    (480, 200),
    "contents_ner":      (480, 270),
    "genre_classifier":  (480, 340),
    "tools":             (720, 110),
    "gemini":            (720, 240),
    "escalate":          (920, 110),
    "cache":             (720, 380),
    "results":           (1120, 240),
    "summary":           (1120, 380),
}

_EVALUATOR_KEYS_ORDERED: list[str] = [
    "person_ner", "provenance_ner", "contents_ner", "genre_classifier",
]
_EVALUATOR_KEYS: set[str] = set(_EVALUATOR_KEYS_ORDERED)

_PIPELINE_KEYS: list[str] = [
    "inputs", "rubric",
    "person_ner", "provenance_ner", "contents_ner", "genre_classifier",
    "tools", "gemini", "escalate", "cache", "results", "summary",
]

_FRIENDLY_NODES: dict[str, tuple[str, str]] = {
    "inputs":            ("Inputs",      "marc + ner_results"),
    "rubric":            ("Rubrics",     "evaluator prompts"),
    "person_ner":        ("Person AI",   "idle"),
    "provenance_ner":    ("Owner AI",    "idle"),
    "contents_ner":      ("Contents AI", "idle"),
    "genre_classifier": ("Genre AI",    "idle"),
    "tools":             ("Tools",       "marc · notes · authority"),
    "gemini":            ("Tier-1 judge", "fast model"),
    "escalate":          ("Escalate",    "stronger model"),
    "cache":             ("Cache",       "verdict store"),
    "results":           ("Results",     "results.jsonl"),
    "summary":           ("Summary",     "report.md"),
}

# Friendly short phrases for the four agent tools.
_TOOL_FRIENDLY: dict[str, str] = {
    "fetch_marc_field":    "reading MARC",
    "expand_note":         "expanding notes",
    "list_record_entities": "listing entities",
    "lookup_authority":    "checking authority",
}

# Forward edges and the dotted cache→evaluator loop-back. Tuples are
# (from_key, to_key, dotted).
_EDGES: list[tuple[str, str, bool]] = [
    ("inputs",           "rubric",            False),
    ("rubric",           "person_ner",        False),
    ("rubric",           "provenance_ner",    False),
    ("rubric",           "contents_ner",      False),
    ("rubric",           "genre_classifier",  False),
    ("person_ner",       "gemini",            False),
    ("provenance_ner",   "gemini",            False),
    ("contents_ner",     "gemini",            False),
    ("genre_classifier", "gemini",            False),
    ("gemini",           "cache",             False),
    ("gemini",           "results",           False),
    ("results",          "summary",           False),
    # Agentic tool-loop: model calls a tool, observation returns.
    ("gemini",           "tools",             True),
    ("tools",            "gemini",            True),
    # Conditional escalation to a stronger model, then to results.
    ("gemini",           "escalate",          True),
    ("escalate",         "results",           False),
    # Dotted cache→evaluators loop-back: activates only on cache hit.
    ("cache",            "person_ner",        True),
    ("cache",            "provenance_ner",    True),
    ("cache",            "contents_ner",      True),
    ("cache",            "genre_classifier",  True),
]

# Friendly evaluator-id mappings produced by ``humanise_log_line``.
_RAW_JUDGING_RE = re.compile(
    r"judging\s+(?P<ev>[\w\-]+)\s+(?P<n>\d+)\s*/\s*(?P<m>\d+)",
    re.IGNORECASE,
)
_FRIENDLY_CHECKING_RE = re.compile(
    r"checking\s+(?P<ev>.+?)\s+(?P<n>\d+)\s+of\s+(?P<m>\d+)",
    re.IGNORECASE,
)
_FRIENDLY_TO_KEY: dict[str, str] = {
    "person ai":   "person_ner",
    "owner ai":    "provenance_ner",
    "contents ai": "contents_ner",
    "genre ai":    "genre_classifier",
    "place ai":    "place_ner",
}

# Agentic tool-loop events. Tolerate an optional ``[STEP] `` prefix.
_TOOL_RE = re.compile(
    r"(?:\[step\]\s*)?tool\s+(?P<tool>[\w\-]+)",
    re.IGNORECASE,
)
_ESCALATE_RE = re.compile(
    r"(?:\[step\]\s*)?escalate\s+(?P<model>[\w\-.:]+)",
    re.IGNORECASE,
)


def _parse_substep_line(text: str) -> dict[str, Any] | None:
    """Parse a substep line into a structured action dict.

    Returns ``None`` for anything we don't recognise.
    """
    if not text:
        return None
    lowered = text.strip().lower()
    if "loading rubric" in lowered or "loading prompts" in lowered:
        return {"action": "load_rubrics"}
    if (
        "writing results" in lowered
        or "saving verdicts" in lowered
        or "writing report" in lowered
    ):
        return {"action": "writing"}

    m = _TOOL_RE.search(text)
    if m is not None:
        return {"action": "tool", "tool": m.group("tool").strip().lower()}

    m = _ESCALATE_RE.search(text)
    if m is not None:
        return {"action": "escalate", "model": m.group("model").strip()}

    m = _RAW_JUDGING_RE.search(text)
    if m is not None:
        return {
            "action": "judging",
            "evaluator_id": m.group("ev").strip().lower(),
            "current": int(m.group("n")),
            "total": int(m.group("m")),
        }

    m = _FRIENDLY_CHECKING_RE.search(text)
    if m is not None:
        ev_friendly = m.group("ev").strip().lower()
        ev_key = _FRIENDLY_TO_KEY.get(ev_friendly)
        if ev_key is None:
            return None
        return {
            "action": "judging",
            "evaluator_id": ev_key,
            "current": int(m.group("n")),
            "total": int(m.group("m")),
        }
    return None


# ── Colour helpers ──────────────────────────────────────────────────


def _success_color() -> str:
    return theme.SEMANTIC_SUCCESS_DARK if theme.is_dark() else theme.SEMANTIC_SUCCESS


def _error_color() -> str:
    return theme.SEMANTIC_ERROR_DARK if theme.is_dark() else theme.SEMANTIC_ERROR


def _highlight_color() -> str:
    value = theme.ui("highlight")
    if value == "#888888":
        # Fall back to a usable accent if the theme registry has no
        # explicit highlight token.
        return theme.SEMANTIC_INFO_DARK if theme.is_dark() else theme.SEMANTIC_INFO
    return value


def _idle_edge_color() -> QColor:
    """Idle edge stroke: text colour at 40% alpha — visible on either
    backdrop without overpowering."""
    color = QColor(theme.ui("text"))
    color.setAlphaF(0.40)
    return color


# ── Particle ───────────────────────────────────────────────────────


class _Particle(QGraphicsObject):
    """A small circle that travels along a bezier path from 0.0 → 1.0."""

    def __init__(
        self,
        path: QPainterPath,
        color_hex: str,
        *,
        radius: float = 4.5,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._color = QColor(color_hex)
        self._radius = float(radius)
        self._pos_t = 0.0
        self.setZValue(20.0)
        self._update_pos()

    def boundingRect(self) -> QRectF:  # noqa: N802 — Qt API
        r = self._radius + 2.0
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(
        self,
        painter: QPainter,
        _option: Any,
        _widget: QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Glow halo.
        halo = QColor(self._color)
        halo.setAlpha(80)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(QPointF(0, 0), self._radius + 2.0, self._radius + 2.0)
        # Core.
        painter.setBrush(QBrush(self._color))
        painter.drawEllipse(QPointF(0, 0), self._radius, self._radius)

    def _get_pos_t(self) -> float:
        return self._pos_t

    def _set_pos_t(self, value: float) -> None:
        self._pos_t = float(value)
        self._update_pos()

    pos_t = pyqtProperty(float, fget=_get_pos_t, fset=_set_pos_t)

    def _update_pos(self) -> None:
        if self._path.isEmpty():
            return
        point = self._path.pointAtPercent(max(0.0, min(1.0, self._pos_t)))
        self.setPos(point)


# ── Node ───────────────────────────────────────────────────────────


class _AgentNode(QGraphicsObject):
    """A rounded-rect card with title + status line and animated states."""

    STATE_IDLE: str = "idle"
    STATE_ACTIVE: str = "active"
    STATE_DONE: str = "done"
    STATE_ERROR: str = "error"

    def __init__(
        self,
        key: str,
        title: str,
        status: str,
        x: float,
        y: float,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self._title = title
        self._status = status
        self.setPos(x, y)
        self._state = self.STATE_IDLE
        self._opacity_value = 0.85
        self._pulse_group: QSequentialAnimationGroup | None = None
        self._error_flash: QSequentialAnimationGroup | None = None
        self._glow_effect: QGraphicsDropShadowEffect | None = None
        self._glow_blur = 0.0
        self.setOpacity(self._opacity_value)
        self.setZValue(10.0)

    # ── Geometry ────────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:  # noqa: N802 — Qt API
        # Include outer-glow margin so we don't get paint clipping.
        return QRectF(-4, -4, _NODE_W + 8, _NODE_H + 8)

    def card_rect(self) -> QRectF:
        return QRectF(0, 0, _NODE_W, _NODE_H)

    def center_scene(self) -> QPointF:
        return self.mapToScene(QPointF(_NODE_W / 2.0, _NODE_H / 2.0))

    def anchor_left(self) -> QPointF:
        return self.mapToScene(QPointF(0, _NODE_H / 2.0))

    def anchor_right(self) -> QPointF:
        return self.mapToScene(QPointF(_NODE_W, _NODE_H / 2.0))

    # ── Paint ───────────────────────────────────────────────────────

    def paint(
        self,
        painter: QPainter,
        _option: Any,
        _widget: QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        fill_color, border_color, border_width = self._palette_for_state()
        text_color = QColor(theme.ui("text"))
        sub_color = QColor(theme.ui("subtext"))
        if self._state == self.STATE_IDLE:
            sub_color = QColor(theme.ui("subtext"))

        # Card fill + border.
        painter.setBrush(QBrush(fill_color))
        painter.setPen(QPen(border_color, float(border_width)))
        painter.drawRoundedRect(
            self.card_rect(), theme.RADIUS_LG, theme.RADIUS_LG,
        )

        # Title.
        title_font = QFont()
        title_font.setPixelSize(theme.FONT_BASE)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(text_color)
        painter.drawText(
            QRectF(theme.SPACE_MD, theme.SPACE_SM,
                   _NODE_W - 2 * theme.SPACE_MD - theme.SPACE_LG,
                   theme.FONT_LG + 4),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._title,
        )

        # Status line.
        status_font = QFont()
        status_font.setPixelSize(theme.FONT_SM)
        painter.setFont(status_font)
        painter.setPen(sub_color)
        painter.drawText(
            QRectF(theme.SPACE_MD,
                   _NODE_H - theme.FONT_SM - theme.SPACE_SM - 2,
                   _NODE_W - 2 * theme.SPACE_MD,
                   theme.FONT_SM + 4),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._status,
        )

        # Badge top-right (state glyph).
        glyph = self._state_glyph()
        if glyph:
            badge_color = self._state_badge_color()
            badge_font = QFont()
            badge_font.setPixelSize(theme.FONT_LG)
            badge_font.setWeight(QFont.Weight.Bold)
            painter.setFont(badge_font)
            painter.setPen(badge_color)
            painter.drawText(
                QRectF(_NODE_W - theme.SPACE_LG - 8, 4,
                       theme.SPACE_LG + 4, theme.FONT_LG + 4),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
                glyph,
            )

    def _palette_for_state(self) -> tuple[QColor, QColor, int]:
        panel = QColor(theme.ui("panel_bg"))
        if self._state == self.STATE_ACTIVE:
            fill = QColor(_highlight_color())
            fill.setAlphaF(0.18)
            return fill, QColor(_highlight_color()), 3
        if self._state == self.STATE_DONE:
            fill = QColor(_success_color())
            fill.setAlphaF(0.12)
            return fill, QColor(_success_color()), 2
        if self._state == self.STATE_ERROR:
            fill = QColor(_error_color())
            fill.setAlphaF(0.15)
            return fill, QColor(_error_color()), 2
        # idle — 70% panel alpha.
        panel.setAlphaF(0.70)
        return panel, QColor(theme.ui("border")), 1

    def _state_glyph(self) -> str:
        if self._state == self.STATE_ACTIVE:
            return "⚡"
        if self._state == self.STATE_DONE:
            return "✓"
        if self._state == self.STATE_ERROR:
            return "✗"
        return ""

    def _state_badge_color(self) -> QColor:
        if self._state == self.STATE_ACTIVE:
            return QColor(_highlight_color())
        if self._state == self.STATE_DONE:
            return QColor(_success_color())
        if self._state == self.STATE_ERROR:
            return QColor(_error_color())
        return QColor(theme.ui("subtext"))

    # ── Animated properties ─────────────────────────────────────────

    def _get_glow_blur(self) -> float:
        return self._glow_blur

    def _set_glow_blur(self, value: float) -> None:
        self._glow_blur = float(value)
        if self._glow_effect is not None:
            try:
                self._glow_effect.setBlurRadius(self._glow_blur)
            except RuntimeError:
                # Effect was deleted out from under us.
                self._glow_effect = None

    glow_blur = pyqtProperty(float, fget=_get_glow_blur, fset=_set_glow_blur)

    def _get_opacity_val(self) -> float:
        return self._opacity_value

    def _set_opacity_val(self, value: float) -> None:
        self._opacity_value = float(value)
        self.setOpacity(self._opacity_value)

    opacity_value = pyqtProperty(
        float, fget=_get_opacity_val, fset=_set_opacity_val,
    )

    # ── State transitions ───────────────────────────────────────────

    def set_status(self, text: str) -> None:
        self._status = text
        self.update()

    def state(self) -> str:
        return self._state

    def set_idle(self) -> None:
        self._stop_animations()
        self._clear_glow_effect()
        self._state = self.STATE_IDLE
        self._set_opacity_val(0.85)
        self.update()

    def set_active(self) -> None:
        if self._state == self.STATE_ACTIVE:
            return
        self._stop_animations()
        self._state = self.STATE_ACTIVE
        self._set_opacity_val(1.0)
        self._install_glow_effect()
        self._start_pulse()
        self.update()

    def set_done(self) -> None:
        self._stop_animations()
        self._clear_glow_effect()
        self._state = self.STATE_DONE
        self._set_opacity_val(1.0)
        self.update()

    def set_error(self) -> None:
        self._stop_animations()
        self._clear_glow_effect()
        self._state = self.STATE_ERROR
        self._set_opacity_val(1.0)
        self._start_error_flash()
        self.update()

    # ── Animation plumbing ──────────────────────────────────────────

    def _install_glow_effect(self) -> None:
        effect = QGraphicsDropShadowEffect()
        effect.setColor(QColor(_highlight_color()))
        effect.setOffset(0.0, 0.0)
        effect.setBlurRadius(18.0)
        self.setGraphicsEffect(effect)
        self._glow_effect = effect
        self._glow_blur = 18.0

    def _clear_glow_effect(self) -> None:
        if self._glow_effect is not None:
            try:
                self.setGraphicsEffect(None)
            except RuntimeError:
                pass
            self._glow_effect = None
        self._glow_blur = 0.0

    def _start_pulse(self) -> None:
        # Ping-pong glow blur 14 ↔ 22 px on a 1200 ms loop.
        group = QSequentialAnimationGroup(self)
        up = QPropertyAnimation(self, b"glow_blur")
        up.setDuration(600)
        up.setStartValue(14.0)
        up.setEndValue(22.0)
        up.setEasingCurve(QEasingCurve.Type.InOutQuad)
        down = QPropertyAnimation(self, b"glow_blur")
        down.setDuration(600)
        down.setStartValue(22.0)
        down.setEndValue(14.0)
        down.setEasingCurve(QEasingCurve.Type.InOutQuad)
        group.addAnimation(up)
        group.addAnimation(down)
        group.setLoopCount(-1)
        group.start()
        self._pulse_group = group

    def _start_error_flash(self) -> None:
        group = QSequentialAnimationGroup(self)
        for _ in range(3):
            down = QPropertyAnimation(self, b"opacity_value")
            down.setDuration(180)
            down.setStartValue(1.0)
            down.setEndValue(0.4)
            down.setEasingCurve(QEasingCurve.Type.InOutQuad)
            up = QPropertyAnimation(self, b"opacity_value")
            up.setDuration(180)
            up.setStartValue(0.4)
            up.setEndValue(1.0)
            up.setEasingCurve(QEasingCurve.Type.InOutQuad)
            group.addAnimation(down)
            group.addAnimation(up)
        group.start()
        self._error_flash = group

    def _stop_animations(self) -> None:
        for anim in (self._pulse_group, self._error_flash):
            if anim is None:
                continue
            try:
                anim.stop()
                anim.deleteLater()
            except RuntimeError:
                pass
        self._pulse_group = None
        self._error_flash = None


# ── Edge ───────────────────────────────────────────────────────────


def _build_bezier(a: QPointF, b: QPointF) -> QPainterPath:
    """Build a cubic-bezier path from ``a`` to ``b`` with horizontal handles."""
    dx = (b.x() - a.x()) * 0.5
    c1 = QPointF(a.x() + dx, a.y())
    c2 = QPointF(b.x() - dx, b.y())
    path = QPainterPath(a)
    path.cubicTo(c1, c2, b)
    return path


def _arrowhead_polygon(tip: QPointF, angle_rad: float) -> QPolygonF:
    """Build a filled-triangle arrowhead at ``tip`` aligned to ``angle_rad``.

    Triangle: 10 px length, 8 px base, pointed in the +x direction
    after rotation. ``angle_rad`` is the tangent angle of the curve at
    ``tip`` — we orient the arrow's spine to match.
    """
    length = 10.0
    half_base = 4.0
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    # Tip first; then the two base corners offset along (-spine + ±perp).
    base_x = tip.x() - length * cos_a
    base_y = tip.y() - length * sin_a
    # Perpendicular unit vector.
    perp_x = -sin_a
    perp_y = cos_a
    p1 = QPointF(base_x + half_base * perp_x, base_y + half_base * perp_y)
    p2 = QPointF(base_x - half_base * perp_x, base_y - half_base * perp_y)
    poly = QPolygonF()
    poly.append(tip)
    poly.append(p1)
    poly.append(p2)
    return poly


class _EdgeAnimator(QObject):
    """QObject proxy carrying the animatable ``dash_offset`` property
    for ``_AgentEdge`` (which is a plain QGraphicsPathItem, not a
    QObject — so it can't host QPropertyAnimation directly)."""

    def __init__(self, edge: _AgentEdge) -> None:
        super().__init__()
        self._edge = edge
        self._dash_offset = 0.0

    def _get_dash_offset(self) -> float:
        return self._dash_offset

    def _set_dash_offset(self, value: float) -> None:
        self._dash_offset = float(value)
        self._edge._apply_dash_offset(self._dash_offset)

    dash_offset = pyqtProperty(
        float, fget=_get_dash_offset, fset=_set_dash_offset,
    )


class _AgentEdge(QGraphicsPathItem):
    """A bezier edge between two nodes — visible at all times, animated
    when active, with an arrowhead at the destination end."""

    STATE_IDLE: str = "idle"
    STATE_ACTIVE: str = "active"
    STATE_DONE: str = "done"
    STATE_ERROR: str = "error"

    def __init__(
        self,
        from_node: _AgentNode,
        to_node: _AgentNode,
        *,
        dotted: bool,
        from_side: str = "right",
        to_side: str = "left",
    ) -> None:
        super().__init__()
        self._from_node = from_node
        self._to_node = to_node
        self._dotted = bool(dotted)
        self._from_side = from_side
        self._to_side = to_side
        self._state = self.STATE_IDLE
        self._dash_offset = 0.0
        self._animator = _EdgeAnimator(self)
        self._dash_anim: QPropertyAnimation | None = None
        self.setZValue(1.0)
        self.refresh_path()
        self._apply_pen()

    def refresh_path(self) -> None:
        a = (self._from_node.anchor_right()
             if self._from_side == "right"
             else self._from_node.anchor_left())
        b = (self._to_node.anchor_left()
             if self._to_side == "left"
             else self._to_node.anchor_right())
        # Cache loopback: bottom→bottom for visual separation.
        if self._dotted:
            a = self._from_node.mapToScene(QPointF(_NODE_W / 2.0, 0))
            b = self._to_node.mapToScene(QPointF(_NODE_W / 2.0, _NODE_H))
        self.setPath(_build_bezier(a, b))

    # ── Animated dash offset (driven by _EdgeAnimator proxy) ────────

    def _apply_dash_offset(self, value: float) -> None:
        self._dash_offset = float(value)
        pen = self.pen()
        pen.setDashOffset(self._dash_offset)
        self.setPen(pen)

    # ── State transitions ───────────────────────────────────────────

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self._stop_dash_anim()
        self._apply_pen()
        if state == self.STATE_ACTIVE:
            self._start_dash_anim()
        self.update()

    def set_idle(self) -> None:
        self.set_state(self.STATE_IDLE)

    def set_active(self) -> None:
        self.set_state(self.STATE_ACTIVE)

    def set_done(self) -> None:
        self.set_state(self.STATE_DONE)

    def set_error(self) -> None:
        self.set_state(self.STATE_ERROR)

    # ── Pen construction ────────────────────────────────────────────

    def _apply_pen(self) -> None:
        color: QColor
        width: float
        dash_pattern: list[float] | None
        if self._state == self.STATE_ACTIVE:
            color = QColor(_highlight_color())
            width = 3.0
            dash_pattern = [10.0, 6.0]
        elif self._state == self.STATE_DONE:
            color = QColor(_success_color())
            width = 2.0
            dash_pattern = None
        elif self._state == self.STATE_ERROR:
            color = QColor(_error_color())
            width = 2.5
            dash_pattern = None
        else:
            color = _idle_edge_color()
            width = 2.5
            dash_pattern = None

        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if self._dotted and dash_pattern is None:
            # Permanent dotted style for the cache loopback.
            pen.setDashPattern([2.0, 4.0])
        elif dash_pattern is not None:
            pen.setDashPattern(dash_pattern)
        else:
            pen.setStyle(Qt.PenStyle.SolidLine)
        pen.setDashOffset(self._dash_offset)
        self.setPen(pen)

    def _start_dash_anim(self) -> None:
        anim = QPropertyAnimation(self._animator, b"dash_offset")
        anim.setDuration(1200)
        anim.setStartValue(0.0)
        anim.setEndValue(-16.0)
        anim.setLoopCount(-1)
        anim.start()
        self._dash_anim = anim

    def _stop_dash_anim(self) -> None:
        if self._dash_anim is not None:
            try:
                self._dash_anim.stop()
                self._dash_anim.deleteLater()
            except RuntimeError:
                pass
            self._dash_anim = None

    # ── Paint: stroke + arrowhead ──────────────────────────────────

    def paint(
        self,
        painter: QPainter,
        option: Any,
        widget: QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        super().paint(painter, option, widget)
        # Arrowhead at the destination end (skip the cache loopback —
        # the dotted permanent pattern reads as a feedback link).
        if self._dotted:
            return
        path = self.path()
        if path.isEmpty():
            return
        tip = path.pointAtPercent(1.0)
        # Approximate the tangent by sampling slightly before the end.
        sample = path.pointAtPercent(0.985)
        dx = tip.x() - sample.x()
        dy = tip.y() - sample.y()
        if dx == 0.0 and dy == 0.0:
            return
        angle = math.atan2(dy, dx)
        arrow = _arrowhead_polygon(tip, angle)
        pen = self.pen()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(pen.color()))
        painter.drawPolygon(arrow)


# ── The diagram widget ─────────────────────────────────────────────


class AgentSystemDiagram(QWidget):
    """Live animated system-design diagram for the AI verification dialog.

    Public slots (connect worker signals to these):
      * ``on_substep(str)``     ← ``worker.substep``
      * ``on_stats(dict)``      ← ``worker.stats_update``
      * ``on_log_line(str)``    ← ``worker.log_line``
      * ``on_error(str)``       ← ``worker.error``
      * ``on_finished()``       ← ``worker.finished``  (parameter ignored)

    ``reset()`` returns every node to idle and drains in-flight
    animations.

    ``apply_summary_counts(csv_path)`` reads the per-evaluator totals
    from the eval-agent's ``summary.csv`` and updates each evaluator
    node's status line. Defensive: silently no-ops on parse errors.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._view = QGraphicsView(self._scene, self)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self._view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self._view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._view.setStyleSheet("QGraphicsView { background: transparent; }")
        self._view.viewport().setAutoFillBackground(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._view)

        self._nodes: dict[str, _AgentNode] = {}
        self._edges: list[_AgentEdge] = []
        self._edges_by_pair: dict[tuple[str, str], _AgentEdge] = {}
        self._particles: list[_Particle] = []
        self._particle_anims: list[QPropertyAnimation] = []
        self._touched_nodes: set[str] = set()
        self._done_after_finished: set[str] = set()

        # Track stats deltas + round-robin evaluator selection.
        self._last_stats: dict[str, Any] = {}
        self._last_cache_hits: int = 0
        self._last_judged: int = 0
        self._last_total: int = 0
        self._current_active_evaluator: str | None = None
        self._active_evaluator_index: int = 0
        self._per_eval_counts: dict[str, tuple[int, int]] = {}

        self._build_scene()

    # ── Public API ──────────────────────────────────────────────────

    def node_count(self) -> int:
        return len(self._nodes)

    def reset(self) -> None:
        """Return every node to idle and drain in-flight animations."""
        for anim in list(self._particle_anims):
            try:
                anim.stop()
                anim.deleteLater()
            except RuntimeError:
                pass
        self._particle_anims.clear()

        for particle in list(self._particles):
            try:
                self._scene.removeItem(particle)
                particle.deleteLater()
            except RuntimeError:
                pass
        self._particles.clear()

        for node in self._nodes.values():
            node.set_idle()
            friendly = _FRIENDLY_NODES.get(node._key, (node._key, "idle"))
            node.set_status(friendly[1])

        for edge in self._edges:
            edge.set_idle()

        self._touched_nodes.clear()
        self._done_after_finished.clear()
        self._last_stats = {}
        self._last_cache_hits = 0
        self._last_judged = 0
        self._last_total = 0
        self._current_active_evaluator = None
        self._active_evaluator_index = 0
        self._per_eval_counts.clear()

    @pyqtSlot(str)
    def on_substep(self, text: str) -> None:
        """Drive node activation from ``worker.substep`` emissions."""
        parsed = _parse_substep_line(text)
        if parsed is None:
            return
        action = parsed.get("action")

        if action == "load_rubrics":
            self._activate("inputs")
            self._activate("rubric")
            self._activate_edge("inputs", "rubric")
            return

        if action == "judging":
            evaluator_id = str(parsed.get("evaluator_id") or "")
            current = int(parsed.get("current") or 0)
            total = int(parsed.get("total") or 0)
            if evaluator_id not in self._nodes:
                return
            self._per_eval_counts[evaluator_id] = (current, total)
            self._activate("rubric")
            self._activate(evaluator_id)
            self._set_status(
                evaluator_id, f"{current} / {total} judged",
            )
            self._current_active_evaluator = evaluator_id
            self._activate_edge("rubric", evaluator_id)
            self._activate_edge(evaluator_id, "gemini")
            self._launch_particle(
                evaluator_id, "gemini", color_hex=_highlight_color(),
            )
            self._activate("gemini")
            return

        if action == "tool":
            tool_name = str(parsed.get("tool") or "")
            friendly = _TOOL_FRIENDLY.get(tool_name, tool_name or "tool call")
            self._activate("gemini")
            self._activate("tools")
            self._set_status("tools", friendly)
            self._activate_edge("gemini", "tools")
            self._activate_edge("tools", "gemini")
            self._launch_particle(
                "gemini", "tools", color_hex=_highlight_color(),
            )
            self._launch_particle(
                "tools", "gemini", color_hex=_highlight_color(),
            )
            return

        if action == "escalate":
            model = str(parsed.get("model") or "")
            friendly = humanise_model(model) if model else "stronger model"
            self._activate("gemini")
            self._activate("escalate")
            self._set_status("escalate", f"→ {friendly}")
            self._activate_edge("gemini", "escalate")
            self._launch_particle(
                "gemini", "escalate", color_hex=_highlight_color(),
            )
            return

        if action == "writing":
            self._activate("results")
            self._activate("summary")
            self._activate_edge("results", "summary")
            self._set_status("results", "writing…")
            self._set_status("summary", "writing…")

    @pyqtSlot(dict)
    def on_stats(self, stats: dict[str, Any]) -> None:
        """Update per-node counters and fire cache-hit particles.

        The eval-agent only emits aggregate ``[STATS]`` lines (no
        per-evaluator ``[STEP] Judging`` lines), so this is where the
        diagram learns that judging is happening at all. When
        ``judged > 0`` for the first time we activate ALL FOUR
        evaluators + Gemini simultaneously and mark Inputs + Rubric as
        done — they're prerequisites that have necessarily finished by
        the time anything was judged.
        """
        total = int(stats.get("total") or 0)
        judged = int(stats.get("judged") or 0)
        cache_hits = int(stats.get("cache_hits") or 0)

        self._last_stats = dict(stats)
        self._last_total = total

        # Aggregate status lines.
        self._set_status("cache", f"{cache_hits} reused")
        if cache_hits > 0:
            self._touched_nodes.add("cache")
        if total > 0:
            self._set_status("results", f"{judged} / {total}")

        # First judged tick: activate all four evaluators + gemini.
        # Inputs/rubric have implicitly finished, so mark them done.
        if judged > 0 and self._last_judged == 0:
            for key in ("inputs", "rubric"):
                node = self._nodes.get(key)
                if node is not None:
                    node.set_done()
                    self._touched_nodes.add(key)
            self._activate_edge("inputs", "rubric", final=True)
            for ev_key in _EVALUATOR_KEYS_ORDERED:
                self._activate(ev_key)
                self._activate_edge("rubric", ev_key)
                self._activate_edge(ev_key, "gemini")
                if not self._per_eval_counts.get(ev_key):
                    # Synthetic status so the user sees activity.
                    self._set_status(ev_key, "judging…")
            self._activate("gemini")

        # Round-robin advances on each fresh judged tick so cache-hit
        # particles visit every evaluator over time.
        delta_cache = cache_hits - self._last_cache_hits
        delta_judged = judged - self._last_judged
        self._last_cache_hits = cache_hits
        self._last_judged = judged

        if delta_cache > 0:
            # Fan-out: one cache-hit particle to each evaluator that's
            # been touched (or all four if none yet). Keeps things
            # snappy while making the loop-back visible.
            self._activate("cache")
            targets = [
                k for k in _EVALUATOR_KEYS_ORDERED
                if k in self._touched_nodes
            ] or list(_EVALUATOR_KEYS_ORDERED)
            for target in targets:
                self._launch_particle(
                    "cache", target, color_hex=_success_color(),
                )

        if delta_judged > 0 and (delta_judged - delta_cache) > 0:
            # Fresh judging → gemini writes to cache + results.
            self._activate_edge("gemini", "cache")
            self._activate_edge("gemini", "results")
            self._launch_particle(
                "gemini", "cache", color_hex=_highlight_color(),
            )
            self._launch_particle(
                "gemini", "results", color_hex=_highlight_color(),
            )
            # Advance round-robin selection of "currently judging" evaluator.
            self._active_evaluator_index = (
                self._active_evaluator_index + 1
            ) % len(_EVALUATOR_KEYS_ORDERED)
            self._current_active_evaluator = _EVALUATOR_KEYS_ORDERED[
                self._active_evaluator_index
            ]
            self._activate("results")

    @pyqtSlot(str)
    def on_log_line(self, line: str) -> None:
        """Defensive parse of raw log lines (e.g. ``[STEP]`` markers)."""
        if not line:
            return
        stripped = line.strip()
        if stripped.startswith("[STEP]"):
            stripped = stripped[len("[STEP]"):].strip()
        if (
            not stripped
            or stripped.startswith("[STATS]")
            or stripped.startswith("[PROGRESS]")
        ):
            return
        if _parse_substep_line(stripped) is not None:
            self.on_substep(stripped)

    @pyqtSlot(str)
    def on_error(self, msg: str) -> None:
        """Flash all currently-active nodes red, then dim them."""
        del msg
        flashed = False
        for node in self._nodes.values():
            if node.state() == _AgentNode.STATE_ACTIVE:
                node.set_error()
                flashed = True
        if not flashed and self._current_active_evaluator is not None:
            node = self._nodes.get(self._current_active_evaluator)
            if node is not None:
                node.set_error()
        # All currently-active edges flip to error too.
        for edge in self._edges:
            if edge.state() == _AgentEdge.STATE_ACTIVE:
                edge.set_error()

    @pyqtSlot()
    def on_finished(self) -> None:
        """Transition every pipeline node to 'done'.

        The eval-agent doesn't emit per-evaluator step lines, so naively
        only marking touched nodes leaves the four evaluators + Cache
        stuck on "idle" forever. A successful run finishes with every
        pipeline node done.
        """
        for key in _PIPELINE_KEYS:
            node = self._nodes.get(key)
            if node is None:
                continue
            if node.state() == _AgentNode.STATE_ERROR:
                continue
            node.set_done()
            self._done_after_finished.add(key)

        # Set a per-evaluator status if no explicit per-eval count is
        # available; falls back to "checked" so users see *something*.
        for ev_key in _EVALUATOR_KEYS_ORDERED:
            if ev_key in self._per_eval_counts:
                current, total = self._per_eval_counts[ev_key]
                self._set_status(ev_key, f"{current} / {total} checked")
            else:
                self._set_status(ev_key, "checked")

        # Cache + gemini friendly status when nothing more specific
        # has been set.
        cache_node = self._nodes.get("cache")
        if cache_node is not None and not self._last_stats:
            cache_node.set_status("verdict store")
        gemini_node = self._nodes.get("gemini")
        if gemini_node is not None:
            gemini_node.set_status("done")

        # Every edge connecting two done nodes also flips to done.
        for edge in self._edges:
            from_key = edge._from_node._key
            to_key = edge._to_node._key
            if (
                from_key in self._done_after_finished
                and to_key in self._done_after_finished
                and edge.state() != _AgentEdge.STATE_ERROR
            ):
                edge.set_done()

    def apply_summary_counts(self, summary_csv_path: Path) -> None:
        """Set per-evaluator status text from ``summary.csv``.

        The eval-agent's per-run summary CSV has rows keyed by
        evaluator id. We sum the ``total`` or ``candidates`` column per
        evaluator and write ``"<N> checked"`` onto the evaluator node.
        Silently no-ops on any parse error — this is a polish path,
        not a critical one.
        """
        try:
            path = Path(summary_csv_path)
            if not path.exists():
                return
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                count_field: str | None = None
                if reader.fieldnames:
                    for candidate in ("total", "candidates", "count", "n"):
                        if candidate in reader.fieldnames:
                            count_field = candidate
                            break
                if count_field is None:
                    return
                totals: dict[str, int] = {}
                for row in reader:
                    ev_id = (row.get("evaluator") or "").strip().lower()
                    if not ev_id or ev_id not in self._nodes:
                        # Accept friendly names too.
                        ev_id = _FRIENDLY_TO_KEY.get(ev_id, ev_id)
                    if not ev_id or ev_id not in _EVALUATOR_KEYS:
                        continue
                    raw = row.get(count_field) or "0"
                    try:
                        totals[ev_id] = totals.get(ev_id, 0) + int(float(raw))
                    except (TypeError, ValueError):
                        continue
                for ev_id, n in totals.items():
                    self._set_status(ev_id, f"{n} checked")
        except (OSError, csv.Error, ValueError):
            # Defensive: silently no-op on any parse error.
            return

    # ── Scene construction ──────────────────────────────────────────

    def _build_scene(self) -> None:
        max_x = max(x for x, _ in _LAYOUT.values()) + _NODE_W + 60
        max_y = max(y for _, y in _LAYOUT.values()) + _NODE_H + 60
        self._scene.setSceneRect(QRectF(0, 0, max_x, max_y))

        # "Evaluators" group rect — a faint translucent rounded box
        # behind the four evaluator nodes, with a small caption.
        self._add_evaluator_group_panel()

        for key, (x, y) in _LAYOUT.items():
            title, status = _FRIENDLY_NODES.get(key, (key, ""))
            node = _AgentNode(key, title, status, float(x), float(y))
            self._scene.addItem(node)
            self._nodes[key] = node

        for from_key, to_key, dotted in _EDGES:
            a = self._nodes.get(from_key)
            b = self._nodes.get(to_key)
            if a is None or b is None:
                continue
            edge = _AgentEdge(a, b, dotted=dotted)
            self._scene.addItem(edge)
            self._edges.append(edge)
            self._edges_by_pair[(from_key, to_key)] = edge

        self._view.fitInView(
            self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio,
        )

    def _add_evaluator_group_panel(self) -> None:
        """Add a translucent rounded rect framing the 4 evaluator nodes."""
        ev_positions = [_LAYOUT[k] for k in _EVALUATOR_KEYS_ORDERED]
        min_x = min(x for x, _ in ev_positions) - 20
        min_y = min(y for _, y in ev_positions) - 26
        max_x = max(x for x, _ in ev_positions) + _NODE_W + 20
        max_y = max(y for _, y in ev_positions) + _NODE_H + 20
        rect = QRectF(min_x, min_y, max_x - min_x, max_y - min_y)
        panel = QGraphicsRectItem(rect)
        bg = QColor(theme.ui("text"))
        bg.setAlphaF(0.04)
        border = QColor(theme.ui("border"))
        border.setAlphaF(0.45)
        panel.setBrush(QBrush(bg))
        pen = QPen(border, 1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        panel.setPen(pen)
        panel.setZValue(0.0)
        # Manually round via clipping isn't trivial on a plain rect
        # item; the dashed-rect read is enough to suggest a group.
        self._scene.addItem(panel)

        caption = QGraphicsSimpleTextItem("Evaluators")
        caption_font = QFont()
        caption_font.setPixelSize(theme.FONT_XS)
        caption_font.setWeight(QFont.Weight.DemiBold)
        caption.setFont(caption_font)
        caption.setBrush(QBrush(QColor(theme.ui("subtext"))))
        caption.setPos(min_x + 10, min_y + 6)
        caption.setZValue(0.5)
        self._scene.addItem(caption)

    # ── Event hooks ────────────────────────────────────────────────

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 — Qt API
        super().resizeEvent(event)
        self._view.fitInView(
            self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio,
        )

    def showEvent(self, event: Any) -> None:  # noqa: N802 — Qt API
        super().showEvent(event)
        self._view.fitInView(
            self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio,
        )
        # Belt-and-suspenders refit on next tick once the layout has
        # had a chance to settle.
        QTimer.singleShot(0, self._refit_view)

    def _refit_view(self) -> None:
        self._view.fitInView(
            self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio,
        )

    # ── Internal helpers ───────────────────────────────────────────

    def _activate(self, key: str) -> None:
        node = self._nodes.get(key)
        if node is None:
            return
        node.set_active()
        self._touched_nodes.add(key)

    def _activate_edge(
        self, from_key: str, to_key: str, *, final: bool = False,
    ) -> None:
        edge = self._edges_by_pair.get((from_key, to_key))
        if edge is None:
            return
        if final:
            edge.set_done()
        else:
            edge.set_active()

    def _set_status(self, key: str, text: str) -> None:
        node = self._nodes.get(key)
        if node is None:
            return
        node.set_status(text)

    def _launch_particle(
        self,
        from_key: str,
        to_key: str,
        *,
        color_hex: str,
    ) -> None:
        """Fire a particle along the edge between two nodes."""
        if len(self._particles) > 20:
            return
        a = self._nodes.get(from_key)
        b = self._nodes.get(to_key)
        if a is None or b is None:
            return

        is_loopback = (from_key == "cache" and to_key in _EVALUATOR_KEYS)
        if is_loopback:
            start = a.mapToScene(QPointF(_NODE_W / 2.0, 0))
            end = b.mapToScene(QPointF(_NODE_W / 2.0, _NODE_H))
        else:
            if a.scenePos().x() <= b.scenePos().x():
                start = a.anchor_right()
                end = b.anchor_left()
            else:
                start = a.anchor_left()
                end = b.anchor_right()

        path = _build_bezier(start, end)
        particle = _Particle(path, color_hex)
        self._scene.addItem(particle)
        self._particles.append(particle)

        anim = QPropertyAnimation(particle, b"pos_t")
        anim.setDuration(800)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.finished.connect(
            lambda p=particle, a=anim: self._on_particle_done(p, a),
        )
        anim.start()
        self._particle_anims.append(anim)

    def _on_particle_done(
        self,
        particle: _Particle,
        anim: QPropertyAnimation,
    ) -> None:
        try:
            self._scene.removeItem(particle)
        except RuntimeError:
            pass
        try:
            particle.deleteLater()
        except RuntimeError:
            pass
        try:
            self._particle_anims.remove(anim)
        except ValueError:
            pass
        try:
            self._particles.remove(particle)
        except ValueError:
            pass
        try:
            anim.deleteLater()
        except RuntimeError:
            pass


# Silence the unused-import lint for QObject (kept for forward
# compatibility with API consumers that subclass).
del QObject
