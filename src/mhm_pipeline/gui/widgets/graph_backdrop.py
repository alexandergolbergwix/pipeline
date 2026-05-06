"""Graph-theory backdrop — the wallpaper behind every panel.

Paints a deterministic node-and-edge field whose visual vocabulary matches
the subject matter of the pipeline (manuscript knowledge graphs). The
pattern is deliberately subtle — low contrast, no movement — so it serves
as *content beneath the glass chrome* rather than as decoration.

Why this exists: Liquid Glass surfaces (see ``glass_panel.py``) rely on
**contrast with their backdrop** to look glassy. A flat solid colour gives
the frosted/specular layers nothing to lens, so the glass effect reads as
merely "a lighter rectangle". Painting a subtle graph pattern behind
everything gives the specular highlights and rim catches something to
react to — and reinforces the app's purpose visually.

Generation:
  * Poisson-disc-like scatter using a seeded RNG (so the pattern is stable
    across runs but still irregular enough to read as organic).
  * Each node is connected to every node within a fixed radius ``R_LINK``,
    producing the small-world structure characteristic of knowledge graphs.
  * A second smaller RNG stream marks ~10 % of nodes as "hubs" (slightly
    larger radius, higher alpha) — mimics authority records.

Cross-platform: pure Qt, no native calls.
"""

from __future__ import annotations

import math
import random
from typing import cast

from PyQt6.QtCore import QLineF, QPointF, QRect, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QWidget

# ── Generation parameters (tuned by visual calibration) ────────────────────
_SEED: int = 424242                  # deterministic pattern across launches
_GRID_STEP: int = 92                 # jitter grid cell size (px)
_JITTER: float = 0.45                # 0 = regular grid; 1 = fully random
_R_LINK: int = 140                   # link radius (px) — small-world density
_HUB_PROB: float = 0.08              # ~8 % of nodes are hubs
_NODE_R: float = 1.6                 # base node radius
_HUB_R: float = 3.0                  # hub node radius
_MAX_EDGES_PER_NODE: int = 4         # cap to avoid dense spaghetti


class GraphBackdrop(QWidget):
    """Subtle graph-theory wallpaper for the application backdrop.

    Place as the bottom widget in any layout (or use as a ``QMainWindow``
    central-widget background). Child widgets painted on top will read it
    through their translucent surfaces.

    Args:
        parent: Parent widget.
        base_color: Solid backdrop fill behind the pattern. Defaults to a
            calm warm-dark charcoal; pass a light tone for light mode.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        base_dark: QColor | None = None,
        base_light: QColor | None = None,
    ) -> None:
        super().__init__(parent)
        self._base_dark = base_dark or QColor(14, 16, 22)
        self._base_light = base_light or QColor(246, 246, 249)
        self._nodes: list[tuple[float, float, bool]] = []    # (x, y, is_hub)
        self._edges: list[tuple[int, int]] = []              # (i, j) into _nodes
        self._last_size: tuple[int, int] = (0, 0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        # Do NOT set WA_TransparentForMouseEvents — per Qt docs it disables
        # mouse events for the widget *and all its children*, which would
        # break every clickable control layered on top. The backdrop simply
        # paints behind its children; mouse dispatch to children works
        # normally without this attribute.

    # ── Pattern generation ────────────────────────────────────────────────

    def _regenerate(self, w: int, h: int) -> None:
        """Generate a jittered grid of nodes + nearest-neighbour edges."""
        if w == 0 or h == 0:
            return
        rng = random.Random(_SEED)
        nodes: list[tuple[float, float, bool]] = []
        for gy in range(-1, h // _GRID_STEP + 2):
            for gx in range(-1, w // _GRID_STEP + 2):
                jx = (rng.random() - 0.5) * 2 * _JITTER
                jy = (rng.random() - 0.5) * 2 * _JITTER
                x = (gx + 0.5 + jx) * _GRID_STEP
                y = (gy + 0.5 + jy) * _GRID_STEP
                is_hub = rng.random() < _HUB_PROB
                nodes.append((x, y, is_hub))
        self._nodes = nodes

        # Edge construction: each node links to up to _MAX_EDGES_PER_NODE
        # neighbours within _R_LINK. Symmetric: insert each undirected edge once.
        r2 = _R_LINK * _R_LINK
        edges: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        # Cell hash for O(n) neighbour lookup
        cell: dict[tuple[int, int], list[int]] = {}
        for i, (x, y, _hub) in enumerate(nodes):
            cx, cy = int(x // _R_LINK), int(y // _R_LINK)
            cell.setdefault((cx, cy), []).append(i)

        for i, (x, y, _hub) in enumerate(nodes):
            cx, cy = int(x // _R_LINK), int(y // _R_LINK)
            candidates: list[int] = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    candidates.extend(cell.get((cx + dx, cy + dy), []))
            # Distance + cap
            dists: list[tuple[float, int]] = []
            for j in candidates:
                if j == i:
                    continue
                jx, jy, _ = nodes[j]
                d2 = (jx - x) ** 2 + (jy - y) ** 2
                if d2 <= r2:
                    dists.append((d2, j))
            dists.sort()
            for _d2, j in dists[:_MAX_EDGES_PER_NODE]:
                key = (i, j) if i < j else (j, i)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(key)
        self._edges = edges

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        w = self.width()
        h = self.height()
        if (w, h) != self._last_size:
            self._regenerate(w, h)
            self._last_size = (w, h)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Theme — lazy import so widgets don't pay the import cost at startup
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        dark = theme.is_dark()
        base = self._base_dark if dark else self._base_light

        # ── Layer 1: ambient radial gradient backdrop ────────────────────
        # Slight brightness toward the upper-left simulates the off-canvas
        # light source shared with GlassPanel's specular bloom.
        ambient = QLinearGradient(0, 0, w * 0.6, h * 1.1)
        if dark:
            ambient.setColorAt(0.0, base.lighter(118))
            ambient.setColorAt(1.0, base.darker(112))
        else:
            ambient.setColorAt(0.0, base.lighter(102))
            ambient.setColorAt(1.0, base.darker(103))
        p.fillRect(QRect(0, 0, w, h), QBrush(ambient))

        # ── Layer 2: edges (thin, very low alpha) ────────────────────────
        if dark:
            edge_col = QColor(255, 255, 255, 14)
            node_col = QColor(255, 255, 255, 52)
            hub_col = QColor(180, 200, 255, 90)      # cool highlight on hubs
        else:
            edge_col = QColor(0, 0, 0, 22)
            node_col = QColor(0, 0, 0, 78)
            hub_col = QColor(60, 90, 180, 120)

        edge_pen = QPen(edge_col, 0.6)
        edge_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(edge_pen)
        for i, j in self._edges:
            x1, y1, _ = self._nodes[i]
            x2, y2, _ = self._nodes[j]
            p.drawLine(QLineF(x1, y1, x2, y2))

        # ── Layer 3: nodes (tiny discs, hubs slightly larger + coloured) ─
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, is_hub in self._nodes:
            if is_hub:
                p.setBrush(QBrush(hub_col))
                p.drawEllipse(QPointF(x, y), _HUB_R, _HUB_R)
            else:
                p.setBrush(QBrush(node_col))
                p.drawEllipse(QPointF(x, y), _NODE_R, _NODE_R)

        # ── Layer 4: soft vignette at edges ──────────────────────────────
        # Pulls focus toward the centre, where content sits.
        vignette = QLinearGradient(0, 0, 0, h)
        if dark:
            vignette.setColorAt(0.0, QColor(0, 0, 0, 36))
            vignette.setColorAt(0.5, QColor(0, 0, 0, 0))
            vignette.setColorAt(1.0, QColor(0, 0, 0, 52))
        else:
            vignette.setColorAt(0.0, QColor(0, 0, 0, 10))
            vignette.setColorAt(0.5, QColor(0, 0, 0, 0))
            vignette.setColorAt(1.0, QColor(0, 0, 0, 14))
        p.fillRect(QRect(0, 0, w, h), QBrush(vignette))

        p.end()

    # ── Utility: theme change refresh ─────────────────────────────────────

    def refresh(self) -> None:
        """Call after a theme toggle to repaint immediately."""
        self._last_size = (0, 0)
        self.update()
