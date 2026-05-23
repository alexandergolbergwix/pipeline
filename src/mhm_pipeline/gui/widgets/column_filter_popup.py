"""Excel-style per-column value filter popup + table wiring helper.

Reused by the NER entities editor and the authority-match editor (and
any other ``QTableView`` whose proxy implements the small protocol
below). Provides:

* :class:`ColumnFilterPopup` — a modal dialog listing distinct values
  from one column with a checkbox per value, a search box, and
  Select-all / Clear-all controls. On Apply the dialog returns the
  checked subset; an empty subset means "no filter on this column".
* :func:`install_column_filters` — wires the table's horizontal
  header so right-clicking any section opens a context menu with
  ``Filter…`` / ``Sort ascending`` / ``Sort descending`` /
  ``Clear filter on this column`` / ``Clear all filters``.

Design choices (see plan `silly-prancing-quiche.md`):

* Right-click trigger (left-click still sorts via Qt's built-in
  ``setSortingEnabled(True)``).
* Filters AND with the existing chip-row dimension filter; both
  pre-existing :class:`AuthorityFilterProxy` and
  :class:`EntityFilterProxy` are extended with a
  ``_column_filters: dict[int, set[str]]`` and a
  ``set_column_filter(col, set)`` method.
* The dialog uses :class:`GlassDialog` so the liquid-glass backdrop
  applies (CLAUDE.md rule 37).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.widgets.glass_dialog import GlassDialog

# Sentinel rendered in place of the empty string in the value list so
# the user can explicitly include / exclude blank cells.
BLANK_LABEL = "(blank)"


class ColumnFilteredProxy(Protocol):
    """Protocol the proxy must implement to wire into the helper."""

    def set_column_filter(self, column: int, values: set[str]) -> None: ...
    def clear_all_column_filters(self) -> None: ...
    def column_filter(self, column: int) -> set[str]: ...


# ─────────────────────────────────────────────────────────────────────
# Popup widget
# ─────────────────────────────────────────────────────────────────────


class ColumnFilterPopup(GlassDialog):
    """Excel-style filter popup. Lists distinct values with checkboxes.

    Emits :pyattr:`selection_changed` on Apply with the set of values
    the user wants to KEEP. An empty set means "no filter" (all rows
    accepted on this column). Cancel emits nothing.
    """

    selection_changed = pyqtSignal(set)

    def __init__(
        self,
        column_name: str,
        values: list[str],
        selected: set[str] | None = None,
        counts: dict[str, int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._values: list[str] = list(values)
        self._counts: dict[str, int] = counts or {}
        self._initial_selected: set[str] = set(selected or set())

        self.setWindowTitle(f"Filter — {column_name}")
        self.setMinimumSize(320, 400)
        self.setWindowModality(Qt.WindowModality.WindowModal)

        self._build_ui(column_name)
        self._populate(self._values)

    # ── construction ────────────────────────────────────────────────

    def _build_ui(self, column_name: str) -> None:
        outer = QVBoxLayout(self.glass_content)
        outer.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD,
        )
        outer.setSpacing(theme.SPACE_SM)

        title = QLabel(f"<b>Filter values in column:</b> {column_name}")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setStyleSheet(f"color:{theme.ui('text')};")
        outer.addWidget(title)

        # Search line edit
        self._search = QLineEdit()
        self._search.setPlaceholderText("Type to filter the list…")
        self._search.textChanged.connect(self._on_search_text)
        outer.addWidget(self._search)

        # Select all / Clear all row
        button_row = QHBoxLayout()
        select_all_btn = QPushButton("Select all")
        select_all_btn.setProperty("flat", True)
        select_all_btn.clicked.connect(self._on_select_all)
        clear_all_btn = QPushButton("Clear all")
        clear_all_btn.setProperty("flat", True)
        clear_all_btn.clicked.connect(self._on_clear_all)
        button_row.addWidget(select_all_btn)
        button_row.addWidget(clear_all_btn)
        button_row.addStretch(1)
        outer.addLayout(button_row)

        # Value list
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {theme.ui('panel_bg')};"
            f" color: {theme.ui('text')};"
            f" border: 1px solid {theme.ui('border')};"
            f" border-radius: {theme.RADIUS_SM}px; }}"
            f"QListWidget::item {{ padding: 4px 6px; }}"
        )
        outer.addWidget(self._list, 1)

        # Apply / Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        apply_btn = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_btn is not None:
            apply_btn.setDefault(True)
            apply_btn.clicked.connect(self._on_apply)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.clicked.connect(self.reject)
        outer.addWidget(buttons)

    def _populate(self, values: list[str]) -> None:
        """Build list items from ``values``. Honours the initial-selected
        set: if it is empty (no current filter), every box starts
        checked; if it has entries, only those values are pre-checked."""
        self._list.clear()
        all_unchecked_initially = bool(self._initial_selected)
        for raw in values:
            label = self._format_label(raw)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, raw)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if all_unchecked_initially:
                state = (
                    Qt.CheckState.Checked
                    if raw in self._initial_selected
                    else Qt.CheckState.Unchecked
                )
            else:
                state = Qt.CheckState.Checked
            item.setCheckState(state)
            self._list.addItem(item)

    def _format_label(self, raw: str) -> str:
        display = BLANK_LABEL if raw == "" else raw
        count = self._counts.get(raw)
        if count is not None:
            return f"{display}  ({count})"
        return display

    # ── slots ───────────────────────────────────────────────────────

    def _on_search_text(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None:
                continue
            haystack = str(item.data(Qt.ItemDataRole.UserRole) or "").lower()
            visible = needle in haystack or not needle
            item.setHidden(not visible)

    def _on_select_all(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None or item.isHidden():
                continue
            item.setCheckState(Qt.CheckState.Checked)

    def _on_clear_all(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None or item.isHidden():
                continue
            item.setCheckState(Qt.CheckState.Unchecked)

    def _on_apply(self) -> None:
        checked: set[str] = set()
        unchecked_any = False
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None:
                continue
            raw = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if item.checkState() == Qt.CheckState.Checked:
                checked.add(raw)
            else:
                unchecked_any = True
        # If every value is checked (no exclusions) treat as "no filter".
        if not unchecked_any:
            self.selection_changed.emit(set())
        else:
            self.selection_changed.emit(checked)
        self.accept()


# ─────────────────────────────────────────────────────────────────────
# Wiring helper — attaches a context menu to the table header
# ─────────────────────────────────────────────────────────────────────


def install_column_filters(
    table: QTableView,
    proxy: Any,                                         # ColumnFilteredProxy
    distinct_values_for: Callable[[int], list[str]],
    *,
    counts_for: Callable[[int], dict[str, int]] | None = None,
    column_label_for: Callable[[int], str] | None = None,
    on_filter_changed: Callable[[], None] | None = None,
) -> None:
    """Wire the *table*'s horizontal header for the right-click filter UX.

    Parameters
    ----------
    table:
        The :class:`QTableView` whose header gets the context menu.
    proxy:
        The :class:`QSortFilterProxyModel` implementing
        :class:`ColumnFilteredProxy`. The same proxy must already be
        set on the table.
    distinct_values_for:
        Callable that returns the *sorted* list of distinct string
        values found in the column. Built from the source model on
        demand so the popup always sees fresh data.
    counts_for:
        Optional callable returning a ``{value: count}`` dict for
        rendering ``"value (12)"`` labels.
    column_label_for:
        Optional callable returning a human-readable column name. If
        omitted, falls back to the header's ``DisplayRole`` text.
    on_filter_changed:
        Optional no-arg callback fired after every filter change so
        the surrounding panel can update status text (e.g. the
        ``"showing 47 of 200"`` count).
    """
    header = table.horizontalHeader()
    header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _resolve_label(column: int) -> str:
        if column_label_for is not None:
            return column_label_for(column)
        model = table.model()
        if model is None:
            return f"Column {column}"
        return str(
            model.headerData(
                column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole,
            ) or f"Column {column}"
        )

    def _open_filter(column: int) -> None:
        values = distinct_values_for(column)
        counts = counts_for(column) if counts_for is not None else None
        popup = ColumnFilterPopup(
            column_name=_resolve_label(column),
            values=values,
            selected=proxy.column_filter(column),
            counts=counts,
            parent=table.window(),
        )

        def _apply(selected: set[str]) -> None:
            # Empty set = popup says "no filter". A non-empty set is the
            # exact include list (one or more checkboxes checked).
            proxy.set_column_filter(column, selected)
            if on_filter_changed is not None:
                on_filter_changed()

        popup.selection_changed.connect(_apply)
        popup.exec()

    def _on_context_menu(pos: QPoint) -> None:
        column = header.logicalIndexAt(pos)
        if column < 0:
            return
        menu = QMenu(table)

        filter_action = QAction("Filter…", menu)
        filter_action.triggered.connect(lambda _checked=False, c=column: _open_filter(c))
        menu.addAction(filter_action)

        menu.addSeparator()

        sort_asc = QAction("Sort ascending", menu)
        sort_asc.triggered.connect(
            lambda _checked=False, c=column: table.sortByColumn(
                c, Qt.SortOrder.AscendingOrder,
            )
        )
        menu.addAction(sort_asc)

        sort_desc = QAction("Sort descending", menu)
        sort_desc.triggered.connect(
            lambda _checked=False, c=column: table.sortByColumn(
                c, Qt.SortOrder.DescendingOrder,
            )
        )
        menu.addAction(sort_desc)

        menu.addSeparator()

        # Per-column clear (greyed out when nothing to clear)
        clear_this = QAction("Clear filter on this column", menu)
        clear_this.setEnabled(bool(proxy.column_filter(column)))

        def _clear_col(_checked: bool = False, c: int = column) -> None:
            proxy.set_column_filter(c, set())
            if on_filter_changed is not None:
                on_filter_changed()

        clear_this.triggered.connect(_clear_col)
        menu.addAction(clear_this)

        clear_all = QAction("Clear all column filters", menu)

        def _clear_all(_checked: bool = False) -> None:
            proxy.clear_all_column_filters()
            if on_filter_changed is not None:
                on_filter_changed()

        clear_all.triggered.connect(_clear_all)
        menu.addAction(clear_all)

        menu.exec(header.mapToGlobal(pos))

    header.customContextMenuRequested.connect(_on_context_menu)
    if hasattr(header, "setToolTip"):
        header.setToolTip(
            "Right-click any column header to filter, sort, or clear filters."
        )


# ─────────────────────────────────────────────────────────────────────
# Helpers for proxies — distinct-value collection from a flat list
# ─────────────────────────────────────────────────────────────────────


def distinct_values_from_rows(
    rows: list[dict],
    accessor: Callable[[dict], str],
) -> list[str]:
    """Return the sorted distinct string values of ``accessor(row)``.

    The empty-string bucket is preserved so the popup can render
    ``(blank)`` as an explicit choice; sorted ASCII-asc + empty-first
    for deterministic UI ordering.
    """
    seen: set[str] = set()
    for row in rows:
        seen.add(str(accessor(row) or ""))
    return sorted(seen, key=lambda s: (s != "", s.lower(), s))


def counts_from_rows(
    rows: list[dict],
    accessor: Callable[[dict], str],
) -> dict[str, int]:
    """Return ``{value: occurrence_count}`` over ``accessor(row)``."""
    out: dict[str, int] = {}
    for row in rows:
        key = str(accessor(row) or "")
        out[key] = out.get(key, 0) + 1
    return out


__all__ = [
    "BLANK_LABEL",
    "ColumnFilterPopup",
    "ColumnFilteredProxy",
    "install_column_filters",
    "distinct_values_from_rows",
    "counts_from_rows",
]
