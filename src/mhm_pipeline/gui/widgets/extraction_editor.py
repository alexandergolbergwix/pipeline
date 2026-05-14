"""Editable entity extraction results table.

Primary interaction surface for reviewing and correcting NER output before
Wikidata upload. Features:

- **Inline editing** — double-click text / type / role to fix extraction errors.
- **Per-row approval** — explicit approval column; no entity is uploaded
  without an expert tick.
- **View source** — per-row action opens the original note text with the
  entity span highlighted.
- **Auto-approve rules** — multi-condition builder (confidence > 0.7 AND
  type = "DATE" AND role NOT IN [...]) for bulk approval.
- **Theme-aware type colours** — no more white text on bright backgrounds.
- **Filter + search** — source/type/role chips plus free-text search.

The editor is the sole interaction surface for NER results; the older
full-screen "View Results" reader has been retired in favour of this
richer table.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QAbstractItemModel,
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = [
    "PERSON",
    "OWNER",
    "DATE",
    "COLLECTION",
    "WORK",
    "FOLIO",
    "WORK_AUTHOR",
    "PLACE",
    "ORG",
]

VALID_ROLES = ["", "AUTHOR", "SCRIBE", "OWNER", "CENSOR", "COMMENTATOR", "TRANSCRIBER"]

VALID_SOURCES = [
    "person_ner",
    "provenance_ner",
    "contents_ner",
    "colophon_ml",
    "genre_ml",
    "manual",
]

# Synthetic-only sources that originate in record-level channels
# (``ml_colophon_sentences`` / ``ml_genres``) rather than in the
# ``entities`` list. The editor surfaces them as rows so reviewers can
# approve / reject classifier predictions; ``to_records`` routes them
# back to the original channels instead of polluting ``entities``.
SYNTHETIC_SOURCES: frozenset[str] = frozenset({"colophon_ml", "genre_ml"})


def _type_colors() -> dict[str, tuple[str, str]]:
    """Return ``{type: (bg, fg)}`` pairs appropriate for the current theme.

    Dark mode uses deep saturated hues with white text; light mode uses soft
    pastels with a dark charcoal text. No more white-on-bright.
    """
    from mhm_pipeline.gui import theme  # noqa: PLC0415

    if theme.is_dark():
        return {
            "PERSON":      ("#3730a3", "#e0e7ff"),
            "OWNER":       ("#14532d", "#d1fae5"),
            "DATE":        ("#7c2d12", "#fed7aa"),
            "COLLECTION":  ("#1e3a8a", "#dbeafe"),
            "WORK":        ("#7f1d1d", "#fecaca"),
            "FOLIO":       ("#78350f", "#fef3c7"),
            "WORK_AUTHOR": ("#5b21b6", "#ede9fe"),
            "PLACE":       ("#064e3b", "#a7f3d0"),
            "ORG":         ("#1e293b", "#e2e8f0"),
        }
    return {
        "PERSON":      ("#e0e7ff", "#3730a3"),
        "OWNER":       ("#d1fae5", "#14532d"),
        "DATE":        ("#fed7aa", "#7c2d12"),
        "COLLECTION":  ("#dbeafe", "#1e3a8a"),
        "WORK":        ("#fecaca", "#7f1d1d"),
        "FOLIO":       ("#fef3c7", "#78350f"),
        "WORK_AUTHOR": ("#ede9fe", "#5b21b6"),
        "PLACE":       ("#a7f3d0", "#064e3b"),
        "ORG":         ("#e2e8f0", "#1e293b"),
    }


# ────────────────────────────────────────────────────────────────────────────
# Filter proxy (source / type / role + text search)
# ────────────────────────────────────────────────────────────────────────────

class EntityFilterProxy(QSortFilterProxyModel):
    """Proxy filtering by source/type/role, plus the default text search."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source_filter: set[str] = set()
        self.type_filter: set[str] = set()
        self.role_filter: set[str] = set()

    def set_dimension_filters(
        self,
        sources: set[str],
        types: set[str],
        roles: set[str],
    ) -> None:
        self.source_filter = set(sources)
        self.type_filter = set(types)
        self.role_filter = set(roles)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, EditableEntityModel):
            return True
        if source_row >= len(model._entities):
            return True
        ent = model._entities[source_row]
        if self.source_filter and str(ent.get("source") or "") not in self.source_filter:
            return False
        if self.type_filter and str(ent.get("type") or "") not in self.type_filter:
            return False
        if self.role_filter:
            r = str(ent.get("role") or "")
            if r and r not in self.role_filter:
                return False
        return super().filterAcceptsRow(source_row, source_parent)


# ────────────────────────────────────────────────────────────────────────────
# Model
# ────────────────────────────────────────────────────────────────────────────

# Column indices (kept as constants so delegates + callers stay consistent)
COL_RECORD = 0
COL_TEXT = 1
COL_TYPE = 2
COL_ROLE = 3
COL_CONF = 4
MODEL_CONF = 5
COL_SOURCE = 6
COL_APPROVED = 7
COL_ACTIONS = 8


class EditableEntityModel(QAbstractTableModel):
    """Table model for editable NER entity data.

    Columns: Record · Entity · Type · Role · Conf. · Model Conf. · Source · Approved · View.
    ``Conf.`` is the keyword-classifier signal (hardcoded 0.60 / 0.85 ceilings)
    that Stage 3 guards key on. ``Model Conf.`` is the real softmax probability
    from the BIO classifier — surfacing it lets reviewers write auto-approve
    rules against the actual model score rather than the keyword bucket. The
    column is read-only at the table layer because the value is computed by
    the model, not user-set.

    The ``_records_by_cn`` dict keeps a reference to the original NER result
    record for each control number so the "View source" action can pull the
    full note text for highlighting.
    """

    HEADERS = [
        "Record", "Entity", "Type", "Role", "Conf.", "Model Conf.",
        "Source", "Approved", " ",
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entities: list[dict] = []
        self._original: list[dict] = []
        self._records_by_cn: dict[str, dict] = {}

    # ── Loading ──────────────────────────────────────────────────────────

    def load_from_records(self, records: list[dict]) -> None:
        """Flatten NER result records into entity rows.

        Real NER spans come from ``record["entities"]``. Classifier
        predictions stored in dedicated channels (``ml_colophon_sentences``
        and ``ml_genres``) are surfaced as virtual rows tagged
        ``source="colophon_ml"`` / ``"genre_ml"`` so reviewers can
        approve them. ``to_records`` routes virtual rows back to the
        channels rather than into ``entities``.
        """
        self.beginResetModel()
        self._entities.clear()
        self._records_by_cn = {
            str(r.get("_control_number", "")): r for r in records
        }
        for record in records:
            cn = str(record.get("_control_number", ""))
            for entity in record.get("entities") or []:
                row = {
                    "_control_number": cn,
                    "text": entity.get("person", entity.get("text", "")),
                    "type": entity.get("type", "PERSON"),
                    "confidence": float(entity.get("confidence", 0.0) or 0.0),
                    "model_confidence": float(
                        entity.get("model_confidence", 0.0) or 0.0
                    ),
                    "source": entity.get("source", "person_ner"),
                    "role": entity.get("role", "") or "",
                    "start": int(entity.get("start", 0) or 0),
                    "end": int(entity.get("end", 0) or 0),
                    "approved": bool(entity.get("approved", False)),
                }
                self._entities.append(row)
            for sentence in record.get("ml_colophon_sentences") or []:
                self._entities.append({
                    "_control_number": cn,
                    "text": str(sentence),
                    "type": "COLOPHON",
                    "confidence": 0.0,
                    "model_confidence": 0.0,
                    "source": "colophon_ml",
                    "role": "",
                    "start": 0,
                    "end": 0,
                    "approved": False,
                })
            for prediction in record.get("ml_genres") or []:
                if not isinstance(prediction, dict):
                    continue
                self._entities.append({
                    "_control_number": cn,
                    "text": str(prediction.get("label") or ""),
                    "type": "GENRE",
                    "confidence": float(prediction.get("confidence") or 0.0),
                    "model_confidence": 0.0,
                    "source": "genre_ml",
                    "role": "",
                    "start": 0,
                    "end": 0,
                    "approved": False,
                })
        self._original = copy.deepcopy(self._entities)
        self.endResetModel()

    def source_text_for(self, row: int) -> tuple[str, str, int, int]:
        """Return (source_text, entity_text, start, end) for a given row.

        ``source_text`` is the full note / provenance / contents text the NER
        operated on. For person_ner entities the ``start/end`` offsets are
        reliable; for others we fall back to substring search.
        """
        if not 0 <= row < len(self._entities):
            return ("", "", 0, 0)
        ent = self._entities[row]
        rec = self._records_by_cn.get(ent["_control_number"], {})
        full = str(rec.get("text", "") or "")
        start = int(ent.get("start") or 0)
        end = int(ent.get("end") or 0)
        et = str(ent.get("text") or "")
        # Validate offsets — if they don't extract the entity text, fall back
        # to substring search so highlighting still works.
        if not (0 <= start < end <= len(full)) or full[start:end] != et:
            idx = full.find(et) if et else -1
            if idx >= 0:
                start, end = idx, idx + len(et)
            else:
                start, end = 0, 0
        return (full, et, start, end)

    # ── QAbstractTableModel API ──────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self._entities)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self.HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or index.row() >= len(self._entities):
            return None
        ent = self._entities[index.row()]
        col = index.column()

        # Display / edit values
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == COL_RECORD:
                return ent["_control_number"]
            if col == COL_TEXT:
                return ent["text"]
            if col == COL_TYPE:
                return ent["type"]
            if col == COL_ROLE:
                return ent.get("role", "")
            if col == COL_CONF:
                c = ent.get("confidence", 0.0)
                return f"{c:.2f}" if c else ""
            if col == MODEL_CONF:
                m = ent.get("model_confidence", 0.0)
                return f"{m:.2f}" if m else ""
            if col == COL_SOURCE:
                return ent.get("source", "")

        # Sort role: return the underlying numeric/string value so the column
        # sorts naturally rather than lexicographically.
        if role == Qt.ItemDataRole.UserRole:
            if col == COL_CONF:
                return ent.get("confidence", 0.0)
            if col == MODEL_CONF:
                return ent.get("model_confidence", 0.0)
            if col == COL_APPROVED:
                return int(bool(ent.get("approved", False)))
            return self.data(index, Qt.ItemDataRole.DisplayRole)

        # Check state for approval column
        if role == Qt.ItemDataRole.CheckStateRole and col == COL_APPROVED:
            return (
                Qt.CheckState.Checked if ent.get("approved", False)
                else Qt.CheckState.Unchecked
            )

        # Background colouring on the type cell — theme-aware.
        if role == Qt.ItemDataRole.BackgroundRole and col == COL_TYPE:
            bg, _fg = _type_colors().get(ent["type"], ("#3f3f46", "#f3f4f6"))
            return QColor(bg)
        if role == Qt.ItemDataRole.ForegroundRole and col == COL_TYPE:
            _bg, fg = _type_colors().get(ent["type"], ("#3f3f46", "#f3f4f6"))
            return QColor(fg)

        # Approved row emphasis — subtle green wash
        if role == Qt.ItemDataRole.BackgroundRole and ent.get("approved", False):
            from mhm_pipeline.gui import theme  # noqa: PLC0415
            if theme.is_dark():
                return QColor(22, 163, 74, 28)
            return QColor(22, 163, 74, 18)

        return None

    def setData(  # noqa: N802
        self,
        index: QModelIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid():
            return False
        col = index.column()
        row = index.row()
        if row >= len(self._entities):
            return False
        ent = self._entities[row]

        if role == Qt.ItemDataRole.CheckStateRole and col == COL_APPROVED:
            ent["approved"] = (Qt.CheckState(value) == Qt.CheckState.Checked)
            self.dataChanged.emit(index, index.siblingAtColumn(COL_ACTIONS))
            return True

        if role != Qt.ItemDataRole.EditRole:
            return False
        if col == COL_TEXT:
            ent["text"] = str(value)
            self.dataChanged.emit(index, index)
            return True
        if col == COL_TYPE:
            ent["type"] = str(value)
            self.dataChanged.emit(index, index)
            return True
        if col == COL_ROLE:
            ent["role"] = str(value)
            self.dataChanged.emit(index, index)
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        # Entity text is edited via the per-row ✎ button (popup) rather than
        # inline — inline editing of Hebrew strings in a narrow cell is awkward
        # and users kept triggering it by accident. Type + Role remain inline
        # editable because their combos are short enum lists.
        base = super().flags(index)
        col = index.column()
        if col in (COL_TYPE, COL_ROLE):
            return base | Qt.ItemFlag.ItemIsEditable
        if col == COL_APPROVED:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        if col == MODEL_CONF:
            # Model-computed score; never user-editable.
            return base & ~Qt.ItemFlag.ItemIsEditable
        return base

    # ── Mutators ─────────────────────────────────────────────────────────

    def add_entity(
        self,
        control_number: str,
        text: str,
        entity_type: str,
        source: str = "manual",
    ) -> None:
        row = len(self._entities)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entities.append({
            "_control_number": control_number,
            "text": text,
            "type": entity_type,
            "confidence": 1.0,
            "model_confidence": 1.0,
            "source": source,
            "role": "",
            "start": 0,
            "end": len(text),
            "approved": True,  # manual additions are implicitly approved
        })
        self.endInsertRows()

    def delete_row(self, row: int) -> None:
        if 0 <= row < len(self._entities):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._entities.pop(row)
            self.endRemoveRows()

    def set_approved_bulk(self, source_rows: list[int], approved: bool) -> int:
        """Bulk-toggle approval on a list of source-model rows. Returns count."""
        if not source_rows:
            return 0
        changed = 0
        for r in source_rows:
            if 0 <= r < len(self._entities):
                if self._entities[r].get("approved", False) != approved:
                    self._entities[r]["approved"] = approved
                    changed += 1
        if changed:
            # Emit a single blanket change so the view re-renders
            tl = self.index(0, 0)
            br = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(tl, br)
        return changed

    # ── Serialisation ────────────────────────────────────────────────────

    def to_approved_records(self) -> list[dict]:
        """Like :py:meth:`to_records` but keeps only entities with ``approved=True``.

        This is what downstream stages (authority → RDF → Wikidata) read.
        Empty records (no approved entities) are still emitted so the
        control-number skeleton survives for the authority join.
        """
        saved = self._entities
        self._entities = [e for e in self._entities if e.get("approved", False)]
        try:
            return self.to_records()
        finally:
            self._entities = saved

    def to_records(self) -> list[dict]:
        """Reconstruct NER-style records from the current entity state.

        Real NER rows go back into ``record["entities"]``. Synthetic
        classifier rows (``source`` in :data:`SYNTHETIC_SOURCES`) round-
        trip into the dedicated channels — colophon rows back to
        ``ml_colophon_sentences``, genre rows back to ``ml_genres``.
        Only approved synthetic rows are kept on save so the user's
        rejections from the GUI flow downstream.
        """
        by_cn_entities: dict[str, list[dict]] = {}
        by_cn_colophons: dict[str, list[str]] = {}
        by_cn_genres: dict[str, list[dict[str, Any]]] = {}
        seen_cns: set[str] = set()

        for ent in self._entities:
            cn = ent["_control_number"]
            seen_cns.add(cn)
            source = ent["source"]
            if source == "colophon_ml":
                if ent.get("approved", False):
                    by_cn_colophons.setdefault(cn, []).append(str(ent["text"]))
                continue
            if source == "genre_ml":
                if ent.get("approved", False):
                    by_cn_genres.setdefault(cn, []).append({
                        "label": str(ent["text"]),
                        "confidence": float(ent.get("confidence") or 0.0),
                    })
                continue
            by_cn_entities.setdefault(cn, [])
            out: dict[str, Any] = {
                "text": ent["text"],
                "type": ent["type"],
                "confidence": ent["confidence"],
                "source": source,
                "start": ent["start"],
                "end": ent["end"],
                "approved": bool(ent.get("approved", False)),
            }
            # Preserve the real softmax probability so downstream stages
            # (e.g. Stage 3 auto-approve audits) can key on it.
            if "model_confidence" in ent:
                out["model_confidence"] = ent["model_confidence"]
            if source == "person_ner":
                out["person"] = ent["text"]
                out["role"] = ent.get("role", "AUTHOR")
            elif ent.get("role"):
                out["role"] = ent["role"]
            by_cn_entities[cn].append(out)

        # Merge over the cached source records so unrelated keys
        # (text, catalog_references, provenance_inscriptions, …) survive.
        merged: list[dict] = []
        all_cns = seen_cns | set(self._records_by_cn.keys())
        for cn in all_cns:
            base = copy.deepcopy(self._records_by_cn.get(cn, {"_control_number": cn}))
            base["_control_number"] = cn
            base["entities"] = by_cn_entities.get(cn, [])
            base["ml_colophon_sentences"] = by_cn_colophons.get(cn, [])
            base["ml_genres"] = by_cn_genres.get(cn, [])
            merged.append(base)
        return merged

    def is_dirty(self) -> bool:
        return self._entities != self._original

    def revert(self) -> None:
        self.beginResetModel()
        self._entities = copy.deepcopy(self._original)
        self.endResetModel()


# ────────────────────────────────────────────────────────────────────────────
# Delegates (type + role dropdowns)
# ────────────────────────────────────────────────────────────────────────────

class _ComboDelegate(QStyledItemDelegate):
    """Enum-column delegate: QComboBox editor + painted ▾ drop indicator.

    Paints the default cell content (text, foreground/background from the
    model), then overlays a small chevron on the right edge to tell the
    user this cell is a dropdown. The editor appears on single-selected
    click or double-click (both are enabled in the view's edit triggers).
    """

    def __init__(self, items: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items = items

    def createEditor(  # noqa: N802
        self, parent: QWidget | None, option: object, index: QModelIndex,
    ) -> QComboBox:
        combo = QComboBox(parent)
        combo.addItems(self._items)
        # Opening the dropdown immediately makes the widget feel like a
        # first-class enum control (the user doesn't have to click twice).
        try:
            from PyQt6.QtCore import QTimer  # noqa: PLC0415
            QTimer.singleShot(0, combo.showPopup)
        except Exception:
            pass
        return combo

    def setEditorData(self, editor: QWidget | None, index: QModelIndex) -> None:  # noqa: N802
        if not isinstance(editor, QComboBox):
            return
        current = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(str(current))
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(  # noqa: N802
        self,
        editor: QWidget | None,
        model: QAbstractItemModel | None,
        index: QModelIndex,
    ) -> None:
        if not isinstance(editor, QComboBox) or model is None:
            return
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def paint(self, painter: Any, option: Any, index: QModelIndex) -> None:  # noqa: N802
        """Draw the default cell then overlay a ▾ hint on the right edge."""
        # Let the base class render the value + model-supplied bg/fg first
        super().paint(painter, option, index)
        try:
            from PyQt6.QtCore import QRect  # noqa: PLC0415
            rect: QRect = option.rect
            painter.save()
            # Foreground colour mirrors the model's text colour when the
            # model supplies one (e.g. theme-aware type cells), otherwise
            # falls back to the palette text colour.
            fg = index.data(Qt.ItemDataRole.ForegroundRole)
            if fg is not None:
                try:
                    painter.setPen(fg if hasattr(fg, "red") else QColor(str(fg)))
                except Exception:
                    painter.setPen(option.palette.text().color())
            else:
                painter.setPen(option.palette.text().color())
            chevron_rect = QRect(
                rect.right() - 16, rect.top(), 14, rect.height(),
            )
            painter.drawText(
                chevron_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                "▾",
            )
            painter.restore()
        except Exception:
            # Any paint error shouldn't break the table rendering; just
            # skip the chevron for this cell.
            pass


class EntityTypeDelegate(_ComboDelegate):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(VALID_ENTITY_TYPES, parent)


class EntityRoleDelegate(_ComboDelegate):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(VALID_ROLES, parent)


# ────────────────────────────────────────────────────────────────────────────
# Source-view dialog
# ────────────────────────────────────────────────────────────────────────────

class SourceViewDialog(QDialog):
    """Show the original source text of a record with one entity highlighted."""

    def __init__(
        self,
        control_number: str,
        source_text: str,
        entity_text: str,
        start: int,
        end: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415
        from mhm_pipeline.gui.widgets.glass_dialog import install_glass_backdrop  # noqa: PLC0415

        self.setWindowTitle(f"Source — {control_number}")
        self.resize(760, 520)

        _content = install_glass_backdrop(self)
        layout = QVBoxLayout(_content)
        layout.setContentsMargins(theme.SPACE_LG, theme.SPACE_LG,
                                  theme.SPACE_LG, theme.SPACE_LG)
        layout.setSpacing(theme.SPACE_MD)

        header = QLabel(
            f"<span style='font-weight:600'>Entity:</span> "
            f"<span style='background:{theme.ui('highlight')}; padding:1px 6px; "
            f"border-radius:4px;'>{entity_text}</span>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(source_text or "(no source text available for this record)")

        # Highlight the span (offset-based when reliable, otherwise string-
        # search fallback) by applying a QTextCharFormat.
        if source_text and entity_text:
            self._highlight_all(editor, source_text, entity_text, start, end)

        layout.addWidget(editor, stretch=1)

        # Close button
        close = QPushButton("Close")
        close.setStyleSheet(theme.button_style())
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)

    def _highlight_all(
        self,
        editor: QTextEdit,
        full: str,
        entity_text: str,
        start: int,
        end: int,
    ) -> None:
        """Apply a highlight format at the provided offsets plus every other
        substring match of the same entity text in the document.
        """
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        fmt = QTextCharFormat()
        if theme.is_dark():
            fmt.setBackground(QColor(250, 204, 21, 90))
            fmt.setForeground(QColor(250, 204, 21))
        else:
            fmt.setBackground(QColor(253, 224, 71))
            fmt.setForeground(QColor(120, 53, 15))
        fmt.setFontWeight(700)

        cursor = editor.textCursor()
        cursor.beginEditBlock()

        # Primary span (offset-based) — apply in-place
        if 0 <= start < end <= len(full):
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(fmt)

        # Additional matches via substring search
        idx = 0
        et = entity_text
        while True:
            idx = full.find(et, idx)
            if idx == -1:
                break
            if idx != start:  # don't re-format the primary span
                cursor.setPosition(idx)
                cursor.setPosition(idx + len(et), QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(fmt)
            idx += max(1, len(et))

        cursor.endEditBlock()

        # Jump the viewport to the primary span
        if start >= 0:
            cursor.setPosition(start)
            editor.setTextCursor(cursor)
            editor.ensureCursorVisible()


# ────────────────────────────────────────────────────────────────────────────
# Auto-approve rule builder
# ────────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────────
# Entity-text edit dialog (opened by the per-row ✎ button)
# ────────────────────────────────────────────────────────────────────────────


class EntityTextEditDialog(QDialog):
    """Popup to edit a single entity's ``text`` value.

    Inline cell editing was awkward for Hebrew text (small box, no RTL cue,
    accidental triggering). This dialog gives the user a full multi-line
    editor with the record's source text shown alongside as context so
    they can confirm the correct span.
    """

    def __init__(
        self,
        current_text: str,
        source_text: str,
        control_number: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415
        from mhm_pipeline.gui.widgets.glass_dialog import install_glass_backdrop  # noqa: PLC0415

        self.setWindowTitle(f"Edit entity text — {control_number}")
        self.resize(620, 360)

        _content = install_glass_backdrop(self)
        layout = QVBoxLayout(_content)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        # The editable text field — multi-line so long colophon sentences are
        # reachable without truncation.
        caption = QLabel("Entity text")
        caption.setStyleSheet(theme.minicaps_label_style())
        layout.addWidget(caption)

        self._edit = QTextEdit()
        self._edit.setPlainText(current_text)
        self._edit.setAcceptRichText(False)
        self._edit.setTabChangesFocus(True)
        # Limit height so the source-context panel below is always visible
        self._edit.setMinimumHeight(90)
        self._edit.setMaximumHeight(160)
        layout.addWidget(self._edit)

        if source_text:
            ctx_caption = QLabel("Source text (read-only)")
            ctx_caption.setStyleSheet(theme.minicaps_label_style())
            layout.addWidget(ctx_caption)
            ctx = QTextEdit()
            ctx.setPlainText(source_text)
            ctx.setReadOnly(True)
            layout.addWidget(ctx, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(theme.ghost_button_style())
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(theme.success_btn_style())
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self.accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

    def value(self) -> str:
        return self._edit.toPlainText().strip()


_AUTO_FIELDS: list[str] = ["confidence", "model_confidence", "type", "role", "source"]
_NUMERIC_OPS: list[str] = [">", ">=", "=", "<=", "<", "≠"]
_STRING_OPS: list[str] = ["=", "≠", "in", "not in"]

# Map enum-type fields to the closed set of legal values the user may pick.
_FIELD_OPTIONS: dict[str, list[str]] = {
    "type": VALID_ENTITY_TYPES,
    "role": [r for r in VALID_ROLES if r],   # drop the empty-string role
    "source": VALID_SOURCES,
}


class _CheckableMultiCombo(QComboBox):
    """QComboBox whose popup items are check-boxable.

    Clicking an item toggles its check state without closing the popup; the
    inline display text shows the comma-joined list of checked labels.
    Returned via :py:meth:`checked_items`.
    """

    def __init__(self, items: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PyQt6.QtGui import QStandardItem, QStandardItemModel  # noqa: PLC0415

        self.setEditable(True)
        le = self.lineEdit()
        if le is not None:
            le.setReadOnly(True)
            le.setAlignment(Qt.AlignmentFlag.AlignLeft)
            le.setPlaceholderText("Select one or more…")

        model = QStandardItemModel(len(items), 1, self)
        for i, v in enumerate(items):
            it = QStandardItem(v)
            it.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            it.setData(Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
            model.setItem(i, 0, it)
        self.setModel(model)

        # Toggle the check state on click instead of closing the popup.
        view = self.view()
        if view is not None:
            view.pressed.connect(self._toggle_item)
        model.dataChanged.connect(self._refresh_text)
        self._refresh_text()

    def _toggle_item(self, index: QModelIndex) -> None:
        from PyQt6.QtGui import QStandardItemModel  # noqa: PLC0415

        m = self.model()
        if not isinstance(m, QStandardItemModel):
            return
        item = m.itemFromIndex(index)
        if item is None:
            return
        if item.checkState() == Qt.CheckState.Checked:
            item.setCheckState(Qt.CheckState.Unchecked)
        else:
            item.setCheckState(Qt.CheckState.Checked)

    def _refresh_text(self) -> None:
        le = self.lineEdit()
        if le is None:
            return
        checked = self.checked_items()
        le.setText(", ".join(checked))

    def checked_items(self) -> list[str]:
        from PyQt6.QtGui import QStandardItemModel  # noqa: PLC0415

        m = self.model()
        if not isinstance(m, QStandardItemModel):
            return []
        out: list[str] = []
        for i in range(m.rowCount()):
            item = m.item(i)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                out.append(item.text())
        return out


class _RuleRow(QWidget):
    """One condition row: field · operator · value · remove.

    The value widget changes based on the selected field + operator:
      * ``confidence``                 → QDoubleSpinBox (0.00–1.00)
      * ``type`` / ``role`` / ``source`` with ``=``/``≠``   → QComboBox
      * ``type`` / ``role`` / ``source`` with ``in``/``not in`` → multi-select
      * (fallback for unknown fields)  → free-text QLineEdit
    Only the currently-relevant widget is visible; the others are hidden so
    the row stays single-line.

    The ``options_for`` dict supplies the LIVE enum values — only types /
    roles / sources that actually appear in the currently-loaded entity
    set. This prevents the user from writing rules that can never match.
    """

    removed = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        options_for: dict[str, list[str]] | None = None,
        fields: list[str] | None = None,
        numeric_fields: set[str] | None = None,
        numeric_field_ranges: dict[str, tuple[float, float, float, int]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._options_for: dict[str, list[str]] = options_for or {}
        self._field_list: list[str] = list(fields) if fields else list(_AUTO_FIELDS)
        self._numeric_fields: set[str] = set(
            numeric_fields or {"confidence", "model_confidence"}
        )
        self._numeric_ranges = numeric_field_ranges or {}
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE_SM)

        self.field_combo = QComboBox()
        self.field_combo.addItems(self._field_list)
        self.field_combo.setMinimumWidth(180)
        self.field_combo.currentTextChanged.connect(self._on_field_or_op_changed)
        row.addWidget(self.field_combo)

        # op_combo holds very short labels (>, <, =, in, …). Without a
        # minimum width the cell is narrower than the chevron region, and
        # the click target feels broken. 100 px leaves room for "not in"
        # plus the drop-down arrow. Ops are pre-populated for the default
        # field ("confidence") so the combo is never empty — even if
        # _on_field_or_op_changed is never called or fires before items
        # are connected.
        self.op_combo = QComboBox()
        self.op_combo.setMinimumWidth(100)
        self.op_combo.addItems(_NUMERIC_OPS)
        self.op_combo.currentTextChanged.connect(self._on_field_or_op_changed)
        row.addWidget(self.op_combo)

        # Numeric value — range adapts to the selected numeric field via
        # ``_numeric_ranges``. Default preserves the legacy confidence
        # behaviour (0.0–1.0 step 0.05 default 0.70, 2 decimals).
        self.value_num = QDoubleSpinBox()
        self.value_num.setRange(0.0, 1.0)
        self.value_num.setSingleStep(0.05)
        self.value_num.setDecimals(2)
        self.value_num.setValue(0.70)
        row.addWidget(self.value_num, stretch=1)

        # Single-value enum dropdown (=, ≠)
        self.value_enum_single = QComboBox()
        row.addWidget(self.value_enum_single, stretch=1)

        # Multi-value enum dropdown (in, not in) — checkable popup
        self.value_enum_multi = _CheckableMultiCombo([])
        row.addWidget(self.value_enum_multi, stretch=1)

        # Free-text fallback for unknown fields
        self.value_text = QLineEdit()
        self.value_text.setPlaceholderText("value or comma-separated list")
        row.addWidget(self.value_text, stretch=1)

        remove = QPushButton("×")
        remove.setFixedWidth(28)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setStyleSheet(theme.ghost_button_style())
        remove.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(remove)

        self._on_field_or_op_changed()

    # ── Widget-visibility orchestration ──────────────────────────────────

    def _is_numeric(self, field: str) -> bool:
        return field in self._numeric_fields

    def _repopulate_ops_for_field(self, field: str) -> None:
        """Ensure op_combo contains the right operator set for *field*.
        Preserves the currently-selected op when possible.
        """
        current_op = self.op_combo.currentText()
        new_ops = _NUMERIC_OPS if self._is_numeric(field) else _STRING_OPS
        self.op_combo.blockSignals(True)
        self.op_combo.clear()
        self.op_combo.addItems(new_ops)
        if current_op in new_ops:
            self.op_combo.setCurrentText(current_op)
        self.op_combo.blockSignals(False)

    def _hide_all_values(self) -> None:
        self.value_num.setVisible(False)
        self.value_enum_single.setVisible(False)
        self.value_enum_multi.setVisible(False)
        self.value_text.setVisible(False)

    def _on_field_or_op_changed(self, *_args: object) -> None:
        field = self.field_combo.currentText()
        # Always make sure op_combo holds the ops matching the current field.
        # ``_repopulate_ops_for_field`` is idempotent — it preserves the
        # current op if still applicable and overrides with the correct set
        # otherwise. Depending on QObject.sender() here is unreliable: when
        # this method is called directly (from __init__ or _refresh) PyQt6
        # may or may not return the expected sender, which previously left
        # op_combo empty in dialogs that hadn't been shown yet.
        expected_ops = _NUMERIC_OPS if self._is_numeric(field) else _STRING_OPS
        if [self.op_combo.itemText(i) for i in range(self.op_combo.count())] != expected_ops:
            self._repopulate_ops_for_field(field)

        op = self.op_combo.currentText()
        # Prefer LIVE options (values actually present in the loaded data);
        # fall back to the full enum if the caller didn't supply any.
        options = self._options_for.get(field) or _FIELD_OPTIONS.get(field)

        self._hide_all_values()
        if self._is_numeric(field):
            # Apply per-field range/step/decimals overrides (Wikidata uses
            # 0-100 for label_length, 0-9999 for n_claims etc.). Tuple is
            # (min, max, step, decimals).
            r = self._numeric_ranges.get(field)
            if r is not None:
                lo, hi, step, dec = r
                self.value_num.setRange(lo, hi)
                self.value_num.setSingleStep(step)
                self.value_num.setDecimals(dec)
                if not (lo <= self.value_num.value() <= hi):
                    self.value_num.setValue((lo + hi) / 2 if dec else int((lo + hi) / 2))
            else:
                # Default (confidence-style) range
                self.value_num.setRange(0.0, 1.0)
                self.value_num.setSingleStep(0.05)
                self.value_num.setDecimals(2)
            self.value_num.setVisible(True)
            return
        if options is None:
            self.value_text.setVisible(True)
            return
        if op in ("=", "≠"):
            # Single-value dropdown
            current = self.value_enum_single.currentText()
            self.value_enum_single.blockSignals(True)
            self.value_enum_single.clear()
            self.value_enum_single.addItems(options)
            if current in options:
                self.value_enum_single.setCurrentText(current)
            self.value_enum_single.blockSignals(False)
            self.value_enum_single.setVisible(True)
            return
        if op in ("in", "not in"):
            # Rebuild the multi-select with the new option set only if the
            # options actually differ — avoids losing check state.
            existing = getattr(self.value_enum_multi, "_items_snapshot", None)
            if existing != options:
                self.value_enum_multi.setParent(None)
                self.value_enum_multi.deleteLater()
                self.value_enum_multi = _CheckableMultiCombo(options)
                self.value_enum_multi._items_snapshot = list(options)  # type: ignore[attr-defined]
                self.layout().insertWidget(4, self.value_enum_multi, 1)
            self.value_enum_multi.setVisible(True)
            return
        # Fallback
        self.value_text.setVisible(True)

    # ── Export ───────────────────────────────────────────────────────────

    def to_rule(self) -> dict[str, Any]:
        field = self.field_combo.currentText()
        op = self.op_combo.currentText()
        if self._is_numeric(field):
            # Preserve int-ness for fields declared with 0 decimals
            raw = self.value_num.value()
            if self.value_num.decimals() == 0:
                raw = int(raw)
            return {"field": field, "op": op, "value": raw}
        if op in ("=", "≠") and self.value_enum_single.isVisible():
            return {"field": field, "op": op, "value": self.value_enum_single.currentText()}
        if op in ("in", "not in") and self.value_enum_multi.isVisible():
            return {"field": field, "op": op, "value": list(self.value_enum_multi.checked_items())}
        raw = self.value_text.text().strip()
        if op in ("in", "not in"):
            return {"field": field, "op": op, "value": [s.strip() for s in raw.split(",") if s.strip()]}
        return {"field": field, "op": op, "value": raw}


def evaluate_rule(entity: dict, rule: dict) -> bool:
    """Apply a single rule to an entity dict. True = match."""
    field = rule["field"]
    op = rule["op"]
    val = rule["value"]
    ent_val = entity.get(field)
    # Strict numeric comparisons (>, >=, <=, <) require both sides to parse
    # as floats. ``=`` and ``≠`` are polymorphic — numeric when both sides
    # parse as numbers, otherwise string equality. This keeps confidence
    # rules numeric while letting string-valued synthetic fields like
    # ``has_external_id`` and ``confidence_band`` use the same ``=`` op.
    strict_numeric_ops = {">", ">=", "<=", "<"}
    if op in strict_numeric_ops:
        try:
            ent_val_num = float(ent_val or 0)
            target = float(val or 0)
        except (TypeError, ValueError):
            return False
        if op == ">":
            return ent_val_num > target
        if op == ">=":
            return ent_val_num >= target
        if op == "<=":
            return ent_val_num <= target
        if op == "<":
            return ent_val_num < target
        return False
    if op in ("=", "≠"):
        try:
            ent_val_num = float(ent_val) if ent_val not in (None, "") else None
            target_num = float(val) if val not in (None, "") else None
        except (TypeError, ValueError):
            ent_val_num = target_num = None
        if ent_val_num is not None and target_num is not None:
            return (ent_val_num == target_num) if op == "=" else (ent_val_num != target_num)
        ent_str = str(ent_val or "")
        return (ent_str == str(val)) if op == "=" else (ent_str != str(val))
    ent_str = str(ent_val or "")
    if op == "in":
        return ent_str in (val if isinstance(val, list) else [val])
    if op == "not in":
        return ent_str not in (val if isinstance(val, list) else [val])
    return False


def evaluate_rules(entity: dict, rules: list[dict], combinator: str) -> bool:
    """Evaluate all rules with AND / OR combinator."""
    if not rules:
        return False
    results = [evaluate_rule(entity, r) for r in rules]
    if combinator == "AND":
        return all(results)
    return any(results)


class AutoApproveDialog(QDialog):
    """Multi-condition rule builder for bulk-approving entities.

    Three override kwargs let callers adapt the dialog to *any* domain
    (NER rows, authority matches, Wikidata items):

    ``options_for``   — enum values for each field (only the values
                         present in the currently-loaded data set).
    ``fields``         — override the default NER field list
                         ``_AUTO_FIELDS`` with a custom ordered list.
    ``numeric_fields`` — set of field names rendered with a numeric
                         spin-box instead of an enum dropdown.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        options_for: dict[str, list[str]] | None = None,
        fields: list[str] | None = None,
        numeric_fields: set[str] | None = None,
        numeric_field_ranges: dict[str, tuple[float, float, float, int]] | None = None,
    ) -> None:
        super().__init__(parent)
        # Liquid-glass backdrop — every dialog in the app uses the same
        # GraphBackdrop surface (design-system rule 36). Widgets sit on a
        # translucent content container layered on top of the backdrop.
        from mhm_pipeline.gui.widgets.glass_dialog import (  # noqa: PLC0415
            install_glass_backdrop,
        )

        self._options_for: dict[str, list[str]] = options_for or {}
        self._fields = fields
        # Both confidence signals are spin-box numerics: the legacy
        # keyword-classifier ``confidence`` (capped at 0.85) and the real
        # softmax ``model_confidence`` from the BIO classifier.
        self._numeric_fields = (
            set(numeric_fields or ()) or {"confidence", "model_confidence"}
        )
        self._numeric_ranges = numeric_field_ranges or {}
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self.setWindowTitle("Auto-approve entities")
        self.resize(760, 460)

        content = install_glass_backdrop(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        info = QLabel(
            "Approve every entity that matches all (or any) of the "
            "following conditions. Leave a value blank to match anything."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {theme.ui('subtext')};")
        layout.addWidget(info)

        # Combinator
        comb_row = QHBoxLayout()
        comb_row.setSpacing(theme.SPACE_SM)
        comb_row.addWidget(QLabel("Combine with:"))
        self.combinator = QComboBox()
        self.combinator.addItems(["AND", "OR"])
        comb_row.addWidget(self.combinator)
        comb_row.addStretch()
        layout.addLayout(comb_row)

        # Rule rows
        self._rules_container = QWidget()
        self._rules_container.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True,
        )
        self._rules_layout = QVBoxLayout(self._rules_container)
        self._rules_layout.setContentsMargins(0, 0, 0, 0)
        self._rules_layout.setSpacing(theme.SPACE_SM)
        self._rule_widgets: list[_RuleRow] = []
        scroll = QScrollArea()
        scroll.setWidget(self._rules_container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        layout.addWidget(scroll, stretch=1)

        # Add-rule + action buttons
        bottom = QHBoxLayout()
        bottom.setSpacing(theme.SPACE_MD)
        add_rule = QPushButton("+ Add condition")
        add_rule.setStyleSheet(theme.ghost_button_style())
        add_rule.setCursor(Qt.CursorShape.PointingHandCursor)
        add_rule.clicked.connect(self._add_rule)
        bottom.addWidget(add_rule)
        bottom.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(theme.ghost_button_style())
        cancel.clicked.connect(self.reject)
        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet(theme.success_btn_style())
        apply_btn.clicked.connect(self.accept)
        bottom.addWidget(cancel)
        bottom.addWidget(apply_btn)
        layout.addLayout(bottom)

        self._add_rule()

    def _add_rule(self) -> None:
        rule = _RuleRow(
            options_for=self._options_for,
            fields=self._fields,
            numeric_fields=self._numeric_fields,
            numeric_field_ranges=self._numeric_ranges,
        )
        rule.removed.connect(self._remove_rule)
        self._rule_widgets.append(rule)
        self._rules_layout.addWidget(rule)

    def _remove_rule(self, widget: _RuleRow) -> None:
        if widget in self._rule_widgets:
            self._rule_widgets.remove(widget)
            self._rules_layout.removeWidget(widget)
            widget.deleteLater()

    def rules(self) -> list[dict[str, Any]]:
        return [w.to_rule() for w in self._rule_widgets]

    def combinator_value(self) -> str:
        return self.combinator.currentText()


# ────────────────────────────────────────────────────────────────────────────
# Manual add-entity dialog
# ────────────────────────────────────────────────────────────────────────────

class AddEntityDialog(QDialog):
    """Dialog for manually adding a new entity."""

    def __init__(self, control_numbers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui.widgets.glass_dialog import install_glass_backdrop  # noqa: PLC0415

        self.setWindowTitle("Add Entity")
        self.setMinimumWidth(400)

        _content = install_glass_backdrop(self)
        layout = QFormLayout(_content)

        self.record_combo = QComboBox()
        self.record_combo.addItems(control_numbers)
        layout.addRow("Record:", self.record_combo)

        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("Entity text…")
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


# ────────────────────────────────────────────────────────────────────────────
# Main editor widget
# ────────────────────────────────────────────────────────────────────────────

class ExtractionEditor(QWidget):
    """Editable NER entity table with approval, view-source, and auto-rules."""

    entities_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)

        # ── Header: stats + primary actions ──────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(theme.SPACE_SM)
        self._stats_label = QLabel("No entities loaded")
        self._stats_label.setStyleSheet(
            f"color: {theme.ui('text')}; font-size: {theme.FONT_BASE}px;"
        )
        header.addWidget(self._stats_label)
        header.addStretch()

        self._auto_btn = QPushButton("⚡ Auto-approve…")
        self._auto_btn.setStyleSheet(theme.ghost_button_style())
        self._auto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auto_btn.clicked.connect(self._on_auto_approve)
        header.addWidget(self._auto_btn)

        self._approve_all_btn = QPushButton("Approve visible")
        self._approve_all_btn.setStyleSheet(theme.ghost_button_style())
        self._approve_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._approve_all_btn.clicked.connect(lambda: self._set_visible_approved(True))
        header.addWidget(self._approve_all_btn)

        self._unapprove_all_btn = QPushButton("Clear approval")
        self._unapprove_all_btn.setStyleSheet(theme.ghost_button_style())
        self._unapprove_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unapprove_all_btn.clicked.connect(lambda: self._set_visible_approved(False))
        header.addWidget(self._unapprove_all_btn)

        self._add_btn = QPushButton("+ Add")
        self._add_btn.setStyleSheet(theme.ghost_button_style())
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.clicked.connect(self._on_add)
        header.addWidget(self._add_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setStyleSheet(theme.ghost_button_style())
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.clicked.connect(self._on_delete)
        header.addWidget(self._delete_btn)

        self._revert_btn = QPushButton("Revert")
        self._revert_btn.setStyleSheet(theme.ghost_button_style())
        self._revert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._revert_btn.clicked.connect(self._on_revert)
        header.addWidget(self._revert_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setStyleSheet(theme.success_btn_style())
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)
        header.addWidget(self._save_btn)

        layout.addLayout(header)

        # ── Search ───────────────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(theme.SPACE_SM)
        search_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter entities by text, record, type, role…")
        self._search_edit.textChanged.connect(self._on_search)
        search_row.addWidget(self._search_edit)
        layout.addLayout(search_row)

        # ── Table ────────────────────────────────────────────────────────
        self._model = EditableEntityModel()
        self._proxy = EntityFilterProxy()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)  # search all columns
        self._proxy.setSortRole(Qt.ItemDataRole.UserRole)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setItemDelegateForColumn(COL_TYPE, EntityTypeDelegate(self))
        self._table.setItemDelegateForColumn(COL_ROLE, EntityRoleDelegate(self))

        # Dropdown cells should open on the FIRST click (not require a
        # double-click) because they're clearly enum controls with a visible
        # chevron. QTableView's "SelectedClicked" trigger means: the row is
        # selected on click 1, then click 2 on the same cell opens the editor.
        # CurrentChanged means: moving to the cell via keyboard opens it.
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(False)

        h = self._table.horizontalHeader()
        assert h is not None
        h.setSectionResizeMode(COL_RECORD, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_TEXT, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_ROLE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_CONF, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_APPROVED, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_ACTIONS, 36)

        layout.addWidget(self._table, stretch=1)

        self._output_path: Path | None = None
        self._model.dataChanged.connect(self._on_data_changed)
        self._model.rowsInserted.connect(self._refresh_action_widgets)
        self._model.rowsRemoved.connect(self._refresh_action_widgets)
        self._model.modelReset.connect(self._refresh_action_widgets)

        self._active_filters: dict[str, set[str]] = {
            "sources": set(), "types": set(), "roles": set(),
        }

    # ── Public API ───────────────────────────────────────────────────────

    def load_records(self, records: list[dict], output_path: Path | None = None) -> None:
        self._model.load_from_records(records)
        self._output_path = output_path
        self._active_filters = {"sources": set(), "types": set(), "roles": set()}
        self._refresh_action_widgets()
        self._update_stats()

    def get_all_sources(self) -> list[str]:
        return sorted({str(e.get("source") or "") for e in self._model._entities if e.get("source")})

    def get_all_types(self) -> list[str]:
        return sorted({str(e.get("type") or "") for e in self._model._entities if e.get("type")})

    def get_all_roles(self) -> list[str]:
        return sorted({str(e.get("role") or "") for e in self._model._entities if e.get("role")})

    def apply_filters(
        self,
        sources: set[str] | None,
        types: set[str] | None,
        roles: set[str] | None,
    ) -> None:
        if isinstance(self._proxy, EntityFilterProxy):
            self._proxy.set_dimension_filters(
                set(sources or ()),
                set(types or ()),
                set(roles or ()),
            )
        self._refresh_action_widgets()
        self._update_stats()

    # ── View-source action buttons per row ───────────────────────────────

    def _refresh_action_widgets(self) -> None:
        """Recreate the per-row action widgets (✎ Edit · ↗ View source).

        Each row gets a small inline toolbar with two ghost-style icon
        buttons. Sizing is tight so the column width stays at ~78 px.
        """
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        # Clear any widgets from previous state
        for row in range(self._proxy.rowCount()):
            idx = self._proxy.index(row, COL_ACTIONS)
            self._table.setIndexWidget(idx, None)

        btn_qss = (
            f"QPushButton {{ background: transparent;"
            f" color: {theme.ui('text')};"
            f" border: 1px solid {theme.ui('border')};"
            f" border-radius: {theme.RADIUS_SM}px;"
            f" font-size: {theme.FONT_BASE}px;"
            f" font-weight: 600;"
            f" padding: 0 4px;"
            f" min-height: 22px; min-width: 24px; }}"
            f"QPushButton:hover {{"
            f" background: rgba(255,255,255,18);"
            f" border-color: {theme.ui('highlight')}; }}"
        )

        for row in range(self._proxy.rowCount()):
            idx = self._proxy.index(row, COL_ACTIONS)
            container = QWidget()
            h = QHBoxLayout(container)
            h.setContentsMargins(2, 1, 2, 1)
            h.setSpacing(4)

            edit_btn = QPushButton("✎")
            edit_btn.setToolTip("Edit entity text")
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.setStyleSheet(btn_qss)
            edit_btn.clicked.connect(lambda _=False, r=row: self._on_edit_text(r))
            h.addWidget(edit_btn)

            view_btn = QPushButton("↗")
            view_btn.setToolTip("View source text with this entity highlighted")
            view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            view_btn.setStyleSheet(btn_qss)
            view_btn.clicked.connect(lambda _=False, r=row: self._on_view_source(r))
            h.addWidget(view_btn)

            self._table.setIndexWidget(idx, container)

    def _proxy_row_to_source(self, proxy_row: int) -> int:
        proxy_idx = self._proxy.index(proxy_row, COL_ACTIONS)
        return self._proxy.mapToSource(proxy_idx).row()

    def _on_view_source(self, proxy_row: int) -> None:
        source_row = self._proxy_row_to_source(proxy_row)
        if not 0 <= source_row < len(self._model._entities):
            return
        full, et, start, end = self._model.source_text_for(source_row)
        ent = self._model._entities[source_row]
        cn = ent.get("_control_number", "")
        dlg = SourceViewDialog(cn, full, et, start, end, parent=self)
        dlg.exec()

    def _on_edit_text(self, proxy_row: int) -> None:
        """Open the popup editor for the entity's text; save back to the model."""
        source_row = self._proxy_row_to_source(proxy_row)
        if not 0 <= source_row < len(self._model._entities):
            return
        ent = self._model._entities[source_row]
        cn = ent.get("_control_number", "")
        current = str(ent.get("text", "") or "")
        full, _et, _s, _e = self._model.source_text_for(source_row)

        dlg = EntityTextEditDialog(
            current_text=current,
            source_text=full,
            control_number=str(cn),
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_text = dlg.value()
        if new_text == current:
            return
        # Update via setData so dataChanged propagates and the view re-renders
        text_idx = self._model.index(source_row, COL_TEXT)
        self._model._entities[source_row]["text"] = new_text
        # Also update start/end: if the new text appears in the full source,
        # refresh the offsets so the "view source" highlight stays accurate.
        if full:
            pos = full.find(new_text)
            if pos >= 0:
                self._model._entities[source_row]["start"] = pos
                self._model._entities[source_row]["end"] = pos + len(new_text)
        self._model.dataChanged.emit(text_idx, text_idx)
        self.entities_changed.emit()

    # ── Auto-approve ─────────────────────────────────────────────────────

    def _on_auto_approve(self) -> None:
        # Only offer types/roles/sources that actually exist in the loaded
        # entity set — dropdowns never show a value that can't match a row.
        options_for = {
            "type": self.get_all_types(),
            "role": self.get_all_roles(),
            "source": self.get_all_sources(),
        }
        dlg = AutoApproveDialog(self, options_for=options_for)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rules = dlg.rules()
        combinator = dlg.combinator_value()

        matched_rows: list[int] = []
        for i, ent in enumerate(self._model._entities):
            if evaluate_rules(ent, rules, combinator):
                matched_rows.append(i)
        changed = self._model.set_approved_bulk(matched_rows, True)
        self._update_stats()
        self.entities_changed.emit()
        QMessageBox.information(
            self,
            "Auto-approve",
            f"Approved {changed} entit{'y' if changed == 1 else 'ies'} matching the rules.",
        )

    def _set_visible_approved(self, approved: bool) -> None:
        """Approve / un-approve only the rows currently visible after filters."""
        rows: list[int] = []
        for r in range(self._proxy.rowCount()):
            src = self._proxy.mapToSource(self._proxy.index(r, COL_APPROVED)).row()
            rows.append(src)
        changed = self._model.set_approved_bulk(rows, approved)
        self._update_stats()
        self.entities_changed.emit()
        verb = "Approved" if approved else "Cleared approval on"
        QMessageBox.information(
            self,
            "Bulk approval",
            f"{verb} {changed} visible entit{'y' if changed == 1 else 'ies'}.",
        )

    # ── Stats + CRUD ─────────────────────────────────────────────────────

    def _update_stats(self) -> None:
        total = self._model.rowCount()
        visible = self._proxy.rowCount() if self._proxy else total
        approved = sum(1 for e in self._model._entities if e.get("approved", False))
        dirty = " (modified)" if self._model.is_dirty() else ""
        pct = (approved / total * 100) if total else 0.0
        if visible == total:
            self._stats_label.setText(
                f"{total} entities · {approved} approved ({pct:.0f}%){dirty}"
            )
        else:
            self._stats_label.setText(
                f"{visible} of {total} visible · {approved} approved ({pct:.0f}%){dirty}"
            )

    def _on_search(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        self._refresh_action_widgets()
        self._update_stats()

    def _on_add(self) -> None:
        cns = sorted({self._model._entities[i]["_control_number"]
                      for i in range(len(self._model._entities))})
        if not cns:
            cns = [""]
        dlg = AddEntityDialog(cns, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            text = dlg.text_edit.text().strip()
            if text:
                self._model.add_entity(
                    dlg.record_combo.currentText(), text,
                    dlg.type_combo.currentText(),
                )
                self._update_stats()
                self.entities_changed.emit()

    def _on_delete(self) -> None:
        sel_model = self._table.selectionModel()
        if sel_model is None:
            return
        indices = sel_model.selectedRows()
        if not indices:
            return
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
            self._refresh_action_widgets()
            self._update_stats()

    def _on_save(self) -> None:
        """Write the NER results file, keeping only approved entities.

        Unapproved entities are dropped on save — they won't flow to the
        authority-matching / RDF / upload stages. The edit-side state (in
        memory) retains them until the next reset so the user can still
        re-toggle approval without re-running NER.
        """
        if not self._output_path:
            return

        all_entities = len(self._model._entities)
        approved = sum(1 for e in self._model._entities if e.get("approved", False))
        rejected = all_entities - approved

        if approved == 0 and all_entities > 0:
            confirm = QMessageBox.question(
                self,
                "No approved entities",
                "No entities are currently approved. Saving will write an "
                "empty result set.\n\nContinue anyway?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        elif rejected > 0:
            confirm = QMessageBox.question(
                self,
                "Save approved entities only",
                f"Save will keep {approved} approved entit"
                f"{'y' if approved == 1 else 'ies'} and DROP {rejected} "
                f"unapproved one{'s' if rejected != 1 else ''} from the "
                f"output file.\n\nProceed?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        records = self._model.to_approved_records()
        self._output_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Mark the current in-memory state as the new baseline so subsequent
        # dirty checks are relative to what's on disk.
        self._model._original = copy.deepcopy(self._model._entities)
        self._update_stats()
        logger.info(
            "Saved %d approved entities (%d unapproved dropped) to %s",
            approved, rejected, self._output_path,
        )
        QMessageBox.information(
            self,
            "Saved",
            f"Saved {approved} approved entit{'y' if approved == 1 else 'ies'}"
            f" ({rejected} unapproved dropped) to\n{self._output_path}",
        )

    def _on_data_changed(self) -> None:
        self._update_stats()
        self.entities_changed.emit()
