"""Coloured pill widget for AI-verdict statuses.

A small ``QLabel`` subclass that maps a raw verdict status string
(``"full"``, ``"partial"``, ``"fail"``, ``"abstain"``, …) onto a
friendly English label and a theme-coloured background/foreground
pair. Reads every colour through ``theme.ui()`` / ``theme.severity()``
— never a hardcoded hex (Rule 36).

The pill has two render modes:

* **default** — full friendly text inside a pill ("Looks right").
* **glyph_only** — a single ✓ / ✗ / — / ? glyph used in the small
  per-aspect columns (Name / Type / Role) of the verdicts table.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget

from mhm_pipeline.gui import theme

# Mapping from raw verdict status → (friendly_label, glyph, severity_key).
# ``severity_key`` is fed into ``theme.severity()`` which returns
# (bg, accent) tuples consistent with the rest of the app.
_STATUS_TABLE: dict[str, tuple[str, str, str]] = {
    "full":    ("Looks right",   "✓", "success"),
    "yes":     ("Looks right",   "✓", "success"),
    "ok":      ("Looks right",   "✓", "success"),
    "partial": ("Partly right",  "~", "warning"),
    "fail":    ("Got it wrong",  "✗", "violation"),
    "no":      ("Got it wrong",  "✗", "violation"),
    "abstain": ("Couldn't tell", "—", "warning"),
    "unsure":  ("Couldn't tell", "—", "warning"),
    "unknown": ("Couldn't tell", "—", "warning"),
    "n/a":     ("Not checked",   "—", "info"),
    "error":   ("Error",         "!", "violation"),
}


def _resolve_palette(status: str) -> tuple[str, str, str, str]:
    """Return (friendly_label, glyph, bg_color, fg_color) for *status*.

    Falls back to a neutral grey palette when the status is unknown
    rather than raising — verdict outputs are user-controlled data and
    must never crash the dialog.
    """
    key = (status or "").strip().lower()
    label, glyph, sev_key = _STATUS_TABLE.get(key, (status or "?", "?", "info"))

    # ``theme.severity()`` returns a ColorPair(bg, text). The text is
    # the saturated tone used for borders/strong text; bg is the soft
    # pastel fill. We use text as the foreground colour over bg so the
    # pill remains legible in both light + dark mode.
    pair = theme.severity(sev_key)
    return label, glyph, pair.bg, pair.text


class StatusPill(QLabel):
    """Theme-aware pill rendering a verdict status."""

    def __init__(
        self,
        status: str = "",
        *,
        glyph_only: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._glyph_only = bool(glyph_only)
        self._status: str = ""
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setStatus(status)

    # ── Public API ──────────────────────────────────────────────────

    def setStatus(self, status: str) -> None:  # noqa: N802 — Qt-style camelCase
        """Update the pill's appearance to reflect *status*."""
        self._status = status or ""
        label, glyph, bg, fg = _resolve_palette(self._status)

        if self._glyph_only:
            self.setText(glyph)
            self.setToolTip(label)
            pad_v = theme.SPACE_0
            pad_h = theme.SPACE_XS
            min_w = 22
        else:
            self.setText(label)
            self.setToolTip("")
            pad_v = theme.SPACE_XS
            pad_h = theme.SPACE_SM
            min_w = 0

        self.setMinimumWidth(min_w)
        self.setStyleSheet(
            f"QLabel {{"
            f" background: {bg};"
            f" color: {fg};"
            f" border: 1px solid {fg};"
            f" border-radius: {theme.RADIUS_PILL}px;"
            f" padding: {pad_v}px {pad_h}px;"
            f" font-size: {theme.FONT_SM}px;"
            f" font-weight: {theme.WEIGHT_SEMIBOLD};"
            f" }}"
        )

    def status(self) -> str:
        """Return the raw status currently being displayed."""
        return self._status


__all__ = ["StatusPill"]
