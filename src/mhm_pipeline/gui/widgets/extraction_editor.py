"""Editable entity extraction results table.

Displays NER-extracted entities in a QTableView with inline editing:
- Double-click entity text to fix boundaries
- Dropdown to change entity type (PERSON, OWNER, DATE, COLLECTION, etc.)
- Delete button to remove false positives
- Add button for manually entering missed entities
- Save/Revert for persistence

Expert users can correct NER extractions before authority matching and
Wikidata upload, improving data quality.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = [
    "PERSON", "OWNER", "DATE", "COLLECTION",
    "WORK", "FOLIO", "WORK_AUTHOR",
]

TYPE_COLORS: dict[str, str] = {
    "PERSON": "#c7d2fe",
    "OWNER": "#bbf7d0",
    "DATE": "#fed7aa",
    "COLLECTION": "#dbeafe",
    "WORK": "#fecaca",
    "FOLIO": "#fef3c7",
    "WORK_AUTHOR": "#e9d5ff",
}


class EditableEntityModel(QAbstractTableModel):
    """Table model for editable NER entity data."""

    HEADERS = ["Record", "Entity Text", "Type", "Confidence", "Source"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entities: list[dict] = []
        self._original: list[dict] = []

    def load_from_records(self, records: list[dict]) -> None:
        """Flatten NER result records into entity rows."""
        self.beginResetModel()
        self._entities.clear()
        for record in records:
            cn = str(record.get("_control_number", ""))
            for entity in record.get("entities") or []:
                row = {
                    "_control_number": cn,
                    "text": entity.get("person", entity.get("text", "")),
                    "type": entity.get("type", "PERSON"),
                    "confidence": entity.get("confidence", 0.0),
                    "source": entity.get("source", "person_ner"),
                    "role": entity.get("role", ""),
                    "start": entity.get("start", 0),
                    "end": entity.get("end", 0),
                }
                self._entities.append(row)
        self._original = copy.deepcopy(self._entities)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self._entities)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self.HEADERS)

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or index.row() >= len(self._entities):
            return None
        entity = self._entities[index.row()]
        col = index.column()

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == 0:
                return entity["_control_number"]
            if col == 1:
                return entity["text"]
            if col == 2:
                return entity["type"]
            if col == 3:
                return f"{entity['confidence']:.2f}" if entity["confidence"] else ""
            if col == 4:
                return entity["source"]

        if role == Qt.ItemDataRole.BackgroundRole and col == 2:
            from PyQt6.QtGui import QColor  # noqa: PLC0415
            color_hex = TYPE_COLORS.get(entity["type"], "#f3f4f6")
            return QColor(color_hex)

        return None

    def setData(self, index: QModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        entity = self._entities[index.row()]
        col = index.column()
        if col == 1:
            entity["text"] = str(value)
            self.dataChanged.emit(index, index)
            return True
        if col == 2:
            entity["type"] = str(value)
            self.dataChanged.emit(index, index)
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() in (1, 2):
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def add_entity(
        self, control_number: str, text: str, entity_type: str, source: str = "manual",
    ) -> None:
        """Insert a new manually-added entity."""
        row = len(self._entities)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entities.append({
            "_control_number": control_number,
            "text": text,
            "type": entity_type,
            "confidence": 1.0,
            "source": source,
            "role": "",
            "start": 0,
            "end": len(text),
        })
        self.endInsertRows()

    def delete_row(self, row: int) -> None:
        """Remove entity at given row."""
        if 0 <= row < len(self._entities):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._entities.pop(row)
            self.endRemoveRows()

    def to_records(self) -> list[dict]:
        """Reconstruct NER result records from current entity state."""
        by_cn: dict[str, list[dict]] = {}
        for ent in self._entities:
            cn = ent["_control_number"]
            by_cn.setdefault(cn, [])
            # Reconstruct original entity format
            out: dict[str, object] = {
                "text": ent["text"],
                "type": ent["type"],
                "confidence": ent["confidence"],
                "source": ent["source"],
                "start": ent["start"],
                "end": ent["end"],
            }
            if ent["source"] == "person_ner":
                out["person"] = ent["text"]
                out["role"] = ent.get("role", "AUTHOR")
            by_cn[cn].append(out)

        return [
            {"_control_number": cn, "entities": ents}
            for cn, ents in by_cn.items()
        ]

    def is_dirty(self) -> bool:
        """Check if any edits were made."""
        return self._entities != self._original

    def revert(self) -> None:
        """Undo all edits."""
        self.beginResetModel()
        self._entities = copy.deepcopy(self._original)
        self.endResetModel()


class EntityTypeDelegate(QStyledItemDelegate):
    """QComboBox dropdown delegate for the Type column."""

    def createEditor(self, parent: QWidget, option: object, index: QModelIndex) -> QComboBox:  # noqa: N802
        combo = QComboBox(parent)
        combo.addItems(VALID_ENTITY_TYPES)
        return combo

    def setEditorData(self, editor: QComboBox, index: QModelIndex) -> None:  # noqa: N802
        current = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(str(current))
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor: QComboBox, model: QAbstractTableModel, index: QModelIndex) -> None:  # noqa: N802
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class AddEntityDialog(QDialog):
    """Dialog for manually adding a new entity."""

    def __init__(self, control_numbers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Entity")
        self.setMinimumWidth(400)

        layout = QFormLayout(self)

        self.record_combo = QComboBox()
        self.record_combo.addItems(control_numbers)
        layout.addRow("Record:", self.record_combo)

        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("Entity text...")
        layout.addRow("Entity Text:", self.text_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItems(VALID_ENTITY_TYPES)
        layout.addRow("Entity Type:", self.type_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class ExtractionEditor(QWidget):
    """Editable NER entity table with add/delete/save capabilities."""

    entities_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header with stats and action buttons
        header = QHBoxLayout()
        self._stats_label = QLabel("No entities loaded")
        header.addWidget(self._stats_label)
        header.addStretch()

        self._add_btn = QPushButton("+ Add Entity")
        self._add_btn.clicked.connect(self._on_add)
        header.addWidget(self._add_btn)

        self._delete_btn = QPushButton("Delete Selected")
        self._delete_btn.clicked.connect(self._on_delete)
        header.addWidget(self._delete_btn)

        self._revert_btn = QPushButton("Revert")
        self._revert_btn.clicked.connect(self._on_revert)
        header.addWidget(self._revert_btn)

        self._save_btn = QPushButton("Save Changes")
        self._save_btn.setStyleSheet("QPushButton { font-weight: bold; }")
        self._save_btn.clicked.connect(self._on_save)
        header.addWidget(self._save_btn)

        layout.addLayout(header)

        # Search bar
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter entities...")
        self._search_edit.textChanged.connect(self._on_search)
        search_layout.addWidget(self._search_edit)
        layout.addLayout(search_layout)

        # Table
        self._model = EditableEntityModel()
        self._proxy = QSortFilterProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)  # Search all columns

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setItemDelegateForColumn(2, EntityTypeDelegate(self))

        # Column sizing
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self._table)

        self._output_path: Path | None = None
        self._model.dataChanged.connect(self._on_data_changed)

    def load_records(self, records: list[dict], output_path: Path | None = None) -> None:
        """Load NER results for editing."""
        self._model.load_from_records(records)
        self._output_path = output_path
        self._update_stats()

    def _update_stats(self) -> None:
        n = self._model.rowCount()
        dirty = " (modified)" if self._model.is_dirty() else ""
        self._stats_label.setText(f"{n} entities{dirty}")

    def _on_search(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)

    def _on_add(self) -> None:
        cns = sorted({
            self._model._entities[i]["_control_number"]
            for i in range(len(self._model._entities))
        })
        if not cns:
            cns = [""]
        dialog = AddEntityDialog(cns, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            text = dialog.text_edit.text().strip()
            if text:
                self._model.add_entity(
                    dialog.record_combo.currentText(),
                    text,
                    dialog.type_combo.currentText(),
                )
                self._update_stats()
                self.entities_changed.emit()

    def _on_delete(self) -> None:
        indices = self._table.selectionModel().selectedRows()
        if not indices:
            return
        # Delete from bottom up to preserve indices
        source_rows = sorted(
            [self._proxy.mapToSource(idx).row() for idx in indices],
            reverse=True,
        )
        for row in source_rows:
            self._model.delete_row(row)
        self._update_stats()
        self.entities_changed.emit()

    def _on_revert(self) -> None:
        if not self._model.is_dirty():
            return
        reply = QMessageBox.question(
            self, "Revert Changes",
            "Discard all edits and revert to the original extraction results?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._model.revert()
            self._update_stats()

    def _on_save(self) -> None:
        if not self._output_path:
            return
        records = self._model.to_records()
        self._output_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._model._original = copy.deepcopy(self._model._entities)
        self._update_stats()
        logger.info("Saved %d entities to %s", self._model.rowCount(), self._output_path)

    def _on_data_changed(self) -> None:
        self._update_stats()
        self.entities_changed.emit()
