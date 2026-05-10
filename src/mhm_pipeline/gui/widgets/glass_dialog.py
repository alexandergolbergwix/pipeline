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

    Text is hard-coded light (#e5e7eb) because the ``GraphBackdrop`` is
    always dark — using ``theme.ui('text')`` would return dark in OS-light
    mode and produce invisible text on the dark glass backdrop.
    """
    return (
        f"QTableView {{"
        f" background: rgba(0,0,0, 90);"
        f" alternate-background-color: rgba(255,255,255, 10);"
        f" color: #e5e7eb;"
        f" gridline-color: rgba(255,255,255, 18);"
        f" border: 1px solid rgba(255,255,255, 22);"
        f" border-radius: {theme_mod.RADIUS_MD}px;"
        f" selection-background-color: rgba(99, 102, 241, 120);"
        f" selection-color: white;"
        f" }}"
        f"QHeaderView::section {{"
        f" background: rgba(255,255,255, 12);"
        f" color: #e5e7eb;"
        f" padding: 6px 8px;"
        f" border: none;"
        f" border-bottom: 1px solid rgba(255,255,255, 22);"
        f" font-weight: 600;"
        f" }}"
        f"QTableView::item {{"
        f" padding: 4px 8px;"
        f" border: none;"
        f" color: #e5e7eb;"
        f" }}"
        f"QTableView::item:selected {{"
        f" color: white;"
        f" }}"
        f"QTableCornerButton::section {{"
        f" background: rgba(255,255,255, 10);"
        f" border: none;"
        f" }}"
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
