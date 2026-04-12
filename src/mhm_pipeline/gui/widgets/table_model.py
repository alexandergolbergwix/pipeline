"""Memory-efficient table model for large pipeline result datasets.

Uses QAbstractTableModel + QTableView (model/view pattern) instead of
QTableWidget. Only renders visible rows — handles 100K+ rows instantly
with zero per-row memory overhead.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)


class PipelineTableModel(QAbstractTableModel):
    """Flat table model backed by a list of tuples. No widget items created."""

    def __init__(
        self,
        headers: list[str],
        parent: QTableView | None = None,
    ) -> None:
        super().__init__(parent)
        self._headers = headers
        self._rows: list[tuple[str, ...]] = []

    def load(self, rows: list[tuple[str, ...]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows = []
        self.endResetModel()

    def row_at(self, row: int) -> tuple[str, ...] | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._headers)

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._rows[index.row()]
        col = index.column()
        return str(row[col]) if col < len(row) else None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self._headers[section] if section < len(self._headers) else None
        return None


class PipelineTableView(QWidget):
    """Ready-to-use searchable/sortable table for large datasets.

    Wraps QTableView + PipelineTableModel + QSortFilterProxyModel
    with a search bar and row count label.

    Usage::

        view = PipelineTableView(headers=["Name", "Source", "ID", "Matched"])
        view.load([("Maimonides", "VIAF", "100185495", "Yes"), ...])
    """

    def __init__(
        self,
        headers: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Search bar + count
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter rows...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_filter)
        top_row.addWidget(self._search)

        self._count_label = QLabel("")
        top_row.addWidget(self._count_label)
        layout.addLayout(top_row)

        # Model + proxy + view
        self._model = PipelineTableModel(headers)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.setWordWrap(False)

        # Fixed row heights for performance
        vh = self._table.verticalHeader()
        if vh:
            vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            vh.setDefaultSectionSize(26)
            vh.setVisible(False)

        # Interactive column widths (not ResizeToContents which is slow)
        hh = self._table.horizontalHeader()
        if hh:
            hh.setStretchLastSection(True)
            hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        layout.addWidget(self._table)

    def load(self, rows: list[tuple[str, ...]]) -> None:
        """Load data — instant even for 100K+ rows."""
        self._model.load(rows)
        self._update_count()

    def clear(self) -> None:
        self._model.clear()
        self._update_count()

    def _on_filter(self, text: str) -> None:
        from PyQt6.QtCore import QRegularExpression

        self._proxy.setFilterRegularExpression(QRegularExpression(QRegularExpression.escape(text)))
        self._update_count()

    def _update_count(self) -> None:
        total = self._model.rowCount()
        visible = self._proxy.rowCount()
        if total == visible:
            self._count_label.setText(f"{total:,} rows")
        else:
            self._count_label.setText(f"{visible:,} of {total:,}")
