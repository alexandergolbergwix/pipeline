"""Live animated system-design diagram for the AI verification dialog.

Renders the eval-agent's run as a system-architecture diagram in motion:
six logical nodes (one expanding into four sub-evaluators), edges
between them, animated particles flowing along edges, nodes pulsing
when active. Drives off the live ``substep`` / ``stats_update`` /
``log_line`` / ``error`` / ``finished`` signals from
``EvalAgentWorker``.

Agent work is not linear — multiple evaluators judge in parallel, the
cache short-circuits some candidates, errors retry, and the Gemini
judge feeds back into the cache. A linear progress bar misrepresents
that; a system-design diagram in motion shows the actual data flow.
"""

from __future__ import annotations

import re
from typing import Any

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
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
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme

# ── Layout (scene coordinates) ──────────────────────────────────────

_NODE_W: int = 160
_NODE_H: int = 60

_LAYOUT: dict[str, tuple[int, int]] = {
    "inputs":            (40, 200),
    "rubric":            (240, 200),
    "person_ner":        (480, 80),
    "provenance_ner":    (480, 160),
    "contents_ner":      (480, 240),
    "genre_classifier":  (480, 320),
    "gemini":            (720, 200),
    "cache":             (720, 360),
    "results":           (920, 200),
    "summary":           (920, 360),
}

_EVALUATOR_KEYS: set[str] = {
    "person_ner", "provenance_ner", "contents_ner", "genre_classifier",
}

_FRIENDLY_NODES: dict[str, tuple[str, str]] = {
    "inputs":            ("Inputs",      "marc + ner_results"),
    "rubric":            ("Rubrics",     "evaluator prompts"),
    "person_ner":        ("Person AI",   "idle"),
    "provenance_ner":    ("Owner AI",    "idle"),
    "contents_ner":      ("Contents AI", "idle"),
    "genre_classifier": ("Genre AI",    "idle"),
    "gemini":            ("Gemini",      "judge model"),
    "cache":             ("Cache",       "verdict store"),
    "results":           ("Results",     "results.jsonl"),
    "summary":           ("Summary",     "report.md"),
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
    # Dotted cache→evaluators loop-back: activates only on cache hit.
    ("cache",            "person_ner",        True),
    ("cache",            "provenance_ner",    True),
    ("cache",            "contents_ner",      True),
    ("cache",            "genre_classifier",  True),
]

# Parsing rules — accept both raw "Judging <id> N/M" and the friendly
# rewrites produced by ``friendly_copy.humanise_log_line``.
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


def _ui_or_fallback(key: str, fallback: str) -> str:
    """Read a colour from ``theme.ui()`` with a graceful fallback."""
    value = theme.ui(key)
    # ``theme.ui`` returns ``"#888888"`` for unknown keys.
    if value == "#888888" and key not in {"subtext", "border"}:
        return fallback
    return value


def _connector_color() -> str:
    return _ui_or_fallback("connector", theme.ui("border"))


def _success_color() -> str:
    return theme.SEMANTIC_SUCCESS_DARK if theme.is_dark() else theme.SEMANTIC_SUCCESS


def _error_color() -> str:
    return theme.SEMANTIC_ERROR_DARK if theme.is_dark() else theme.SEMANTIC_ERROR


def _highlight_color() -> str:
    return theme.ui("highlight")


# ── Particle ───────────────────────────────────────────────────────


class _Particle(QGraphicsObject):
    """A small circle that travels along a bezier path from 0.0 → 1.0.

    Animates ``pos_t`` via ``QPropertyAnimation``; on each tick we
    re-position ourselves to ``path.pointAtPercent(pos_t)``.
    """

    def __init__(
        self,
        path: QPainterPath,
        color_hex: str,
        *,
        radius: float = 4.0,
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
        r = self._radius + 1.0
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(
        self,
        painter: QPainter,
        _option: Any,
        _widget: QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
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
        self._opacity_value = 0.6
        self._glow_radius = 0.0
        self._pulse_group: QSequentialAnimationGroup | None = None
        self._error_flash: QSequentialAnimationGroup | None = None
        self.setOpacity(self._opacity_value)
        self.setZValue(10.0)

    # ── Geometry ────────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:  # noqa: N802 — Qt API
        # Include outer-glow margin so we don't get paint clipping.
        return QRectF(-8, -8, _NODE_W + 16, _NODE_H + 16)

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

        border_color, border_width = self._border_for_state()
        fill_color = QColor(theme.ui("panel_bg"))
        text_color = QColor(theme.ui("text"))
        sub_color = QColor(theme.ui("subtext"))

        # Inner glow when active (a soft outer halo).
        if self._state == self.STATE_ACTIVE and self._glow_radius > 0.0:
            glow = QColor(_highlight_color())
            glow.setAlpha(70)
            painter.setPen(QPen(glow, self._glow_radius))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                self.card_rect(), theme.RADIUS_LG, theme.RADIUS_LG,
            )

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
                   _NODE_W - 2 * theme.SPACE_MD, theme.FONT_LG + 4),
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
            badge_font.setPixelSize(theme.FONT_BASE)
            badge_font.setWeight(QFont.Weight.Bold)
            painter.setFont(badge_font)
            painter.setPen(badge_color)
            painter.drawText(
                QRectF(_NODE_W - theme.SPACE_LG - 6, 2,
                       theme.SPACE_LG + 4, theme.FONT_LG + 4),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
                glyph,
            )

    def _border_for_state(self) -> tuple[QColor, int]:
        if self._state == self.STATE_ACTIVE:
            return QColor(_highlight_color()), 2
        if self._state == self.STATE_DONE:
            return QColor(_success_color()), 2
        if self._state == self.STATE_ERROR:
            return QColor(_error_color()), 2
        return QColor(theme.ui("border")), 1

    def _state_glyph(self) -> str:
        if self._state == self.STATE_ACTIVE:
            return "⚡"  # ⚡
        if self._state == self.STATE_DONE:
            return "✓"  # ✓
        if self._state == self.STATE_ERROR:
            return "✗"  # ✗
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

    def _get_glow(self) -> float:
        return self._glow_radius

    def _set_glow(self, value: float) -> None:
        self._glow_radius = float(value)
        self.update()

    glow = pyqtProperty(float, fget=_get_glow, fset=_set_glow)

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
        self._state = self.STATE_IDLE
        self._set_opacity_val(0.6)
        self._set_glow(0.0)
        self.update()

    def set_active(self) -> None:
        if self._state == self.STATE_ACTIVE:
            return
        self._stop_animations()
        self._state = self.STATE_ACTIVE
        self._set_opacity_val(1.0)
        self._start_pulse()
        self.update()

    def set_done(self) -> None:
        self._stop_animations()
        self._state = self.STATE_DONE
        self._set_opacity_val(1.0)
        self._set_glow(0.0)
        self.update()

    def set_error(self) -> None:
        self._stop_animations()
        self._state = self.STATE_ERROR
        self._set_opacity_val(1.0)
        self._start_error_flash()
        self.update()

    # ── Animation plumbing ──────────────────────────────────────────

    def _start_pulse(self) -> None:
        # Ping-pong glow 4 ↔ 12 px on a 1200 ms loop.
        group = QSequentialAnimationGroup(self)
        up = QPropertyAnimation(self, b"glow")
        up.setDuration(600)
        up.setStartValue(4.0)
        up.setEndValue(12.0)
        up.setEasingCurve(QEasingCurve.Type.InOutQuad)
        down = QPropertyAnimation(self, b"glow")
        down.setDuration(600)
        down.setStartValue(12.0)
        down.setEndValue(4.0)
        down.setEasingCurve(QEasingCurve.Type.InOutQuad)
        group.addAnimation(up)
        group.addAnimation(down)
        group.setLoopCount(-1)
        group.start()
        self._pulse_group = group

    def _start_error_flash(self) -> None:
        # 3-pulse opacity flash 1.0 ↔ 0.4.
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


class _AgentEdge(QGraphicsPathItem):
    """A static bezier edge between two node anchors. Solid or dotted."""

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
        # The loop-back cache→evaluator: anchor on the bottom of the
        # cache card and the bottom of the evaluator card for legibility.
        if self._dotted:
            a = self._from_node.mapToScene(QPointF(_NODE_W / 2.0, 0))
            b = self._to_node.mapToScene(QPointF(_NODE_W / 2.0, _NODE_H))
        self.setPath(_build_bezier(a, b))

    def _apply_pen(self) -> None:
        color = QColor(_connector_color())
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if self._dotted:
            pen.setStyle(Qt.PenStyle.DotLine)
            color.setAlpha(120)
            pen.setColor(color)
        self.setPen(pen)


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
        self._particles: list[_Particle] = []
        self._particle_anims: list[QPropertyAnimation] = []
        self._touched_nodes: set[str] = set()

        # Track stats deltas so we know when a cache hit just happened.
        self._last_cache_hits: int = 0
        self._last_judged: int = 0
        self._last_total: int = 0
        self._current_active_evaluator: str | None = None
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

        self._touched_nodes.clear()
        self._last_cache_hits = 0
        self._last_judged = 0
        self._last_total = 0
        self._current_active_evaluator = None
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
            self._launch_particle(
                evaluator_id, "gemini", color_hex=_highlight_color(),
            )
            self._activate("gemini")
            return

        if action == "writing":
            self._activate("results")
            self._activate("summary")
            self._set_status("results", "writing…")
            self._set_status("summary", "writing…")

    @pyqtSlot(dict)
    def on_stats(self, stats: dict[str, Any]) -> None:
        """Update per-node counters and fire cache-hit particles."""
        total = int(stats.get("total") or 0)
        judged = int(stats.get("judged") or 0)
        cache_hits = int(stats.get("cache_hits") or 0)

        self._last_total = total
        # Update aggregate cache status line.
        self._set_status("cache", f"{cache_hits} reused")
        if cache_hits > 0:
            self._touched_nodes.add("cache")

        # Per-run aggregate counters on results / summary.
        if total > 0:
            self._set_status("results", f"{judged} / {total}")

        delta_cache = cache_hits - self._last_cache_hits
        delta_judged = judged - self._last_judged
        self._last_cache_hits = cache_hits
        self._last_judged = judged

        # A cache-hit particle loops from the cache back into the
        # currently-active evaluator. If no evaluator is active right
        # now, fall back to any evaluator that's already been touched
        # so the loop-back is still visible.
        if delta_cache > 0:
            target = self._current_active_evaluator or self._first_touched_evaluator()
            if target is not None:
                self._activate("cache")
                self._launch_particle(
                    "cache", target, color_hex=_success_color(),
                )

        # A fresh judging delta also lights up gemini → cache (the
        # judge feeds the cache) so the user sees the write-back flow.
        if delta_judged > 0 and (delta_judged - delta_cache) > 0:
            self._launch_particle(
                "gemini", "cache", color_hex=_highlight_color(),
            )

    @pyqtSlot(str)
    def on_log_line(self, line: str) -> None:
        """Defensive parse of raw log lines (e.g. ``[STEP]`` markers)."""
        if not line:
            return
        stripped = line.strip()
        # Strip a leading "[STEP] " or similar marker.
        if stripped.startswith("[STEP]"):
            stripped = stripped[len("[STEP]"):].strip()
        # [STATS] / [PROGRESS] lines are routed via on_stats / dialog;
        # we ignore them here unless they happen to embed a substep.
        if not stripped or stripped.startswith("[STATS]") or stripped.startswith("[PROGRESS]"):
            return
        if _parse_substep_line(stripped) is not None:
            self.on_substep(stripped)

    @pyqtSlot(str)
    def on_error(self, msg: str) -> None:
        """Flash all currently-active nodes red, then dim them."""
        # ``msg`` is logged by the dialog already; we only need to
        # react visually.
        del msg
        flashed = False
        for node in self._nodes.values():
            if node.state() == _AgentNode.STATE_ACTIVE:
                node.set_error()
                flashed = True
        # If nothing was active when the error fired, mark the most
        # recently touched evaluator (if any) as the error site so the
        # user sees where it broke.
        if not flashed and self._current_active_evaluator is not None:
            node = self._nodes.get(self._current_active_evaluator)
            if node is not None:
                node.set_error()

    @pyqtSlot()
    def on_finished(self) -> None:
        """Transition every touched node to 'done'."""
        for key in self._touched_nodes:
            node = self._nodes.get(key)
            if node is None:
                continue
            if node.state() == _AgentNode.STATE_ERROR:
                continue
            node.set_done()
        # Inputs/rubric/results/summary always count as done at the
        # end of a successful run (they may not have emitted a substep).
        for key in ("inputs", "rubric", "results", "summary"):
            node = self._nodes.get(key)
            if node is not None and node.state() != _AgentNode.STATE_ERROR:
                node.set_done()

    # ── Scene construction ──────────────────────────────────────────

    def _build_scene(self) -> None:
        # Generous scene rect so the view fits with margins.
        max_x = max(x for x, _ in _LAYOUT.values()) + _NODE_W + 60
        max_y = max(y for _, y in _LAYOUT.values()) + _NODE_H + 60
        self._scene.setSceneRect(QRectF(0, 0, max_x, max_y))

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

        # Fit-to-view on first show. The QGraphicsView will be resized
        # by the parent layout; the view fits the scene into whatever
        # rect it gets.
        self._view.fitInView(
            self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio,
        )

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

    # ── Internal helpers ───────────────────────────────────────────

    def _activate(self, key: str) -> None:
        node = self._nodes.get(key)
        if node is None:
            return
        node.set_active()
        self._touched_nodes.add(key)

    def _set_status(self, key: str, text: str) -> None:
        node = self._nodes.get(key)
        if node is None:
            return
        node.set_status(text)

    def _first_touched_evaluator(self) -> str | None:
        for key in self._touched_nodes:
            if key in _EVALUATOR_KEYS:
                return key
        return None

    def _launch_particle(
        self,
        from_key: str,
        to_key: str,
        *,
        color_hex: str,
    ) -> None:
        """Fire a particle along the (first matching) edge between two nodes."""
        # Cap in-flight particles to keep the scene snappy.
        if len(self._particles) > 20:
            return
        a = self._nodes.get(from_key)
        b = self._nodes.get(to_key)
        if a is None or b is None:
            return

        # Pick anchors based on relative layout (right→left for
        # forward edges; bottom→bottom for the cache loop-back).
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
