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

# ── Spacing tokens (px) — 4-pt grid (Tailwind/shadcn/Linear convention) ─────
SPACE_0: int = 0
SPACE_XS: int = 4    # space-1
SPACE_SM: int = 8    # space-2
SPACE_MD: int = 12   # space-3
SPACE_LG: int = 16   # space-4
SPACE_XL: int = 24   # space-6
SPACE_2XL: int = 32  # space-8
SPACE_3XL: int = 40  # space-10
SPACE_4XL: int = 48  # space-12

# ── Border radius tokens (px) ────────────────────────────────────────────────
# Liquid Glass uses generous, concentric corners: an inner element's radius
# equals its parent's radius minus the parent's padding. Provide enough stops
# to keep that relationship intact from tiny chips (pill buttons) all the way
# up to top-level glass surfaces.
RADIUS_SM: int = 4
RADIUS_MD: int = 6
RADIUS_LG: int = 8
RADIUS_XL: int = 12
RADIUS_2XL: int = 16
RADIUS_3XL: int = 22
RADIUS_PILL: int = 999

# ── Typography scale (2026 desktop convention: smaller than web) ──────────
# Linear, Notion, Stripe, Raycast settle on 12–13 for body, 14 for
# primary content. Mini-labels at 11 with +0.06em letter-spacing.
FONT_XS: int = 11    # meta, mini-caps labels (no smaller on desktop)
FONT_SM: int = 12    # secondary labels, table cells
FONT_MD: int = 12    # UI body — alias for SM to avoid false precision
FONT_BASE: int = 13  # primary body on macOS
FONT_LG: int = 14    # primary content
FONT_XL: int = 16    # card titles
FONT_2XL: int = 18   # section headings
FONT_3XL: int = 22   # page headings
FONT_4XL: int = 28   # hero, empty-state

# Font weights (avoid 700+ in UI chrome — reads aggressive)
WEIGHT_REGULAR: int = 400
WEIGHT_MEDIUM: int = 500   # labels, buttons
WEIGHT_SEMIBOLD: int = 600  # emphasis, headings
# Letter-spacing for mini-caps labels only — never on Hebrew
TRACK_MINICAPS: str = "1px"

# Native font stacks — SF Pro on macOS, Segoe UI Variable on Win 11
FONT_STACK_SANS: str = (
    '-apple-system, "SF Pro Text", "Segoe UI Variable", "Segoe UI", '
    '"Inter", "Helvetica Neue", Arial, sans-serif'
)
FONT_STACK_MONO: str = (
    '"SF Mono", "JetBrains Mono", "Cascadia Code", '
    '"Consolas", "Menlo", monospace'
)

# ── Liquid Glass material tokens ────────────────────────────────────────────
# Apple HIG recommends frost alpha 10–25 (out of 100). On the 0–255 Qt scale
# that maps to roughly 25–65. Values over ~80 read as "milky plastic".
GLASS_FROST_THIN: int = 28       # overlay chips / chrome
GLASS_FROST_REGULAR: int = 42    # panels, sidebars
GLASS_FROST_THICK: int = 58      # dialogs, popovers
GLASS_FROST_OPAQUE: int = 200    # nested control fills — NEVER stack two frosted layers

# Specular rim alpha (1-px inner light edge that mimics the glass edge catch)
GLASS_RIM_ALPHA_LIGHT: int = 40  # on dark mode → whitish rim
GLASS_RIM_ALPHA_DARK: int = 24   # on light mode → soft black rim

# ── Semantic colours (shared across light/dark) ─────────────────────────────
# Reserved for state — success, warning, error, info. One accent for
# primary intent; these four carry meaning, never decoration.
SEMANTIC_SUCCESS: str = "#16a34a"
SEMANTIC_SUCCESS_DARK: str = "#4ade80"
SEMANTIC_WARNING: str = "#d97706"
SEMANTIC_WARNING_DARK: str = "#f6b94a"
SEMANTIC_ERROR: str = "#dc2626"
SEMANTIC_ERROR_DARK: str = "#f87171"
SEMANTIC_INFO: str = "#2563eb"
SEMANTIC_INFO_DARK: str = "#60a5fa"

# ── Elevation scale (layered soft shadows) ───────────────────────────────────
# QGraphicsDropShadowEffect supports only one layer, so these values feed
# ``apply_drop_shadow(widget, blur, offset_y, alpha)``. For QSS we stack via
# a 1-px inner rim plus a single drop shadow.
ELEVATION: dict[str, tuple[int, int, int]] = {
    "xs": (6, 1, 28),     # (blur, offset_y, alpha 0-255)
    "sm": (12, 2, 40),
    "md": (22, 4, 55),
    "lg": (30, 8, 75),
    "xl": (42, 12, 95),
}

# ── Motion ──────────────────────────────────────────────────────────────────
DURATION_INSTANT: int = 80     # toggles, checkbox flip
DURATION_FAST: int = 120       # hover state
DURATION_BASE: int = 200       # dropdown, drawer
DURATION_SLOW: int = 320       # modal, sheet
# Qt QEasingCurve constants are not importable at module top level (torch-style
# lazy import rule), so we expose the cubic-bezier parameters as tuples.
EASING_STANDARD: tuple[float, float, float, float] = (0.4, 0.0, 0.2, 1.0)
EASING_DECELERATE: tuple[float, float, float, float] = (0.0, 0.0, 0.2, 1.0)
EASING_ACCELERATE: tuple[float, float, float, float] = (0.4, 0.0, 1.0, 1.0)

# ── Interactive state tints (apply as overlay on base fill) ────────────────
STATE_HOVER_ALPHA: int = 15       # ~6% on 0-255 scale
STATE_PRESSED_ALPHA: int = 30
STATE_SELECTED_ALPHA: int = 40
STATE_DISABLED_OPACITY: float = 0.38

# ── Focus ring (keyboard focus only) ───────────────────────────────────────
FOCUS_RING_WIDTH: int = 2
FOCUS_RING_OFFSET: int = 2
FOCUS_RING_ALPHA: int = 140   # ~55% of accent

# ── Control sizing (consensus heights) ─────────────────────────────────────
HEIGHT_CHIP: int = 26
HEIGHT_INPUT: int = 32
HEIGHT_BUTTON_SM: int = 28
HEIGHT_BUTTON_MD: int = 32
HEIGHT_BUTTON_LG: int = 40
HEIGHT_TABLE_ROW: int = 36

# ── Checkbox tokens ─────────────────────────────────────────────────────────
CHECKBOX_SIZE: int = 16
CHECKBOX_BORDER_WIDTH: int = 1   # QSS accepts int; 1px on HiDPI reads fine
CHECKBOX_RADIUS: int = 4


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


_BUTTON_PALETTE: dict[str, dict[str, str]] = {
    # (R, G, B) alpha triples for the fill gradient, and text color.
    # Each semantic variant has a hue signalling intent at a glance.
    # ``primary`` → indigo  (main "go" action)
    # ``success`` → green   (save / commit)
    # ``warning`` → amber   (rebuild / caution)
    # ``danger``  → red     (delete / revert)
    # ``config``  → slate   (settings / configure / sources)
    # ``load``    → cyan    (open-existing / load results / import)
    "primary_dark":  {"rgb": "99, 102, 241",  "text": "#ffffff"},
    "primary_light": {"rgb": "79, 91, 213",   "text": "#ffffff"},
    "success_dark":  {"rgb": "22, 163, 74",   "text": "#ffffff"},
    "success_light": {"rgb": "22, 163, 74",   "text": "#ffffff"},
    "warning_dark":  {"rgb": "245, 158, 11",  "text": "#ffffff"},
    "warning_light": {"rgb": "217, 119, 6",   "text": "#ffffff"},
    "danger_dark":   {"rgb": "220, 38, 38",   "text": "#ffffff"},
    "danger_light":  {"rgb": "220, 38, 38",   "text": "#ffffff"},
    # Slate-grey — reads as a quiet chrome colour distinct from accent /
    # success; matches settings-gear iconography.
    "config_dark":   {"rgb": "100, 116, 139", "text": "#ffffff"},
    "config_light":  {"rgb": "71, 85, 105",   "text": "#ffffff"},
    # Cyan-teal — the "open / import" hue that Linear, Notion and macOS
    # use for reading existing data into the app.
    "load_dark":     {"rgb": "20, 184, 166",  "text": "#ffffff"},
    "load_light":    {"rgb": "13, 148, 136",  "text": "#ffffff"},
}


def _tinted_glass_button(variant: str) -> str:
    """Build a Liquid-Glass button QSS for any coloured variant."""
    dark = is_dark()
    key = f"{variant}_{'dark' if dark else 'light'}"
    p = _BUTTON_PALETTE.get(key) or _BUTTON_PALETTE["primary_dark"]
    rgb = p["rgb"]
    text = p["text"]
    return (
        f"QPushButton {{"
        f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 rgba({rgb}, 235), stop:0.5 rgba({rgb}, 195),"
        f" stop:1 rgba({rgb}, 215));"
        f" color: {text};"
        f" padding: 6px 14px;"
        f" border-radius: {RADIUS_MD}px;"
        f" border: 1px solid rgba(255, 255, 255, 40);"
        f" border-top: 1px solid rgba(255, 255, 255, 135);"
        f" font-size: {FONT_MD}px; font-weight: 600;"
        f"}}"
        f"QPushButton:hover {{"
        f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 rgba({rgb}, 250), stop:1 rgba({rgb}, 230));"
        f"}}"
        f"QPushButton:pressed {{ padding-top: 7px; padding-bottom: 5px; }}"
        f"QPushButton:disabled {{ background: rgba(120, 120, 140, 120);"
        f" color: rgba(255, 255, 255, 140);"
        f" border: 1px solid rgba(255, 255, 255, 30); }}"
    )


def button_style(variant: str = "primary") -> str:
    """Return a Liquid-Glass button QSS for the given semantic variant.

    Eight variants, each a distinct hue so the user reads intent at a
    glance without studying labels:

    ==========  ==========================================================
    variant     use case                                      hue
    ==========  ==========================================================
    primary     main action (Run, Extract, Build, Upload)     indigo
    success     positive-commit (Save, Approve, Done)         green
    warning     destructive but common (Rebuild Index)        amber
    danger      truly destructive (Delete, Revert)            red
    config      settings (Configure…, Sources)                slate
    load        import/open (Browse…, Load Results)           cyan-teal
    secondary   neutral chrome button                         neutral
    ghost       minimal / tertiary (Cancel, Close, All)       neutral
    ==========  ==========================================================

    ``secondary`` and ``ghost`` both inherit the app-wide default
    ``QPushButton`` glass style — use them when the button doesn't merit
    its own hue but still needs the glass treatment.
    """
    if variant in {"primary", "success", "warning", "danger", "config", "load"}:
        return _tinted_glass_button(variant)
    if variant == "ghost":
        return ghost_button_style()
    # secondary / fallback — same visual as the global default QPushButton
    return ghost_button_style()


def primary_btn_style() -> str:
    return _tinted_glass_button("primary")


def danger_btn_style() -> str:
    return _tinted_glass_button("danger")


def config_btn_style() -> str:
    """Slate-grey Liquid-Glass — for settings/configure/sources buttons."""
    return _tinted_glass_button("config")


def load_btn_style() -> str:
    """Cyan-teal Liquid-Glass — for Browse…, Load Results, Import actions."""
    return _tinted_glass_button("load")


def secondary_btn_style() -> str:
    """Neutral glass chrome button — inherits the default."""
    return ghost_button_style()


def frame_style() -> str:
    """Return a QFrame QSS stylesheet string (Liquid Glass surface)."""
    return glass_surface_style(radius=RADIUS_XL, frost=GLASS_FROST_REGULAR)


# ── Liquid Glass surface primitives ──────────────────────────────────────────


def _rim_color() -> str:
    """Return the specular-rim border colour for the current theme."""
    alpha = GLASS_RIM_ALPHA_LIGHT if is_dark() else GLASS_RIM_ALPHA_DARK
    channel = "255, 255, 255" if is_dark() else "0, 0, 0"
    return f"rgba({channel}, {alpha})"


def _glass_fill(frost: int) -> tuple[str, str]:
    """Return a (top, bottom) gradient stop pair for a Liquid Glass surface.

    Top is ~12% brighter than bottom to mimic the subtle lensing highlight
    that runs across the top edge of a glass pane.
    """
    if is_dark():
        top = f"rgba(255, 255, 255, {min(frost + 14, 90)})"
        bot = f"rgba(255, 255, 255, {max(frost - 8, 0)})"
    else:
        top = f"rgba(255, 255, 255, {min(frost + 80, 230)})"
        bot = f"rgba(255, 255, 255, {max(frost + 40, 140)})"
    return top, bot


def glass_surface_style(
    *,
    radius: int = RADIUS_XL,
    frost: int = GLASS_FROST_REGULAR,
    selector: str = "QFrame",
) -> str:
    """Generate a Liquid Glass QSS block for *selector*.

    Combines the 1-px specular rim, the top-to-bottom lensing gradient and a
    generous concentric corner radius. Intended for panels, cards, popovers
    and dialog chrome — not for nested controls.
    """
    top, bot = _glass_fill(frost)
    rim = _rim_color()
    return (
        f"{selector} {{"
        f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {top}, stop:1 {bot});"
        f" border: 1px solid {rim};"
        f" border-radius: {radius}px;"
        f"}}"
    )


def glass_chip_style(
    *,
    radius: int = RADIUS_LG,
    frost: int = GLASS_FROST_THIN,
) -> str:
    """QSS for small glass pills (badges, chips, inline tokens)."""
    return glass_surface_style(radius=radius, frost=frost, selector="QFrame")


def content_surface_style(
    *,
    radius: int = RADIUS_XL,
    selector: str = "QFrame",
) -> str:
    """Flat content surface — opaque neutral background, thin rim border.

    HIG discourages stacking glass on glass. Use this for content panels
    (results previews, text viewers, tables) that already sit *inside* a
    glass chrome surface. Produces a calm, non-distracting field that
    lets the content itself carry the visual weight.
    """
    dark = is_dark()
    if dark:
        bg = "rgba(255, 255, 255, 10)"    # barely-there tint on dark backdrop
    else:
        bg = "rgba(255, 255, 255, 210)"   # near-opaque card on light backdrop
    rim = _rim_color()
    return (
        f"{selector} {{"
        f" background: {bg};"
        f" border: 1px solid {rim};"
        f" border-radius: {radius}px;"
        f"}}"
    )


def apply_drop_shadow(
    widget: object,
    *,
    blur: int = 24,
    offset_y: int = 6,
    alpha: int | None = None,
) -> None:
    """Attach a soft Liquid-Glass drop shadow to *widget*.

    Liquid Glass uses soft, slightly-offset shadows to lift glass panes off
    their underlying surface. Alpha defaults to a lighter shadow in dark
    mode (where the shadow is a glow) and a darker shadow in light mode.
    """
    try:
        from PyQt6.QtGui import QColor  # noqa: PLC0415
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect  # noqa: PLC0415
    except Exception:
        return

    if alpha is None:
        alpha = 80 if is_dark() else 55

    effect = QGraphicsDropShadowEffect()
    effect.setBlurRadius(blur)
    effect.setOffset(0, offset_y)
    effect.setColor(QColor(0, 0, 0, alpha))
    try:
        widget.setGraphicsEffect(effect)  # type: ignore[attr-defined]
    except Exception:
        pass


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
    """Amber action button with Liquid-Glass treatment."""
    return _tinted_glass_button("warning")


def success_btn_style() -> str:
    """Green 'continue / save' button with Liquid-Glass treatment."""
    return _tinted_glass_button("success")


def filter_chip_style() -> str:
    """Toggle-chip QSS for filter rows (Linear / Notion / Raycast pattern).

    Used with ``QPushButton(checkable=True)`` instead of ``QCheckBox`` — which
    eliminates the indicator-to-text spacing problem that traditional
    checkboxes suffer in dense horizontal rows.

    - **Inactive:** subtle 1-px border, transparent fill, secondary text.
    - **Active** (`:checked`): accent-tinted background at 20% alpha,
      accent border, primary-emphasis text colour.
    - **Hover:** faint fill tint — never a colour change, HIG guidance.
    - Pill shape (``border-radius: 999``), 10×4 padding.
    """
    dark = is_dark()
    if dark:
        text_idle = "#cbd5e1"       # neutral-300
        text_active = "#e0e7ff"     # indigo-100
        accent_fill = "rgba(124, 140, 248, 60)"     # 24% alpha
        accent_border = "rgba(124, 140, 248, 180)"
        hover_fill = "rgba(255, 255, 255, 14)"
        border_idle = "rgba(255, 255, 255, 34)"
    else:
        text_idle = "#4b5563"
        text_active = "#312e81"
        accent_fill = "rgba(79, 91, 213, 40)"
        accent_border = "rgba(79, 91, 213, 180)"
        hover_fill = "rgba(0, 0, 0, 14)"
        border_idle = "rgba(0, 0, 0, 36)"
    return (
        f"QPushButton {{"
        f" background: transparent;"
        f" color: {text_idle};"
        f" border: 1px solid {border_idle};"
        f" border-radius: {RADIUS_PILL}px;"
        f" padding: 3px 12px;"
        f" font-size: {FONT_SM}px;"
        f" font-weight: {WEIGHT_MEDIUM};"
        f" min-height: {HEIGHT_CHIP - 8}px;"
        f"}}"
        f"QPushButton:hover {{ background: {hover_fill}; }}"
        f"QPushButton:checked {{"
        f" background: {accent_fill};"
        f" color: {text_active};"
        f" border: 1px solid {accent_border};"
        f"}}"
        f"QPushButton:checked:hover {{ background: {accent_fill}; }}"
    )


def minicaps_label_style() -> str:
    """Tracked, uppercase mini-label (11-px, 1-px tracking) — Linear pattern."""
    return (
        f"color: {ui('subtext')};"
        f" font-size: {FONT_XS}px;"
        f" font-weight: {WEIGHT_SEMIBOLD};"
        f" letter-spacing: {TRACK_MINICAPS};"
        f" text-transform: uppercase;"
    )


def ghost_button_style() -> str:
    """Low-emphasis Liquid-Glass button — uncoloured but still clearly a button.

    Still a glass surface (subtle frost + rim) so it reads as a tappable
    control, not as a text link. Used for non-primary actions like "All",
    "None", "Cancel", "Revert".
    """
    dark = is_dark()
    if dark:
        base_top = "rgba(255, 255, 255, 34)"
        base_bot = "rgba(255, 255, 255, 14)"
        hover_top = "rgba(255, 255, 255, 52)"
        hover_bot = "rgba(255, 255, 255, 26)"
        rim_top = "rgba(255, 255, 255, 95)"
        rim_bot = "rgba(255, 255, 255, 30)"
        text = "#e5e7eb"
    else:
        base_top = "rgba(255, 255, 255, 220)"
        base_bot = "rgba(245, 245, 248, 180)"
        hover_top = "rgba(255, 255, 255, 245)"
        hover_bot = "rgba(248, 248, 252, 220)"
        rim_top = "rgba(0, 0, 0, 55)"
        rim_bot = "rgba(0, 0, 0, 22)"
        text = "#1f2937"
    return (
        f"QPushButton {{"
        f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {base_top}, stop:1 {base_bot});"
        f" color: {text};"
        f" border: 1px solid {rim_bot};"
        f" border-top: 1px solid {rim_top};"
        f" border-radius: {RADIUS_MD}px;"
        f" padding: 4px 12px;"
        f" font-size: {FONT_SM}px; font-weight: 500;"
        f"}}"
        f"QPushButton:hover {{"
        f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {hover_top}, stop:1 {hover_bot});"
        f"}}"
        f"QPushButton:pressed {{ padding-top: 5px; padding-bottom: 3px; }}"
    )


# ── App-level stylesheet ─────────────────────────────────────────────────────


def generate_app_stylesheet() -> str:
    """Generate the global Liquid Glass QSS applied at app startup.

    Covers scrollbars, splitters, the main window backdrop gradient, the
    sidebar list, form controls (QLineEdit, QSpinBox, QComboBox), tabs,
    group boxes, table headers, tooltips and the log viewer. Individual
    widgets may still supply per-widget stylesheets; those generally layer
    on top of these defaults without conflict.

    Design intent (Apple HIG 2026 — Liquid Glass):
      * Translucent, frosted surfaces — 10–25% frost (see ``GLASS_FROST_*``)
      * 1-px specular rim — soft white on dark / soft black on light
      * Concentric corners — generous radii on surfaces, smaller pills on chips
      * Adaptive colour — subtle top-to-bottom lensing gradient
      * Nested solid fills — buttons/inputs sit on opaque tints so text stays
        legible even when the glass backdrop is busy
    """
    dark = is_dark()
    border = ui("border")
    rim = _rim_color()
    # Inline SVG check mark — crisp at any DPI, works identically on Mac + Windows
    checkmark_url = (
        "data:image/svg+xml;utf8,"
        "<svg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 14 14'>"
        "<path d='M3 7.2 L5.8 10 L11 4.2' fill='none' stroke='white' "
        "stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/>"
        "</svg>"
    )

    if dark:
        # Backdrop: deep neutral charcoal → calm, not tinted with accent
        window_top     = "rgba(20, 22, 28, 240)"
        window_bot     = "rgba(14, 15, 20, 240)"
        # Sidebar: restrained frost (HIG: chrome only, no stacked glass)
        sidebar_bg_t   = "rgba(255, 255, 255, 14)"
        sidebar_bg_b   = "rgba(255, 255, 255, 6)"
        # Accent: muted indigo — not saturated — used sparingly
        sidebar_sel_t  = "rgba(99, 102, 241, 170)"
        sidebar_sel_b  = "rgba(79, 70, 229, 170)"
        sidebar_hover  = "rgba(255, 255, 255, 14)"
        input_bg       = "rgba(255, 255, 255, 10)"
        input_focus_bg = "rgba(255, 255, 255, 22)"
        input_text     = "#e5e7eb"
        # Log viewer sits on content, not chrome — flat neutral
        log_top        = "rgba(255, 255, 255, 8)"
        log_bot        = "rgba(255, 255, 255, 8)"
        tab_text       = "#9ca3af"
        tab_bg         = "transparent"
        tab_hover      = "rgba(255, 255, 255, 14)"
        tab_sel        = "rgba(255, 255, 255, 22)"
        tooltip_bg     = "rgba(30, 32, 40, 244)"
        tooltip_text   = "#f3f4f6"
        # Glass button tokens — bright top catch, dim bottom body
        btn_top        = "rgba(255, 255, 255, 34)"
        btn_mid        = "rgba(255, 255, 255, 18)"
        btn_bot        = "rgba(255, 255, 255, 22)"
        btn_hover_top  = "rgba(255, 255, 255, 52)"
        btn_hover_bot  = "rgba(255, 255, 255, 28)"
        btn_pressed    = "rgba(255, 255, 255, 16)"
        btn_disabled   = "rgba(255, 255, 255, 8)"
        btn_rim_top    = "rgba(255, 255, 255, 100)"
        btn_rim_bot    = "rgba(255, 255, 255, 30)"
    else:
        # Backdrop: warm neutral — matches macOS Tahoe desktop tint, not sky-blue
        window_top     = "rgba(250, 250, 252, 246)"
        window_bot     = "rgba(242, 242, 246, 246)"
        sidebar_bg_t   = "rgba(255, 255, 255, 170)"
        sidebar_bg_b   = "rgba(248, 248, 250, 160)"
        sidebar_sel_t  = "rgba(59, 130, 246, 190)"
        sidebar_sel_b  = "rgba(37, 99, 235, 190)"
        sidebar_hover  = "rgba(0, 0, 0, 10)"
        input_bg       = "rgba(255, 255, 255, 200)"
        input_focus_bg = "rgba(255, 255, 255, 248)"
        input_text     = "#1f2937"
        log_top        = "rgba(255, 255, 255, 220)"
        log_bot        = "rgba(255, 255, 255, 220)"
        tab_text       = "#4b5563"
        tab_bg         = "transparent"
        tab_hover      = "rgba(0, 0, 0, 8)"
        tab_sel        = "rgba(0, 0, 0, 18)"
        tooltip_bg     = "rgba(255, 255, 255, 248)"
        tooltip_text   = "#111827"
        # Glass button tokens (light): near-opaque white with a Fresnel rim
        btn_top        = "rgba(255, 255, 255, 230)"
        btn_mid        = "rgba(250, 250, 252, 200)"
        btn_bot        = "rgba(240, 242, 247, 210)"
        btn_hover_top  = "rgba(255, 255, 255, 248)"
        btn_hover_bot  = "rgba(250, 250, 252, 230)"
        btn_pressed    = "rgba(235, 235, 240, 220)"
        btn_disabled   = "rgba(245, 245, 248, 140)"
        btn_rim_top    = "rgba(0, 0, 0, 55)"
        btn_rim_bot    = "rgba(0, 0, 0, 22)"

    return f"""
/* ─── Scrollbars (invisible track + soft glass handle) ─── */
QScrollBar:vertical, QScrollBar:horizontal {{
    background: transparent;
    margin: 0px;
}}
QScrollBar:vertical {{ width: 10px; }}
QScrollBar:horizontal {{ height: 10px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {border};
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {rim};
}}
QScrollBar::handle:vertical {{ min-height: 28px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ─── Splitter handles ─── */
QSplitter::handle {{ background: {rim}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* ─── Backdrop (main window + every modal dialog) ─── */
/* Dialogs are top-level windows — Qt doesn't inherit QMainWindow QSS —
   so they need their own rule. Using the same gradient as the main
   window keeps the Liquid-Glass hierarchy consistent across popups.  */
QMainWindow, QDialog {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {window_top}, stop:1 {window_bot});
}}

/* ─── Sidebar navigation list ─── */
QListWidget {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {sidebar_bg_t}, stop:1 {sidebar_bg_b});
    border: 1px solid {rim};
    border-radius: {RADIUS_2XL}px;
    outline: none;
    padding: {SPACE_XS}px;
}}
QListWidget::item {{
    padding: {SPACE_SM}px {SPACE_MD}px;
    border-radius: {RADIUS_LG}px;
    margin: 2px 2px;
    color: {input_text};
}}
QListWidget::item:selected {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {sidebar_sel_t}, stop:1 {sidebar_sel_b});
    color: white;
}}
QListWidget::item:hover:!selected {{
    background: {sidebar_hover};
}}

/* ─── Glass form controls (nested solid fills per HIG) ─── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {input_bg};
    color: {input_text};
    border: 1px solid {rim};
    border-radius: {RADIUS_LG}px;
    padding: 4px 10px;
    selection-background-color: {sidebar_sel_t};
    selection-color: white;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    background: {input_focus_bg};
    border: 1px solid {sidebar_sel_t};
}}
QComboBox {{
    min-height: 22px;
    padding-right: 22px;   /* leave room for the native chevron */
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: right center;
    border: none;
    width: 22px;
    margin-right: 2px;
}}
/* Let Qt paint its native down-arrow — overriding ::down-arrow with
   image:none leaves nothing visible and some platforms also disable the
   click target, which makes the combo feel unresponsive. */
QComboBox QAbstractItemView {{
    background: {input_focus_bg};
    border: 1px solid {rim};
    border-radius: {RADIUS_MD}px;
    selection-background-color: {sidebar_sel_t};
    selection-color: white;
    padding: 4px;
    outline: none;
    min-width: 120px;       /* dropdown popup never narrower than this */
}}
QComboBox QAbstractItemView::item {{
    padding: 5px 10px;
    min-height: 22px;
    border-radius: 3px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {tab_hover};
}}

/* ─── Group boxes (glass card with embedded caption) ─── */
QGroupBox {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {sidebar_bg_t}, stop:1 {sidebar_bg_b});
    border: 1px solid {rim};
    border-radius: {RADIUS_XL}px;
    margin-top: {SPACE_LG}px;
    padding: {SPACE_MD}px;
    color: {input_text};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    left: {SPACE_MD}px;
    color: {input_text};
    font-weight: 600;
}}

/* ─── Tabs ─── */
QTabWidget::pane {{
    background: transparent;
    border: 1px solid {rim};
    border-radius: {RADIUS_XL}px;
    top: -1px;
}}
QTabBar::tab {{
    background: {tab_bg};
    color: {tab_text};
    padding: 6px 16px;
    border-top-left-radius: {RADIUS_LG}px;
    border-top-right-radius: {RADIUS_LG}px;
    margin-right: 2px;
    font-weight: 500;
}}
QTabBar::tab:hover {{ background: {tab_hover}; }}
QTabBar::tab:selected {{
    background: {tab_sel};
    color: white;
}}

/* ─── Tables & headers ─── */
QHeaderView::section {{
    background: {tab_bg};
    color: {input_text};
    padding: 4px 10px;
    border: none;
    border-right: 1px solid {rim};
    font-weight: 600;
}}
QTableView {{
    background: transparent;
    alternate-background-color: {tab_bg};
    color: {input_text};
    gridline-color: {rim};
    border: 1px solid {rim};
    border-radius: {RADIUS_LG}px;
    selection-background-color: {sidebar_sel_t};
    selection-color: white;
}}
/* Default delegate honours `color:` from the table rule, so cells without
   an explicit ForegroundRole on the model render with the theme's input_text
   colour — matching the dialog/window gradient. Without this rule the cell
   falls back to QPalette.text() which on Windows light mode is dark, and
   the QSS-driven dialog background is dark, producing invisible cells.   */
QTableView::item {{
    color: {input_text};
}}
QTableView::item:selected {{
    color: white;
}}

/* ─── Checkboxes (subtle 14-px square, accent only on checked) ─── */
/* Linear / Notion convention: unchecked = thin neutral border, checked = */
/* muted accent fill with a clean white check mark — no oversized pills.  */
QCheckBox {{
    spacing: 8px;
    color: {input_text};
    font-size: {FONT_MD}px;
    padding: 2px 0px;
}}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {rim};
    border-radius: 3px;
    background: transparent;
}}
QCheckBox::indicator:hover {{
    border: 1px solid {sidebar_sel_t};
    background: {sidebar_hover};
}}
QCheckBox::indicator:checked {{
    border: 1px solid {sidebar_sel_b};
    background: {sidebar_sel_t};
}}
QCheckBox::indicator:checked:hover {{
    background: {sidebar_sel_b};
}}
QCheckBox:disabled {{ color: rgba(128,128,128,160); }}

/* ─── Log viewer & read-only text panes ─── */
QPlainTextEdit, QTextEdit {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {log_top}, stop:1 {log_bot});
    color: {input_text};
    border: 1px solid {rim};
    border-radius: {RADIUS_LG}px;
    selection-background-color: {sidebar_sel_t};
    selection-color: white;
}}

/* ─── Buttons (default Liquid Glass look for EVERY QPushButton) ─── */
/* Applies to buttons that don't set their own stylesheet — browse dialogs,
   run buttons, dialog close buttons, FileSelector "Browse…" etc. Primary-
   emphasis buttons still override with ``theme.button_style()`` where they
   want the accent fill.                                                     */
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {btn_top}, stop:0.5 {btn_mid}, stop:1 {btn_bot});
    color: {input_text};
    border: 1px solid {btn_rim_bot};
    border-top: 1px solid {btn_rim_top};
    border-radius: {RADIUS_MD}px;
    padding: 5px 14px;
    font-size: {FONT_MD}px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {btn_hover_top}, stop:1 {btn_hover_bot});
    border-color: {btn_rim_top};
}}
QPushButton:pressed {{
    padding-top: 6px;
    padding-bottom: 4px;
    background: {btn_pressed};
}}
QPushButton:disabled {{
    color: rgba(128, 128, 128, 160);
    background: {btn_disabled};
    border: 1px solid {btn_rim_bot};
}}
QPushButton:default {{
    border-top: 1px solid {btn_rim_top};
}}

/* ─── Tooltips (glass popover) ─── */
QToolTip {{
    background: {tooltip_bg};
    color: {tooltip_text};
    border: 1px solid {rim};
    border-radius: {RADIUS_MD}px;
    padding: 4px 8px;
}}

/* ─── Menu (File/Edit/Help) ─── */
QMenu {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {sidebar_bg_t}, stop:1 {sidebar_bg_b});
    border: 1px solid {rim};
    border-radius: {RADIUS_LG}px;
    padding: {SPACE_XS}px;
    color: {input_text};
}}
QMenu::item {{
    padding: 5px 14px;
    border-radius: {RADIUS_SM}px;
}}
QMenu::item:selected {{
    background: {sidebar_sel_t};
    color: white;
}}
QMenuBar {{
    background: transparent;
    color: {input_text};
}}
QMenuBar::item:selected {{
    background: {sidebar_hover};
    border-radius: {RADIUS_SM}px;
}}
"""


def apply_stylesheet(app: object) -> None:
    """Apply the current theme stylesheet to a QApplication instance."""
    app.setStyleSheet(generate_app_stylesheet())  # type: ignore[union-attr]


def invalidate_cache() -> None:
    """Clear the cached dark-mode flag (call after a palette/theme change)."""
    global _dark  # noqa: PLW0603
    _dark = None
