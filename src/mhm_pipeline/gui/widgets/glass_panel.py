"""Liquid Glass surface — a physically-inspired painted QWidget.

Apple's Liquid Glass combines four optical phenomena:

1. **Frost (translucent base)** — backdrop seen through a Gaussian-blurred
   layer tinted with ~20% white. In Qt we can't apply a real backdrop blur,
   so we simulate the apparent whiteness with a multi-stop linear gradient
   whose alpha profile follows the Gaussian-like shape of the glass body.

2. **Fresnel rim (edge catch)** — at grazing angles, reflectance
   ``R(θ) = R₀ + (1-R₀)(1-cos θ)⁵`` approaches 1. For a flat widget this
   manifests as a brighter **top** border (where the virtual light grazes
   the curved edge) and a softer bottom border. We paint a 1-px rounded
   rectangle with a vertical-alpha pen gradient.

3. **Specular highlight (light-source reflection)** — a radial bloom whose
   centre sits along the light direction. The kube.io reference
   implementation parameterises this as an angle (``-60°`` in their demo)
   and a peak alpha of 0.20–0.50. We place the bloom centre at
   ``(0.30 × W, -0.10 × H)`` — slightly above the top edge — with a radius
   of ``0.9 × max(W, H)``, producing a soft upper-right glint.

4. **Body tint (depth)** — real glass has a very slight cool tint below the
   half-way line because frost scatters short wavelengths preferentially.
   We add a tiny blue-grey gradient on the bottom half.

Cross-platform: pure ``QPainter`` calls, works identically on macOS and
Windows without any native-API bridges. No ctypes, no backdrop-filter, no
crashes.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QFrame, QWidget

# ── Material presets (frost · specular · rim intensity triples) ─────────────
# alpha values are on the 0–255 scale. Picked by visual calibration against
# macOS Tahoe's actual Liquid Glass surfaces.

_PRESETS: dict[str, dict[str, Any]] = {
    # Thin chrome — toolbar chips, tooltips, floating pills
    "thin": {
        "base_alpha_top": 48,
        "base_alpha_mid": 16,
        "base_alpha_bot": 22,
        "spec_peak": 70,
        "spec_mid":  28,
        "rim_top":   120,
        "rim_bot":   26,
        "tint_bot":  10,
    },
    # Regular — sidebars, popovers, card surfaces (default)
    "regular": {
        "base_alpha_top": 60,
        "base_alpha_mid": 22,
        "base_alpha_bot": 30,
        "spec_peak": 95,
        "spec_mid":  38,
        "rim_top":   150,
        "rim_bot":   34,
        "tint_bot":  14,
    },
    # Thick — large modal sheets, hero surfaces
    "thick": {
        "base_alpha_top": 78,
        "base_alpha_mid": 32,
        "base_alpha_bot": 42,
        "spec_peak": 120,
        "spec_mid":  52,
        "rim_top":   180,
        "rim_bot":   44,
        "tint_bot":  22,
    },
}


class GlassPanel(QFrame):
    """A rounded-rect container that paints a realistic Liquid Glass surface.

    Drop-in replacement for ``QFrame``. Children are added with the usual
    ``QVBoxLayout``/``QHBoxLayout``; their backgrounds should remain
    transparent so the glass shows through.

    Args:
        parent: Parent widget.
        radius: Corner radius in px. 12–22 looks right for panels.
        variant: One of ``"thin"``, ``"regular"`` (default), ``"thick"``.
        light_from: Tuple ``(x, y)`` in unit coordinates (0–1) positioning
            the virtual light source. Default ``(0.3, -0.1)`` places the
            bloom slightly above the upper-left — Apple's convention.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        radius: int = 16,
        variant: str = "regular",
        light_from: tuple[float, float] = (0.3, -0.1),
    ) -> None:
        super().__init__(parent)
        self._radius = radius
        self._preset = _PRESETS.get(variant, _PRESETS["regular"])
        self._light = light_from
        # Transparent background — our paintEvent handles the fill
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(False)
        self.setFrameShape(QFrame.Shape.NoFrame)

    # ── Public API ────────────────────────────────────────────────────────

    def set_radius(self, radius: int) -> None:
        self._radius = radius
        self.update()

    def set_variant(self, variant: str) -> None:
        self._preset = _PRESETS.get(variant, _PRESETS["regular"])
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Paint the four-layer Liquid Glass composite."""
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        r = QRectF(self.rect())
        radius = float(self._radius)
        preset = self._preset

        # Rounded-rect clip (all layers except the rim are inside the clip)
        path = QPainterPath()
        # Inset by 0.5 so the rim pen strokes cleanly at the widget edge
        inner = r.adjusted(0.5, 0.5, -0.5, -0.5)
        path.addRoundedRect(inner, radius, radius)
        p.save()
        p.setClipPath(path)

        # ── Layer 1: Frost base (vertical alpha profile, Gaussian-like) ──
        base = QLinearGradient(0.0, 0.0, 0.0, r.height())
        base.setColorAt(0.00, QColor(255, 255, 255, preset["base_alpha_top"]))
        base.setColorAt(0.10, QColor(255, 255, 255, int(preset["base_alpha_top"] * 0.72)))
        base.setColorAt(0.35, QColor(255, 255, 255, preset["base_alpha_mid"]))
        base.setColorAt(0.75, QColor(255, 255, 255, int(preset["base_alpha_mid"] * 0.85)))
        base.setColorAt(1.00, QColor(255, 255, 255, preset["base_alpha_bot"]))
        p.fillRect(r, QBrush(base))

        # ── Layer 2: Specular bloom (radial, off-canvas centre) ─────────
        lx = r.width() * self._light[0]
        ly = r.height() * self._light[1]
        spec_radius = max(r.width(), r.height()) * 0.90
        spec = QRadialGradient(QPointF(lx, ly), spec_radius)
        spec.setColorAt(0.00, QColor(255, 255, 255, preset["spec_peak"]))
        spec.setColorAt(0.28, QColor(255, 255, 255, preset["spec_mid"]))
        spec.setColorAt(0.70, QColor(255, 255, 255, 0))
        p.fillRect(r, QBrush(spec))

        # ── Layer 3: Cool body tint on lower half (simulates scatter) ──
        tint = QLinearGradient(0.0, r.height() * 0.45, 0.0, r.height())
        tint.setColorAt(0.0, QColor(120, 140, 200, 0))
        tint.setColorAt(1.0, QColor(120, 140, 200, preset["tint_bot"]))
        p.fillRect(r, QBrush(tint))

        # ── Layer 4: Fresnel rim (painted OUTSIDE the clip) ─────────────
        p.restore()
        rim = QLinearGradient(0.0, 0.0, 0.0, r.height())
        rim.setColorAt(0.00, QColor(255, 255, 255, preset["rim_top"]))
        rim.setColorAt(0.50, QColor(255, 255, 255, int(preset["rim_top"] * 0.45)))
        rim.setColorAt(1.00, QColor(255, 255, 255, preset["rim_bot"]))
        pen = QPen(QBrush(rim), 1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(inner, radius, radius)

        p.end()
