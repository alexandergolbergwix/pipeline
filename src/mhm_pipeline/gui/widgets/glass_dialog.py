"""Liquid-glass base class for every QDialog in the app.

**Rule:** every popup, detail view, wizard page, or sheet in the MHM
Pipeline GUI must inherit from :class:`GlassDialog` (or call
:func:`install_glass_backdrop` with a bare QDialog).  This guarantees
visual continuity with the main window — the same ``GraphBackdrop``
particle/gradient surface lenses through every modal layer instead of
the dialog sitting on a flat dark fill.

Usage::

    class MyDialog(GlassDialog):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            layout = QVBoxLayout(self.glass_content)
            layout.addWidget(QLabel("Hello"))

Or, for an existing QDialog subclass you cannot easily rewrite::

    dialog = QDialog(parent)
    content = install_glass_backdrop(dialog)
    # put your UI inside `content`, not `dialog`
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QWidget


def install_glass_backdrop(dialog: QDialog) -> QWidget:
    """Insert a ``GraphBackdrop`` into *dialog* and return the translucent
    content container callers should populate.

    Idempotent: calling twice on the same dialog is a no-op that returns
    the existing glass-content child.
    """
    from mhm_pipeline.gui.widgets.graph_backdrop import GraphBackdrop  # noqa: PLC0415

    existing = dialog.findChild(QWidget, "__glass_content__")
    if existing is not None:
        return existing

    backdrop = GraphBackdrop(parent=dialog)
    backdrop.setObjectName("__glass_backdrop__")
    outer = dialog.layout()
    if outer is None:
        outer = QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(backdrop)

    content = QWidget(backdrop)
    content.setObjectName("__glass_content__")
    content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    backdrop_layout = QVBoxLayout(backdrop)
    backdrop_layout.setContentsMargins(0, 0, 0, 0)
    backdrop_layout.addWidget(content)
    return content


class GlassDialog(QDialog):
    """Every dialog in the MHM Pipeline app must use this as a base.

    Constructing a ``GlassDialog`` automatically installs the
    ``GraphBackdrop`` and exposes a ``glass_content`` widget for
    subclasses to populate.  The outer ``QDialog`` itself has NO layout
    content — subclasses should never call ``setLayout`` on ``self``.
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.glass_content: QWidget = install_glass_backdrop(self)


def glass_table_style(theme_mod: Any) -> str:
    """Translucent QTableView QSS so the backdrop reads through the table.

    Rule 52 update (2026-05-25): the old version hard-coded
    ``color: #e5e7eb`` (white) on the assumption that the
    ``GraphBackdrop`` is always dark. In practice the backdrop renders
    much lighter on macOS in light mode + when the dialog is large
    enough that the translucent panel grey dominates over the
    backdrop. White text on light-grey panel became invisible — a
    real bright-on-bright contrast bug.

    Fix: read every colour from ``theme_mod.ui(...)`` so dark/light
    auto-adapts. The translucent BACKGROUNDS stay glass-styled
    (low-alpha so the backdrop still reads through), but the
    foreground TEXT colour follows the active OS theme.
    """
    text = theme_mod.ui("text")
    subtext = theme_mod.ui("subtext")
    border = theme_mod.ui("border")
    is_dark = theme_mod.is_dark()
    # Glass background tint: dark glass on dark theme (rgba 0,0,0 90),
    # near-white glass on light theme (rgba 255,255,255 110). The
    # backdrop still reads through at low alpha — the panel just
    # tinted differently so dark text remains legible in light mode.
    panel_rgba = "rgba(0,0,0, 90)" if is_dark else "rgba(255,255,255, 140)"
    alt_rgba = "rgba(255,255,255, 10)" if is_dark else "rgba(0,0,0, 10)"
    header_rgba = "rgba(255,255,255, 12)" if is_dark else "rgba(0,0,0, 14)"
    grid_rgba = "rgba(255,255,255, 18)" if is_dark else "rgba(0,0,0, 22)"
    border_rgba = "rgba(255,255,255, 22)" if is_dark else "rgba(0,0,0, 28)"
    selection_text = "white" if is_dark else text
    return (
        f"QTableView {{"
        f" background: {panel_rgba};"
        f" alternate-background-color: {alt_rgba};"
        f" color: {text};"
        f" gridline-color: {grid_rgba};"
        f" border: 1px solid {border_rgba};"
        f" border-radius: {theme_mod.RADIUS_MD}px;"
        f" selection-background-color: rgba(99, 102, 241, 120);"
        f" selection-color: {selection_text};"
        f" }}"
        f"QHeaderView::section {{"
        f" background: {header_rgba};"
        f" color: {text};"
        f" padding: 6px 8px;"
        f" border: none;"
        f" border-bottom: 1px solid {border_rgba};"
        f" font-weight: 600;"
        f" }}"
        f"QTableView::item {{"
        f" padding: 4px 8px;"
        f" border: none;"
        f" color: {text};"
        f" }}"
        f"QTableView::item:selected {{"
        f" color: {selection_text};"
        f" }}"
        f"QTableCornerButton::section {{"
        f" background: {header_rgba};"
        f" border: none;"
        f" }}"
        f"QTableView {{ "
        f" /* subtext for hint rows applied via Qt::ForegroundRole */"
        f" }}"
        # Keep an unused subtext token reference so refactors don't
        # accidentally drop the lookup (small lint-friendly stub).
        f"/* subtext: {subtext} · border: {border} */"
    )


def glass_tab_style(theme_mod: Any) -> str:
    """Translucent QTabWidget QSS matching the liquid-glass look."""
    return (
        f"QTabWidget::pane {{"
        f" background: rgba(0,0,0, 75);"
        f" border: 1px solid rgba(255,255,255, 22);"
        f" border-radius: {theme_mod.RADIUS_MD}px; }}"
        f"QTabBar::tab {{"
        f" background: rgba(255,255,255, 12);"
        f" color: {theme_mod.ui('subtext')};"
        f" padding: 6px 14px;"
        f" border-top-left-radius: {theme_mod.RADIUS_SM}px;"
        f" border-top-right-radius: {theme_mod.RADIUS_SM}px;"
        f" margin-right: 2px; }}"
        f"QTabBar::tab:selected {{"
        f" background: rgba(99, 102, 241, 120);"
        f" color: white; }}"
    )


def glass_panel_style(theme_mod: Any) -> str:
    """Liquid-glass frame style for grouped sections (credentials, etc.)."""
    return (
        f"QFrame#glassPanel {{"
        f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 rgba(255,255,255, 18),"
        f" stop:0.5 rgba(255,255,255, 10),"
        f" stop:1 rgba(255,255,255, 14));"
        f" border: 1px solid rgba(255,255,255, 35);"
        f" border-top: 1px solid rgba(255,255,255, 90);"
        f" border-radius: {theme_mod.RADIUS_LG}px;"
        f" padding: {theme_mod.SPACE_MD}px; }}"
    )
