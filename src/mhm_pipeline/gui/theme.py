"""Centralised colour theme for the MHM Pipeline GUI.

Every widget imports colours from this module instead of defining its own.
Supports Dark / Light / System (auto-detect) modes via ``SettingsManager``.

Usage::

    from mhm_pipeline.gui import theme

    bg, text = theme.node_color("person")
    style    = theme.button_style()
    dark     = theme.is_dark()
"""

from __future__ import annotations

from typing import NamedTuple

# ── Data types ───────────────────────────────────────────────────────────────


class ColorPair(NamedTuple):
    """A background + text/border colour pair."""

    bg: str
    text: str


# ── Theme state (lazy-initialised) ──────────────────────────────────────────

_dark: bool | None = None


def _resolve_dark() -> bool:
    """Determine whether dark mode is active.

    Priority: user preference in settings → system palette luminance.
    """
    # 1. Check user preference
    try:
        from mhm_pipeline.settings.settings_manager import SettingsManager  # noqa: PLC0415

        pref = SettingsManager().theme_preference
        if pref == "dark":
            return True
        if pref == "light":
            return False
        # "system" → fall through to auto-detect
    except Exception:
        pass

    # 2. Auto-detect from palette
    try:
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

        app = QApplication.instance()
        if app is not None:
            palette = app.palette()
            bg = palette.color(palette.ColorRole.Window)
            luminance = (0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()) / 255
            return luminance < 0.5
    except Exception:
        pass

    return False


def is_dark() -> bool:
    """Return ``True`` if the app is in dark mode (cached)."""
    global _dark  # noqa: PLW0603
    if _dark is None:
        _dark = _resolve_dark()
    return _dark


def is_dark_mode(widget: object = None) -> bool:
    """Backward-compatible alias for ``is_dark()``."""
    return is_dark()


# ── Colour data ──────────────────────────────────────────────────────────────

# Node types (9) — used by triple_graph_view, knowledge_graph_view, graph_store
_NODE_LIGHT: dict[str, ColorPair] = {
    "manuscript":         ColorPair("#dbeafe", "#3b82f6"),
    "person":             ColorPair("#fce7f3", "#ec4899"),
    "work":               ColorPair("#dcfce7", "#22c55e"),
    "expression":         ColorPair("#ccfbf1", "#14b8a6"),
    "place":              ColorPair("#fef3c7", "#eab308"),
    "codicological_unit": ColorPair("#ffedd5", "#f97316"),
    "event":              ColorPair("#ede9fe", "#8b5cf6"),
    "organization":       ColorPair("#e0e7ff", "#6366f1"),
    "default":            ColorPair("#f3f4f6", "#6b7280"),
}
_NODE_DARK: dict[str, ColorPair] = {
    "manuscript":         ColorPair("#1e3a5f", "#60a5fa"),
    "person":             ColorPair("#4a1942", "#f472b6"),
    "work":               ColorPair("#14332d", "#4ade80"),
    "expression":         ColorPair("#134e4a", "#2dd4bf"),
    "place":              ColorPair("#422006", "#facc15"),
    "codicological_unit": ColorPair("#431407", "#fb923c"),
    "event":              ColorPair("#2e1065", "#a78bfa"),
    "organization":       ColorPair("#1e1b4b", "#818cf8"),
    "default":            ColorPair("#374151", "#9ca3af"),
}

# Entity types (5) — used by entity_highlighter
_ENTITY_LIGHT: dict[str, tuple[str, str]] = {
    "PERSON": ("#c7d2fe", "#3730a3"),
    "DATE":   ("#fed7aa", "#9a3412"),
    "PLACE":  ("#bbf7d0", "#166534"),
    "WORK":   ("#fecaca", "#991b1b"),
    "ORG":    ("#e5e7eb", "#374151"),
}
_ENTITY_DARK: dict[str, tuple[str, str]] = {
    "PERSON": ("#312e81", "#c7d2fe"),
    "DATE":   ("#431407", "#fed7aa"),
    "PLACE":  ("#14532d", "#bbf7d0"),
    "WORK":   ("#450a0a", "#fecaca"),
    "ORG":    ("#374151", "#d1d5db"),
}

# Role types (7) — used by entity_highlighter
_ROLE_LIGHT: dict[str, tuple[str, str]] = {
    "AUTHOR":      ("#c7d2fe", "#3730a3"),
    "SCRIBE":      ("#fed7aa", "#9a3412"),
    "TRANSCRIBER": ("#fed7aa", "#9a3412"),
    "OWNER":       ("#bbf7d0", "#166534"),
    "CENSOR":      ("#fecaca", "#991b1b"),
    "TRANSLATOR":  ("#e5e7eb", "#374151"),
    "COMMENTATOR": ("#dbeafe", "#1e40af"),
}
_ROLE_DARK: dict[str, tuple[str, str]] = {
    "AUTHOR":      ("#312e81", "#c7d2fe"),
    "SCRIBE":      ("#431407", "#fed7aa"),
    "TRANSCRIBER": ("#431407", "#fed7aa"),
    "OWNER":       ("#14532d", "#bbf7d0"),
    "CENSOR":      ("#450a0a", "#fecaca"),
    "TRANSLATOR":  ("#374151", "#d1d5db"),
    "COMMENTATOR": ("#1e3a5f", "#93c5fd"),
}

# State colours (4) — used by stage_progress
_STATE_LIGHT = {
    "pending": (180, 180, 180),
    "running": (50, 130, 240),
    "done":    (60, 180, 75),
    "error":   (220, 50, 50),
}
_STATE_DARK = {
    "pending": (120, 120, 120),
    "running": (60, 140, 255),
    "done":    (70, 200, 85),
    "error":   (240, 60, 60),
}

# Confidence background — used by authority_matcher_view
_CONF_LIGHT = {"high": "#dcfce7", "medium": "#fef3c7", "low": "#fee2e2"}
_CONF_DARK = {"high": "#14532d", "medium": "#422006", "low": "#450a0a"}

# Severity — used by validation_result_view
_SEV_LIGHT: dict[str, tuple[str, str]] = {
    "violation": ("#ef4444", "#fee2e2"),
    "warning":   ("#f59e0b", "#fef3c7"),
    "info":      ("#3b82f6", "#dbeafe"),
    "success":   ("#22c55e", "#dcfce7"),
}
_SEV_DARK: dict[str, tuple[str, str]] = {
    "violation": ("#f87171", "#450a0a"),
    "warning":   ("#fbbf24", "#422006"),
    "info":      ("#60a5fa", "#1e3a5f"),
    "success":   ("#4ade80", "#14532d"),
}

# Upload status hex — used by upload_progress_view
_STATUS_LIGHT = {
    "pending": "#888888", "uploading": "#3280F0", "success": "#3CB44B",
    "exists": "#3CB44B", "failed": "#DC3232", "skipped": "#888888",
}
_STATUS_DARK = {
    "pending": "#888888", "uploading": "#60a5fa", "success": "#4ade80",
    "exists": "#4ade80", "failed": "#f87171", "skipped": "#888888",
}

# MARC field colours — used by marc_field_visualizer
_FIELD_LIGHT: dict[str, tuple[str, str]] = {
    "001": ("#f3f4f6", "#374151"), "100": ("#dbeafe", "#1e40af"),
    "245": ("#a5f3fc", "#155e75"), "260": ("#ffedd5", "#9a3412"),
    "300": ("#bbf7d0", "#166534"), "500": ("#fef3c7", "#92400e"),
    "600": ("#e5dbff", "#5b21b6"), "700": ("#fce7f3", "#be185d"),
    "957": ("#fee2e2", "#991b1b"),
}
_FIELD_DARK: dict[str, tuple[str, str]] = {
    "001": ("#374151", "#d1d5db"), "100": ("#1e3a5f", "#93c5fd"),
    "245": ("#164e63", "#67e8f9"), "260": ("#431407", "#fdba74"),
    "300": ("#14532d", "#86efac"), "500": ("#422006", "#fde68a"),
    "600": ("#2e1065", "#c4b5fd"), "700": ("#4a1942", "#f9a8d4"),
    "957": ("#450a0a", "#fca5a5"),
}

# Syntax highlighting — used by ttl_preview
_SYNTAX_LIGHT = {"directive": (30, 80, 200), "uri": (20, 140, 60), "comment": (140, 140, 140)}
_SYNTAX_DARK = {"directive": (120, 160, 255), "uri": (80, 220, 120), "comment": (140, 140, 140)}

# UI chrome — used everywhere
_UI_LIGHT: dict[str, str] = {
    "text": "#1f2937", "subtext": "#6b7280", "border": "#d1d5db",
    "panel_bg": "#f9fafb", "page_bg": "#ffffff",
    "overlay_bg": "rgba(255,255,255,210)",
    "highlight": "#fbbf24", "warning": "#996600",
    "match_found": "#059669", "no_match": "#d97706",
    "button_bg": "#3b82f6", "button_hover": "#2563eb", "button_disabled": "#9ca3af",
    "record_header": "#6b7280", "record_content": "#f9fafb",
    "connector": "#a0a0a0",
}
_UI_DARK: dict[str, str] = {
    "text": "#cdd6f4", "subtext": "#a6adc8", "border": "#585b70",
    "panel_bg": "#313244", "page_bg": "#1e1e2e",
    "overlay_bg": "rgba(30,30,46,210)",
    "highlight": "#fbbf24", "warning": "#fbbf24",
    "match_found": "#34d399", "no_match": "#fbbf24",
    "button_bg": "#6366f1", "button_hover": "#4f46e5", "button_disabled": "#4b5563",
    "record_header": "#4b5563", "record_content": "#1e1e2e",
    "connector": "#6c7086",
}

# ── Public accessors ─────────────────────────────────────────────────────────


def node_color(node_type: str) -> ColorPair:
    """Return (bg, border) for a semantic node type."""
    table = _NODE_DARK if is_dark() else _NODE_LIGHT
    return table.get(node_type, table["default"])


def entity_color(entity_type: str) -> tuple[str, str]:
    """Return (bg, text) for an entity type."""
    table = _ENTITY_DARK if is_dark() else _ENTITY_LIGHT
    return table.get(entity_type, ("#e5e7eb", "#374151"))


def role_color(role: str) -> tuple[str, str]:
    """Return (bg, text) for a role."""
    table = _ROLE_DARK if is_dark() else _ROLE_LIGHT
    return table.get(role, entity_color("ORG"))


def entity_colors() -> dict[str, tuple[str, str]]:
    """Return the full entity colour dict (for pure-function callers)."""
    return dict(_ENTITY_DARK if is_dark() else _ENTITY_LIGHT)


def role_colors() -> dict[str, tuple[str, str]]:
    """Return the full role colour dict (for pure-function callers)."""
    return dict(_ROLE_DARK if is_dark() else _ROLE_LIGHT)


def state_qcolor(state: str) -> "QColor":
    """Return a QColor for a pipeline state."""
    from PyQt6.QtGui import QColor  # noqa: PLC0415

    table = _STATE_DARK if is_dark() else _STATE_LIGHT
    r, g, b = table.get(state, (180, 180, 180))
    return QColor(r, g, b)


def confidence_bg(level: str) -> str:
    """Return hex background for a confidence level."""
    table = _CONF_DARK if is_dark() else _CONF_LIGHT
    return table.get(level, "#f3f4f6")


def severity(sev: str) -> ColorPair:
    """Return (accent, bg) for a severity level."""
    table = _SEV_DARK if is_dark() else _SEV_LIGHT
    accent, bg = table.get(sev, ("#6b7280", "#f3f4f6"))
    return ColorPair(bg, accent)


def status_hex(status: str) -> str:
    """Return hex colour for an upload status."""
    table = _STATUS_DARK if is_dark() else _STATUS_LIGHT
    return table.get(status, "#888888")


def field_color(tag: str) -> ColorPair:
    """Return (bg, text) for a MARC field tag (or its base, e.g. '1' → '100')."""
    table = _FIELD_DARK if is_dark() else _FIELD_LIGHT
    # Try exact, then base tag (first digit + "00")
    if tag in table:
        bg, text = table[tag]
        return ColorPair(bg, text)
    base = tag[0] + "00" if tag else "001"
    bg, text = table.get(base, table["001"])
    return ColorPair(bg, text)


def syntax_qcolor(token: str) -> "QColor":
    """Return a QColor for a syntax highlighting token."""
    from PyQt6.QtGui import QColor  # noqa: PLC0415

    table = _SYNTAX_DARK if is_dark() else _SYNTAX_LIGHT
    r, g, b = table.get(token, (140, 140, 140))
    return QColor(r, g, b)


def ui(key: str) -> str:
    """Return a UI chrome colour string by key."""
    table = _UI_DARK if is_dark() else _UI_LIGHT
    return table.get(key, "#888888")


def button_style(variant: str = "primary") -> str:
    """Return a QPushButton QSS stylesheet string."""
    bg = ui("button_bg")
    hover = ui("button_hover")
    disabled = ui("button_disabled")
    return (
        f"QPushButton {{ background-color: {bg}; color: white; "
        f"padding: 5px 16px; border-radius: 4px; font-weight: bold; border: none; }}"
        f"QPushButton:hover {{ background-color: {hover}; }}"
        f"QPushButton:disabled {{ background-color: {disabled}; }}"
    )


def frame_style() -> str:
    """Return a QFrame QSS stylesheet string."""
    return f"QFrame {{ border: 1px solid {ui('border')}; border-radius: 6px; }}"


def node_colors_for_js() -> dict[str, dict[str, str]]:
    """Return node colours as a dict suitable for JSON injection into the HTML template."""
    table = _NODE_DARK if is_dark() else _NODE_LIGHT
    return {k: {"bg": v.bg, "border": v.text} for k, v in table.items()}
