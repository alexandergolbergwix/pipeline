"""Centralised design system for the MHM Pipeline GUI.

Every widget imports tokens from this module instead of defining its own.
Supports Dark / Light / System (auto-detect) modes via ``SettingsManager``.

Design tokens
-------------
- Spacing:      SPACE_XS … SPACE_2XL  (px integers)
- Border radii: RADIUS_SM … RADIUS_LG  (px integers)
- Font sizes:   FONT_XS … FONT_XL      (px integers)
- Colors:       ui(), node_color(), entity_color(), role_color(), …
- Stylesheets:  button_style(), frame_style(), warning_banner_style(), …

Usage::

    from mhm_pipeline.gui import theme

    bg, text = theme.node_color("person")
    style    = theme.button_style()
    dark     = theme.is_dark()
    lbl.setStyleSheet(f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from PyQt6.QtGui import QColor

# ── Spacing tokens (px) ──────────────────────────────────────────────────────
SPACE_XS: int = 4
SPACE_SM: int = 8
SPACE_MD: int = 12
SPACE_LG: int = 16
SPACE_XL: int = 24
SPACE_2XL: int = 32

# ── Border radius tokens (px) ────────────────────────────────────────────────
RADIUS_SM: int = 4
RADIUS_MD: int = 6
RADIUS_LG: int = 8

# ── Font size tokens (px) ────────────────────────────────────────────────────
FONT_XS: int = 10
FONT_SM: int = 11
FONT_MD: int = 12
FONT_BASE: int = 13
FONT_LG: int = 14
FONT_XL: int = 16

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

        pref = str(SettingsManager().get("display/theme", "system"))
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
            palette = app.palette()  # type: ignore[attr-defined]
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
    "manuscript": ColorPair("#dbeafe", "#3b82f6"),
    "person": ColorPair("#fce7f3", "#ec4899"),
    "work": ColorPair("#dcfce7", "#22c55e"),
    "expression": ColorPair("#ccfbf1", "#14b8a6"),
    "place": ColorPair("#fef3c7", "#eab308"),
    "codicological_unit": ColorPair("#ffedd5", "#f97316"),
    "event": ColorPair("#ede9fe", "#8b5cf6"),
    "organization": ColorPair("#e0e7ff", "#6366f1"),
    "default": ColorPair("#f3f4f6", "#6b7280"),
}
_NODE_DARK: dict[str, ColorPair] = {
    "manuscript": ColorPair("#1e3a5f", "#60a5fa"),
    "person": ColorPair("#4a1942", "#f472b6"),
    "work": ColorPair("#14332d", "#4ade80"),
    "expression": ColorPair("#134e4a", "#2dd4bf"),
    "place": ColorPair("#422006", "#facc15"),
    "codicological_unit": ColorPair("#431407", "#fb923c"),
    "event": ColorPair("#2e1065", "#a78bfa"),
    "organization": ColorPair("#1e1b4b", "#818cf8"),
    "default": ColorPair("#374151", "#9ca3af"),
}

# Entity types (5) — used by entity_highlighter
_ENTITY_LIGHT: dict[str, tuple[str, str]] = {
    "PERSON": ("#c7d2fe", "#3730a3"),
    "DATE": ("#fed7aa", "#9a3412"),
    "PLACE": ("#bbf7d0", "#166534"),
    "WORK": ("#fecaca", "#991b1b"),
    "ORG": ("#e5e7eb", "#374151"),
}
_ENTITY_DARK: dict[str, tuple[str, str]] = {
    "PERSON": ("#312e81", "#c7d2fe"),
    "DATE": ("#431407", "#fed7aa"),
    "PLACE": ("#14532d", "#bbf7d0"),
    "WORK": ("#450a0a", "#fecaca"),
    "ORG": ("#374151", "#d1d5db"),
}

# Role types (7) — used by entity_highlighter
_ROLE_LIGHT: dict[str, tuple[str, str]] = {
    "AUTHOR": ("#c7d2fe", "#3730a3"),
    "SCRIBE": ("#fed7aa", "#9a3412"),
    "TRANSCRIBER": ("#fed7aa", "#9a3412"),
    "OWNER": ("#bbf7d0", "#166534"),
    "CENSOR": ("#fecaca", "#991b1b"),
    "TRANSLATOR": ("#e5e7eb", "#374151"),
    "COMMENTATOR": ("#dbeafe", "#1e40af"),
}
_ROLE_DARK: dict[str, tuple[str, str]] = {
    "AUTHOR": ("#312e81", "#c7d2fe"),
    "SCRIBE": ("#431407", "#fed7aa"),
    "TRANSCRIBER": ("#431407", "#fed7aa"),
    "OWNER": ("#14532d", "#bbf7d0"),
    "CENSOR": ("#450a0a", "#fecaca"),
    "TRANSLATOR": ("#374151", "#d1d5db"),
    "COMMENTATOR": ("#1e3a5f", "#93c5fd"),
}

# State colours (4) — used by stage_progress
_STATE_LIGHT = {
    "pending": (180, 180, 180),
    "running": (50, 130, 240),
    "done": (60, 180, 75),
    "error": (220, 50, 50),
}
_STATE_DARK = {
    "pending": (120, 120, 120),
    "running": (60, 140, 255),
    "done": (70, 200, 85),
    "error": (240, 60, 60),
}

# Confidence background — used by authority_matcher_view
_CONF_LIGHT = {"high": "#dcfce7", "medium": "#fef3c7", "low": "#fee2e2"}
_CONF_DARK = {"high": "#14532d", "medium": "#422006", "low": "#450a0a"}

# Severity — used by validation_result_view
_SEV_LIGHT: dict[str, tuple[str, str]] = {
    "violation": ("#ef4444", "#fee2e2"),
    "warning": ("#f59e0b", "#fef3c7"),
    "info": ("#3b82f6", "#dbeafe"),
    "success": ("#22c55e", "#dcfce7"),
}
_SEV_DARK: dict[str, tuple[str, str]] = {
    "violation": ("#f87171", "#450a0a"),
    "warning": ("#fbbf24", "#422006"),
    "info": ("#60a5fa", "#1e3a5f"),
    "success": ("#4ade80", "#14532d"),
}

# Upload status hex — used by upload_progress_view
_STATUS_LIGHT = {
    "pending": "#888888",
    "uploading": "#3280F0",
    "success": "#3CB44B",
    "exists": "#3CB44B",
    "failed": "#DC3232",
    "skipped": "#888888",
}
_STATUS_DARK = {
    "pending": "#888888",
    "uploading": "#60a5fa",
    "success": "#4ade80",
    "exists": "#4ade80",
    "failed": "#f87171",
    "skipped": "#888888",
}

# MARC field colours — used by marc_field_visualizer
_FIELD_LIGHT: dict[str, tuple[str, str]] = {
    "001": ("#f3f4f6", "#374151"),
    "100": ("#dbeafe", "#1e40af"),
    "245": ("#a5f3fc", "#155e75"),
    "260": ("#ffedd5", "#9a3412"),
    "300": ("#bbf7d0", "#166534"),
    "500": ("#fef3c7", "#92400e"),
    "600": ("#e5dbff", "#5b21b6"),
    "700": ("#fce7f3", "#be185d"),
    "957": ("#fee2e2", "#991b1b"),
}
_FIELD_DARK: dict[str, tuple[str, str]] = {
    "001": ("#374151", "#d1d5db"),
    "100": ("#1e3a5f", "#93c5fd"),
    "245": ("#164e63", "#67e8f9"),
    "260": ("#431407", "#fdba74"),
    "300": ("#14532d", "#86efac"),
    "500": ("#422006", "#fde68a"),
    "600": ("#2e1065", "#c4b5fd"),
    "700": ("#4a1942", "#f9a8d4"),
    "957": ("#450a0a", "#fca5a5"),
}

# Syntax highlighting — used by ttl_preview
_SYNTAX_LIGHT = {"directive": (30, 80, 200), "uri": (20, 140, 60), "comment": (140, 140, 140)}
_SYNTAX_DARK = {"directive": (120, 160, 255), "uri": (80, 220, 120), "comment": (140, 140, 140)}

# UI chrome — used everywhere
_UI_LIGHT: dict[str, str] = {
    "text": "#1f2937",
    "subtext": "#6b7280",
    "border": "#d1d5db",
    "panel_bg": "#f9fafb",
    "page_bg": "#ffffff",
    "overlay_bg": "rgba(255,255,255,210)",
    "highlight": "#fbbf24",
    "warning": "#996600",
    "match_found": "#059669",
    "no_match": "#d97706",
    "button_bg": "#3b82f6",
    "button_hover": "#2563eb",
    "button_disabled": "#9ca3af",
    "record_header": "#6b7280",
    "record_content": "#f9fafb",
    "connector": "#a0a0a0",
}
_UI_DARK: dict[str, str] = {
    "text": "#cdd6f4",
    "subtext": "#a6adc8",
    "border": "#585b70",
    "panel_bg": "#313244",
    "page_bg": "#1e1e2e",
    "overlay_bg": "rgba(30,30,46,210)",
    "highlight": "#fbbf24",
    "warning": "#fbbf24",
    "match_found": "#34d399",
    "no_match": "#fbbf24",
    "button_bg": "#6366f1",
    "button_hover": "#4f46e5",
    "button_disabled": "#4b5563",
    "record_header": "#4b5563",
    "record_content": "#1e1e2e",
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


def state_qcolor(state: str) -> QColor:
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


def syntax_qcolor(token: str) -> QColor:
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


# ── Wikidata Preview source badge colours ────────────────────────────────────

_SOURCE_LIGHT: dict[str, tuple[str, str]] = {
    "MARC":           ("#f1f5f9", "MARC"),
    "Person NER":     ("#dbeafe", "Person NER 🤖"),
    "Provenance NER": ("#ede9fe", "Provenance NER 🤖"),
    "Contents NER":   ("#ccfbf1", "Contents NER 🤖"),
    "Colophon ML":    ("#ffedd5", "Colophon ML ⚡"),
    "VIAF":           ("#d1fae5", "VIAF"),
    "NLI/Mazal":      ("#bbf7d0", "NLI/Mazal"),
    "KIMA":           ("#dcfce7", "KIMA"),
}
_SOURCE_DARK: dict[str, tuple[str, str]] = {
    "MARC":           ("#1e293b", "MARC"),
    "Person NER":     ("#1e3a5f", "Person NER 🤖"),
    "Provenance NER": ("#2d1b69", "Provenance NER 🤖"),
    "Contents NER":   ("#042f2e", "Contents NER 🤖"),
    "Colophon ML":    ("#431407", "Colophon ML ⚡"),
    "VIAF":           ("#052e16", "VIAF"),
    "NLI/Mazal":      ("#052e16", "NLI/Mazal"),
    "KIMA":           ("#052e16", "KIMA"),
}


def source_bg(source: str) -> str:
    """Return background hex for a Wikidata Preview source badge."""
    dark = is_dark()
    table = _SOURCE_DARK if dark else _SOURCE_LIGHT
    fallback = "#1e293b" if dark else "#f8fafc"
    return table.get(source, (fallback, source))[0]


def source_label(source: str) -> str:
    """Return display label for a Wikidata Preview source."""
    return _SOURCE_LIGHT.get(source, ("#f8fafc", source))[1]


# ── Banner stylesheet helpers ────────────────────────────────────────────────


def info_banner_style() -> str:
    """QFrame stylesheet for an info banner — amber border, transparent background."""
    return (
        f"QFrame {{ border: 1px solid {ui('highlight')};"
        f" border-radius: {RADIUS_MD}px; padding: 4px; }}"
    )


def warning_banner_style() -> str:
    """QFrame stylesheet for a warning banner — amber tinted."""
    dark = is_dark()
    bg = "#422006" if dark else "#fffbeb"
    border = "#d97706" if dark else "#f59e0b"
    return (
        f"QFrame {{ background: {bg}; border: 1px solid {border};"
        f" border-radius: {RADIUS_MD}px; }}"
    )


def warning_text_color() -> str:
    """Foreground color for text inside a warning banner."""
    return "#fcd34d" if is_dark() else "#92400e"


def warning_btn_style() -> str:
    """Amber action button style for use inside a warning banner."""
    return (
        f"QPushButton {{ background: #f59e0b; color: white; border: none;"
        f" border-radius: {RADIUS_SM}px; padding: 4px 10px; font-size: {FONT_MD}px; }}"
        f"QPushButton:hover {{ background: #d97706; }}"
    )


def success_btn_style() -> str:
    """Green 'continue / save' button style."""
    return (
        f"QPushButton {{ background: #16a34a; color: white; border-radius: {RADIUS_SM}px;"
        f" padding: 6px 18px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: #15803d; }}"
        f"QPushButton:disabled {{ background: {ui('button_disabled')}; }}"
    )


# ── App-level stylesheet ─────────────────────────────────────────────────────


def generate_app_stylesheet() -> str:
    """Generate a global QSS string from current theme tokens.

    Applied once at app startup via ``apply_stylesheet()``.  Covers scrollbars,
    splitter handles, and a glass-aesthetic for the sidebar and log viewer.
    Individual widgets keep their own per-widget stylesheets.
    """
    dark = is_dark()
    border = ui("border")

    if dark:
        window_bg     = "rgba(20, 20, 32, 230)"
        sidebar_bg    = "rgba(24, 24, 37, 218)"
        sidebar_sel   = "rgba(99, 102, 241, 200)"
        sidebar_hover = "rgba(255, 255, 255, 18)"
        glass_border  = "rgba(255, 255, 255, 25)"
        log_bg        = "rgba(17, 17, 28, 220)"
    else:
        window_bg     = "rgba(248, 250, 252, 230)"
        sidebar_bg    = "rgba(243, 244, 246, 220)"
        sidebar_sel   = "rgba(59, 130, 246, 200)"
        sidebar_hover = "rgba(0, 0, 0, 15)"
        glass_border  = "rgba(0, 0, 0, 30)"
        log_bg        = "rgba(241, 245, 249, 220)"

    return f"""
QScrollBar:vertical {{
    width: 8px;
    background: transparent;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{
    height: 8px;
    background: transparent;
    margin: 0px;
}}
QScrollBar::handle:horizontal {{
    background: {border};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QMainWindow {{
    background: {window_bg};
}}

QListWidget {{
    background: {sidebar_bg};
    border: 1px solid {glass_border};
    border-radius: {RADIUS_LG}px;
    outline: none;
}}
QListWidget::item {{
    padding: {SPACE_SM}px {SPACE_MD}px;
    border-radius: {RADIUS_SM}px;
    margin: 1px 3px;
}}
QListWidget::item:selected {{
    background: {sidebar_sel};
    color: white;
    border-radius: {RADIUS_SM}px;
}}
QListWidget::item:hover:!selected {{
    background: {sidebar_hover};
    border-radius: {RADIUS_SM}px;
}}

QPlainTextEdit {{
    background: {log_bg};
    border: 1px solid {glass_border};
    border-radius: {RADIUS_MD}px;
}}
"""


def apply_stylesheet(app: object) -> None:
    """Apply the current theme stylesheet to a QApplication instance."""
    app.setStyleSheet(generate_app_stylesheet())  # type: ignore[union-attr]


def invalidate_cache() -> None:
    """Clear the cached dark-mode flag (call after a palette/theme change)."""
    global _dark  # noqa: PLW0603
    _dark = None
