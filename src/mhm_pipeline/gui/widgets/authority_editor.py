"""Authority-match editor — Stage 2 review surface.

Mirrors :mod:`extraction_editor` for Stage 2 authority results. A row is
one ``(entity, match)`` tuple drawn from three shapes in
``authority_enriched.json``:

* ``marc_authority_matches[*]`` — persons from MARC 100/700/710 etc.
* ``entities[*]`` where the NER entity was enriched with an authority ID
* ``kima_places`` — place-name → Wikidata URI matches

The user approves each match; on save, unapproved rows are dropped before
the file is read by Stage 3 (``RdfBuildWorker``). Stage 3 already tolerates
missing authority links — each entity falls back to a local item — so
dropping rows is safe.

Columns: Record · Entity · Match · Source · Type · Conf. · Approved · ✎↗
"""

from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

# Pattern for extracting a Wikidata QID from a Wikidata entity URI of the
# shape ``https://www.wikidata.org/entity/Q123`` (KIMA values often arrive
# as such URIs). Used by :func:`flatten_authority_records` to surface the
# QID in its dedicated column.
_WIKIDATA_QID_RE = re.compile(r"/entity/(Q\d+)/?$")

# Stage-3 hardening (2026-05-02) emits ``confidence`` as a tri-level
# string ("high"/"medium"/"low") instead of a 0.0–1.0 float. The widget
# stores everything as a float for sorting/colour coding — this coercer
# bridges both schemas without breaking older artefacts.
_CONF_BUCKET_TO_FLOAT = {"high": 0.95, "medium": 0.6, "low": 0.3}


def _coerce_confidence(value: object) -> float:
    """Return a 0.0–1.0 float for any of: float, int, bool, level-string."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _CONF_BUCKET_TO_FLOAT:
            return _CONF_BUCKET_TO_FLOAT[s]
        try:
            return float(s)
        except ValueError:
            return 0.0
    return 0.0

from PyQt6.QtCore import (
    QAbstractItemModel,
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
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

# We reuse the rule primitives + the multi-select combo from the NER editor
# so the auto-approve flow stays consistent across stages.
from mhm_pipeline.gui.widgets.extraction_editor import (
    _CheckableMultiCombo,
    evaluate_rule,
    evaluate_rules,
)

logger = logging.getLogger(__name__)

VALID_SOURCES: list[str] = ["mazal", "viaf", "kima", "ner_entity", "marc_field"]
VALID_MATCH_TYPES: list[str] = ["person", "place", "work"]
VALID_CONF_BANDS: list[str] = ["high", "medium", "low", "no_match"]


def _conf_band(conf: float | None) -> str:
    if conf is None or conf <= 0:
        return "no_match"
    if conf >= 0.90:
        return "high"
    if conf >= 0.70:
        return "medium"
    return "low"


# ────────────────────────────────────────────────────────────────────────────
# Normalisation — flatten authority_enriched.json into flat match rows
# ────────────────────────────────────────────────────────────────────────────


def flatten_authority_records(records: list[dict]) -> list[dict]:
    """Flatten the three-shape authority JSON into flat match-rows.

    Each returned dict has the shape consumed by :class:`AuthorityMatchModel`:

    .. code-block::

        {
          "_control_number": "...",
          "_origin_kind": "marc" | "entity" | "kima",
          "_origin_index": int,   # position in the origin list
          "entity_text": str,
          "match_type": "person" | "place" | "work",
          "role": str,
          "matched_name": str,
          "source": str,          # mazal / viaf / kima / …
          "matched_id": str,
          "wikidata_qid": str,    # Wikidata QID when known (KIMA URI / harvested)
          "confidence": float,
          "dates": str,
          "gnd_id": str, "lc_id": str, "isni": str, "bnf_id": str,
          "field_origin": str,
          "approved": bool,
        }
    """
    out: list[dict] = []
    for record in records:
        cn = str(record.get("_control_number", ""))

        # 1. MARC authority matches (persons from MARC fields)
        for i, m in enumerate(record.get("marc_authority_matches") or []):
            viaf = m.get("viaf_uri") or ""
            mazal = m.get("mazal_id") or ""
            source = "mazal" if mazal else ("viaf" if viaf else "marc_field")
            # When neither Mazal nor VIAF resolved, the match was not
            # found — show "(no match found)" instead of echoing the
            # entity name (which previously made the row look like a
            # successful self-match).
            if mazal or viaf:
                matched_name = str(m.get("preferred_name_lat") or m.get("name") or "")
            else:
                matched_name = "(no match found)"
            out.append({
                "_control_number": cn,
                "_origin_kind": "marc",
                "_origin_index": i,
                "entity_text": str(m.get("name") or ""),
                "match_type": "person",
                "role": str(m.get("role") or ""),
                "matched_name": matched_name,
                "source": source,
                "matched_id": str(mazal or viaf or ""),
                "wikidata_qid": str(m.get("wikidata_qid") or ""),
                "confidence": _coerce_confidence(m.get("confidence")),
                "dates": str(m.get("dates") or ""),
                "gnd_id": str(m.get("gnd_id") or ""),
                "lc_id": str(m.get("lc_id") or ""),
                "isni": str(m.get("isni") or ""),
                "bnf_id": str(m.get("bnf_id") or ""),
                "field_origin": str(m.get("field") or ""),
                "approved": bool(m.get("approved", False)),
            })

        # 2. NER entities enriched with authority IDs
        for i, e in enumerate(record.get("entities") or []):
            viaf = e.get("viaf_uri") or ""
            mazal = e.get("mazal_id") or ""
            if not viaf and not mazal:
                continue
            source = "mazal" if mazal else "viaf"
            out.append({
                "_control_number": cn,
                "_origin_kind": "entity",
                "_origin_index": i,
                "entity_text": str(e.get("person") or e.get("text") or ""),
                "match_type": "person",
                "role": str(e.get("role") or ""),
                "matched_name": "",
                "source": source,
                "matched_id": str(mazal or viaf),
                "wikidata_qid": str(e.get("wikidata_qid") or ""),
                "confidence": _coerce_confidence(e.get("confidence")),
                "dates": "",
                "gnd_id": "", "lc_id": "", "isni": "", "bnf_id": "",
                "field_origin": "ner",
                "approved": bool(e.get("authority_approved", False)),
            })

        # 3. KIMA places (name → Wikidata URI)
        kima = record.get("kima_places") or {}
        if isinstance(kima, dict):
            for i, (name, uri) in enumerate(kima.items()):
                # KIMA values are often Wikidata entity URIs of the form
                # ``https://www.wikidata.org/entity/Q1218``. Extract the
                # QID so it can be surfaced in its own column without
                # forcing the reviewer to parse the URI by eye.
                qid_match = _WIKIDATA_QID_RE.search(str(uri))
                qid = qid_match.group(1) if qid_match else ""
                out.append({
                    "_control_number": cn,
                    "_origin_kind": "kima",
                    "_origin_index": i,
                    "entity_text": str(name),
                    "match_type": "place",
                    "role": "",
                    "matched_name": "",
                    "source": "kima",
                    "matched_id": str(uri),
                    "wikidata_qid": qid,
                    "confidence": 1.0,          # KIMA is a direct-index lookup
                    "dates": "",
                    "gnd_id": "", "lc_id": "", "isni": "", "bnf_id": "",
                    "field_origin": "marc_place",
                    "approved": False,
                })
    return out


def unflatten_rows_into_records(
    rows: list[dict], original_records: list[dict],
) -> list[dict]:
    """Inverse of :func:`flatten_authority_records`.

    Takes the possibly-edited flat rows and merges them back into the
    original record skeletons. Rows with ``approved=False`` are DROPPED
    from the corresponding ``marc_authority_matches`` / ``entities`` /
    ``kima_places`` collections. Stage 3 tolerates empties, so this is
    safe downstream.
    """
    out = [copy.deepcopy(r) for r in original_records]
    by_cn: dict[str, dict] = {str(r.get("_control_number") or ""): r for r in out}

    # Reset the authority-bearing collections on each record; we'll
    # repopulate only the approved rows below.
    for r in out:
        r["marc_authority_matches"] = []
        r["kima_places"] = {}
        for e in r.get("entities") or []:
            # Clear authority IDs — will be re-populated if approved.
            e.pop("viaf_uri", None)
            e.pop("mazal_id", None)

    for row in rows:
        if not row.get("approved", False):
            continue
        cn = row.get("_control_number", "")
        rec = by_cn.get(cn)
        if rec is None:
            continue
        kind = row.get("_origin_kind")
        if kind == "marc":
            marc_match: dict[str, Any] = {
                "name": row.get("entity_text", ""),
                "role": row.get("role", ""),
                "field": row.get("field_origin", ""),
                "confidence": row.get("confidence", 0.0),
                "mazal_id": row.get("matched_id", "") if row.get("source") == "mazal" else "",
                "viaf_uri": row.get("matched_id", "") if row.get("source") == "viaf" else "",
                "preferred_name_lat": row.get("matched_name", ""),
                "dates": row.get("dates", ""),
                "gnd_id": row.get("gnd_id", ""),
                "lc_id": row.get("lc_id", ""),
                "isni": row.get("isni", ""),
                "bnf_id": row.get("bnf_id", ""),
                "approved": True,
            }
            if row.get("wikidata_qid"):
                marc_match["wikidata_qid"] = row["wikidata_qid"]
            rec["marc_authority_matches"].append(marc_match)
        elif kind == "entity":
            idx = int(row.get("_origin_index") or 0)
            entities = rec.get("entities") or []
            if 0 <= idx < len(entities):
                e = entities[idx]
                if row.get("source") == "mazal":
                    e["mazal_id"] = row.get("matched_id", "")
                else:
                    e["viaf_uri"] = row.get("matched_id", "")
                if row.get("wikidata_qid"):
                    e["wikidata_qid"] = row["wikidata_qid"]
                e["authority_approved"] = True
        elif kind == "kima":
            name = row.get("entity_text", "")
            uri = row.get("matched_id", "")
            if name and uri:
                rec["kima_places"][name] = uri
    return out


# ────────────────────────────────────────────────────────────────────────────
# Proxy / filtering
# ────────────────────────────────────────────────────────────────────────────


class AuthorityFilterProxy(QSortFilterProxyModel):
    """Proxy filtering by source / match_type / confidence-band + free search."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source_filter: set[str] = set()
        self.type_filter: set[str] = set()
        self.band_filter: set[str] = set()

    def set_dimension_filters(
        self,
        sources: set[str],
        types: set[str],
        bands: set[str],
    ) -> None:
        self.source_filter = set(sources)
        self.type_filter = set(types)
        self.band_filter = set(bands)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        m = self.sourceModel()
        if not isinstance(m, AuthorityMatchModel):
            return True
        if source_row >= len(m._rows):
            return True
        row = m._rows[source_row]
        if self.source_filter and str(row.get("source") or "") not in self.source_filter:
            return False
        if self.type_filter and str(row.get("match_type") or "") not in self.type_filter:
            return False
        if self.band_filter and _conf_band(row.get("confidence")) not in self.band_filter:
            return False
        return super().filterAcceptsRow(source_row, source_parent)


# ────────────────────────────────────────────────────────────────────────────
# Model
# ────────────────────────────────────────────────────────────────────────────

COL_RECORD = 0
COL_ENTITY = 1
COL_MATCH = 2
COL_SOURCE = 3
COL_TYPE = 4
COL_CONF = 5
COL_APPROVED = 6
COL_WIKIDATA_QID = 7
COL_ACTIONS = 8


class AuthorityMatchModel(QAbstractTableModel):
    """Flat model over authority matches, supporting approval + editing."""

    HEADERS = [
        "Record", "Entity", "Match", "Source", "Type", "Conf.", "Approved",
        "Wikidata QID", " ",
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._original: list[dict] = []
        self._records: list[dict] = []

    def load(self, records: list[dict]) -> None:
        self.beginResetModel()
        self._records = records
        self._rows = flatten_authority_records(records)
        self._original = copy.deepcopy(self._rows)
        self.endResetModel()

    def to_records(self) -> list[dict]:
        """Return records with ALL rows unfolded (approved or not)."""
        return unflatten_rows_into_records(
            [dict(r, approved=True) for r in self._rows], self._records,
        )

    def to_approved_records(self) -> list[dict]:
        """Return records with unapproved rows dropped — fed to Stage 3."""
        return unflatten_rows_into_records(self._rows, self._records)

    def is_dirty(self) -> bool:
        return self._rows != self._original

    def revert(self) -> None:
        self.beginResetModel()
        self._rows = copy.deepcopy(self._original)
        self.endResetModel()

    def set_approved_bulk(self, source_rows: list[int], approved: bool) -> int:
        if not source_rows:
            return 0
        changed = 0
        for r in source_rows:
            if 0 <= r < len(self._rows):
                if self._rows[r].get("approved", False) != approved:
                    self._rows[r]["approved"] = approved
                    changed += 1
        if changed:
            tl = self.index(0, 0)
            br = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(tl, br)
        return changed

    # ── QAbstractTableModel API ──────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self._rows)

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
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        r = self._rows[index.row()]
        col = index.column()

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == COL_RECORD:
                return r["_control_number"]
            if col == COL_ENTITY:
                return r["entity_text"]
            if col == COL_MATCH:
                mid = r.get("matched_id", "")
                mname = r.get("matched_name", "")
                if mname and mid:
                    return f"{mname} ({mid})"
                return mname or mid or "—"
            if col == COL_SOURCE:
                return r["source"]
            if col == COL_TYPE:
                return r["match_type"]
            if col == COL_CONF:
                c = r.get("confidence", 0.0)
                return f"{c:.2f}" if c else ""
            if col == COL_WIKIDATA_QID:
                return r.get("wikidata_qid", "")

        if role == Qt.ItemDataRole.UserRole:
            if col == COL_CONF:
                return r.get("confidence", 0.0)
            if col == COL_APPROVED:
                return int(bool(r.get("approved", False)))
            return self.data(index, Qt.ItemDataRole.DisplayRole)

        if role == Qt.ItemDataRole.CheckStateRole and col == COL_APPROVED:
            return (
                Qt.CheckState.Checked if r.get("approved", False)
                else Qt.CheckState.Unchecked
            )

        if role == Qt.ItemDataRole.BackgroundRole and r.get("approved", False):
            from mhm_pipeline.gui import theme  # noqa: PLC0415
            return QColor(22, 163, 74, 28 if theme.is_dark() else 18)

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
        if row >= len(self._rows):
            return False
        r = self._rows[row]
        if role == Qt.ItemDataRole.CheckStateRole and col == COL_APPROVED:
            r["approved"] = (Qt.CheckState(value) == Qt.CheckState.Checked)
            self.dataChanged.emit(index, index.siblingAtColumn(COL_ACTIONS))
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == COL_APPROVED:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base


# ────────────────────────────────────────────────────────────────────────────
# Popup dialogs
# ────────────────────────────────────────────────────────────────────────────


class MatchEditDialog(QDialog):
    """Edit a single authority match — entity text + matched name + external IDs."""

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self.setWindowTitle(f"Edit match — {row.get('_control_number','')}")
        self.resize(560, 420)
        self._row = copy.deepcopy(row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        form = QFormLayout()
        form.setSpacing(theme.SPACE_SM)

        self._entity_edit = QLineEdit(str(row.get("entity_text") or ""))
        form.addRow("Entity text:", self._entity_edit)

        self._matched_edit = QLineEdit(str(row.get("matched_name") or ""))
        form.addRow("Preferred name:", self._matched_edit)

        self._id_edit = QLineEdit(str(row.get("matched_id") or ""))
        self._id_edit.setPlaceholderText("Primary authority ID (Mazal ID or VIAF URI)")
        form.addRow("Matched ID:", self._id_edit)

        self._source_combo = QComboBox()
        self._source_combo.addItems(VALID_SOURCES)
        cur = str(row.get("source") or "")
        if cur in VALID_SOURCES:
            self._source_combo.setCurrentText(cur)
        form.addRow("Source:", self._source_combo)

        self._gnd_edit = QLineEdit(str(row.get("gnd_id") or ""))
        form.addRow("GND ID:", self._gnd_edit)

        layout.addLayout(form)

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

    def edited_row(self) -> dict:
        self._row["entity_text"] = self._entity_edit.text().strip()
        self._row["matched_name"] = self._matched_edit.text().strip()
        self._row["matched_id"] = self._id_edit.text().strip()
        self._row["source"] = self._source_combo.currentText()
        self._row["gnd_id"] = self._gnd_edit.text().strip()
        return self._row


class MatchSourceViewDialog(QDialog):
    """Show authority-record context for a single match row."""

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self.setWindowTitle(f"Match context — {row.get('_control_number','')}")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        summary = QLabel(
            f"<b>Entity:</b> {row.get('entity_text','')}"
            f"<br><b>Match:</b> {row.get('matched_name','')} "
            f"(<code>{row.get('matched_id','')}</code>)"
            f"<br><b>Source:</b> {row.get('source','')} · "
            f"<b>Type:</b> {row.get('match_type','')} · "
            f"<b>Conf.:</b> {row.get('confidence',0.0):.2f} "
            f"(band: {_conf_band(row.get('confidence'))})"
        )
        summary.setTextFormat(Qt.TextFormat.RichText)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        details = QTextEdit()
        details.setReadOnly(True)
        lines = [
            f"Record:        {row.get('_control_number','')}",
            f"Field origin:  {row.get('field_origin','')}",
            f"Role:          {row.get('role','')}",
            f"Dates:         {row.get('dates','')}",
            f"GND:           {row.get('gnd_id','')}",
            f"LCCN:          {row.get('lc_id','')}",
            f"ISNI:          {row.get('isni','')}",
            f"BnF:           {row.get('bnf_id','')}",
        ]
        details.setPlainText("\n".join(lines))
        layout.addWidget(details, stretch=1)

        close = QPushButton("Close")
        close.setStyleSheet(theme.button_style())
        close.clicked.connect(self.accept)
        bar = QHBoxLayout()
        bar.addStretch()
        bar.addWidget(close)
        layout.addLayout(bar)


# ────────────────────────────────────────────────────────────────────────────
# Auto-approve rule builder — specialised field set for authority stage
# ────────────────────────────────────────────────────────────────────────────

_AUTH_FIELDS: list[str] = ["confidence", "source", "match_type", "confidence_band", "has_external_id"]
_AUTH_FIELD_OPTIONS: dict[str, list[str]] = {
    "source": VALID_SOURCES,
    "match_type": VALID_MATCH_TYPES,
    "confidence_band": VALID_CONF_BANDS,
    "has_external_id": ["true", "false"],
}


class _AuthRuleRow(QWidget):
    """A single authority rule row (field · op · value · remove)."""

    removed = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        options_for: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self._options_for = options_for or {}
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(theme.SPACE_SM)

        self.field_combo = QComboBox()
        self.field_combo.addItems(_AUTH_FIELDS)
        self.field_combo.setMinimumWidth(140)
        self.field_combo.currentTextChanged.connect(self._refresh)
        h.addWidget(self.field_combo)

        self.op_combo = QComboBox()
        self.op_combo.setMinimumWidth(100)
        # Pre-populate the numeric op set so the combo is never empty on
        # first render — the default field is "confidence".
        self.op_combo.addItems([">", ">=", "=", "<=", "<", "≠"])
        self.op_combo.currentTextChanged.connect(self._refresh)
        h.addWidget(self.op_combo)

        from PyQt6.QtWidgets import QDoubleSpinBox  # noqa: PLC0415
        self.value_num = QDoubleSpinBox()
        self.value_num.setRange(0.0, 1.0)
        self.value_num.setSingleStep(0.05)
        self.value_num.setDecimals(2)
        self.value_num.setValue(0.80)
        h.addWidget(self.value_num, stretch=1)

        self.value_single = QComboBox()
        h.addWidget(self.value_single, stretch=1)

        self.value_multi = _CheckableMultiCombo([])
        h.addWidget(self.value_multi, stretch=1)

        self.value_text = QLineEdit()
        self.value_text.setPlaceholderText("value")
        h.addWidget(self.value_text, stretch=1)

        remove = QPushButton("×")
        remove.setFixedWidth(28)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setStyleSheet(theme.ghost_button_style())
        remove.clicked.connect(lambda: self.removed.emit(self))
        h.addWidget(remove)

        self._refresh()

    def _hide_all(self) -> None:
        self.value_num.setVisible(False)
        self.value_single.setVisible(False)
        self.value_multi.setVisible(False)
        self.value_text.setVisible(False)

    def _refresh(self, *_a: object) -> None:
        field = self.field_combo.currentText()
        # Always reconcile op_combo items with the current field. This used
        # to be guarded by a sender() check — unreliable in PyQt6 when the
        # slot is invoked directly, which left op_combo blank.
        expected_ops = (
            [">", ">=", "=", "<=", "<", "≠"] if field == "confidence"
            else ["=", "≠", "in", "not in"]
        )
        if [self.op_combo.itemText(i) for i in range(self.op_combo.count())] != expected_ops:
            current_op = self.op_combo.currentText()
            self.op_combo.blockSignals(True)
            self.op_combo.clear()
            self.op_combo.addItems(expected_ops)
            if current_op:
                idx = self.op_combo.findText(current_op)
                if idx >= 0:
                    self.op_combo.setCurrentIndex(idx)
            self.op_combo.blockSignals(False)

        op = self.op_combo.currentText()
        self._hide_all()
        if field == "confidence":
            self.value_num.setVisible(True)
            return

        options = self._options_for.get(field) or _AUTH_FIELD_OPTIONS.get(field)
        if options is None:
            self.value_text.setVisible(True)
            return
        if op in ("=", "≠"):
            cur = self.value_single.currentText()
            self.value_single.blockSignals(True)
            self.value_single.clear()
            self.value_single.addItems(options)
            if cur in options:
                self.value_single.setCurrentText(cur)
            self.value_single.blockSignals(False)
            self.value_single.setVisible(True)
        elif op in ("in", "not in"):
            snap = getattr(self.value_multi, "_items_snapshot", None)
            if snap != options:
                self.value_multi.setParent(None)
                self.value_multi.deleteLater()
                self.value_multi = _CheckableMultiCombo(options)
                self.value_multi._items_snapshot = list(options)  # type: ignore[attr-defined]
                self.layout().insertWidget(4, self.value_multi, 1)
            self.value_multi.setVisible(True)
        else:
            self.value_text.setVisible(True)

    def to_rule(self) -> dict[str, Any]:
        field = self.field_combo.currentText()
        op = self.op_combo.currentText()
        if field == "confidence":
            return {"field": field, "op": op, "value": self.value_num.value()}
        if op in ("=", "≠") and self.value_single.isVisible():
            return {"field": field, "op": op, "value": self.value_single.currentText()}
        if op in ("in", "not in") and self.value_multi.isVisible():
            return {"field": field, "op": op, "value": list(self.value_multi.checked_items())}
        raw = self.value_text.text().strip()
        if op in ("in", "not in"):
            return {"field": field, "op": op,
                    "value": [s.strip() for s in raw.split(",") if s.strip()]}
        return {"field": field, "op": op, "value": raw}


def evaluate_auth_rule(row: dict, rule: dict) -> bool:
    """Evaluate one authority-rule against a flat match row."""
    field = rule["field"]
    if field == "confidence_band":
        band = _conf_band(row.get("confidence"))
        return evaluate_rule({"confidence_band": band}, rule)
    if field == "has_external_id":
        has = bool(row.get("matched_id"))
        return evaluate_rule({"has_external_id": "true" if has else "false"}, rule)
    return evaluate_rule(row, rule)


def evaluate_auth_rules(row: dict, rules: list[dict], combinator: str) -> bool:
    if not rules:
        return False
    results = [evaluate_auth_rule(row, r) for r in rules]
    return all(results) if combinator == "AND" else any(results)


class AuthorityAutoApproveDialog(QDialog):
    """Multi-condition builder tailored to authority match fields."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        options_for: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self.setWindowTitle("Auto-approve authority matches")
        self.resize(720, 420)
        self._options_for = options_for or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        info = QLabel(
            "Approve every match that satisfies all (or any) of the "
            "conditions. Use ``confidence_band`` for high/medium/low/no_match, "
            "``has_external_id`` to require a Mazal/VIAF/KIMA ID."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {theme.ui('subtext')};")
        layout.addWidget(info)

        comb = QHBoxLayout()
        comb.setSpacing(theme.SPACE_SM)
        comb.addWidget(QLabel("Combine with:"))
        self.combinator = QComboBox()
        self.combinator.addItems(["AND", "OR"])
        comb.addWidget(self.combinator)
        comb.addStretch()
        layout.addLayout(comb)

        self._rules_container = QWidget()
        self._rules_layout = QVBoxLayout(self._rules_container)
        self._rules_layout.setContentsMargins(0, 0, 0, 0)
        self._rules_layout.setSpacing(theme.SPACE_SM)
        self._rule_widgets: list[_AuthRuleRow] = []
        sa = QScrollArea()
        sa.setWidget(self._rules_container)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setStyleSheet("QScrollArea { background: transparent; }")
        layout.addWidget(sa, stretch=1)

        bottom = QHBoxLayout()
        bottom.setSpacing(theme.SPACE_SM)
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
        r = _AuthRuleRow(options_for=self._options_for)
        r.removed.connect(self._remove_rule)
        self._rule_widgets.append(r)
        self._rules_layout.addWidget(r)

    def _remove_rule(self, w: _AuthRuleRow) -> None:
        if w in self._rule_widgets:
            self._rule_widgets.remove(w)
            self._rules_layout.removeWidget(w)
            w.deleteLater()

    def rules(self) -> list[dict[str, Any]]:
        return [w.to_rule() for w in self._rule_widgets]

    def combinator_value(self) -> str:
        return self.combinator.currentText()


# ────────────────────────────────────────────────────────────────────────────
# Main editor widget
# ────────────────────────────────────────────────────────────────────────────


class AuthorityEditor(QWidget):
    """Review surface for authority matches — mirrors ``ExtractionEditor``."""

    entities_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)

        # Header
        header = QHBoxLayout()
        header.setSpacing(theme.SPACE_SM)
        self._stats = QLabel("No authority matches loaded")
        header.addWidget(self._stats)
        header.addStretch()

        def _ghost(text: str, on_click: Any) -> QPushButton:
            btn = QPushButton(text)
            btn.setStyleSheet(theme.ghost_button_style())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(on_click)
            return btn

        header.addWidget(_ghost("⚡ Auto-approve…", self._on_auto_approve))
        header.addWidget(_ghost("Approve visible", lambda: self._set_visible(True)))
        header.addWidget(_ghost("Clear approval", lambda: self._set_visible(False)))
        header.addWidget(_ghost("Revert", self._on_revert))
        self._save_btn = QPushButton("Save")
        self._save_btn.setStyleSheet(theme.success_btn_style())
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)
        header.addWidget(self._save_btn)
        layout.addLayout(header)

        # Search
        search = QHBoxLayout()
        search.setSpacing(theme.SPACE_SM)
        search.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by record, entity, match, ID…")
        self._search_edit.textChanged.connect(self._on_search)
        search.addWidget(self._search_edit)
        layout.addLayout(search)

        # Table
        self._model = AuthorityMatchModel()
        self._proxy = AuthorityFilterProxy()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)
        self._proxy.setSortRole(Qt.ItemDataRole.UserRole)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        h = self._table.horizontalHeader()
        assert h is not None
        h.setSectionResizeMode(COL_RECORD, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_ENTITY, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_MATCH, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_CONF, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_APPROVED, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_WIKIDATA_QID, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_ACTIONS, 78)

        layout.addWidget(self._table, stretch=1)

        self._output_path: Path | None = None
        self._model.dataChanged.connect(self._update_stats)
        self._model.modelReset.connect(self._refresh_actions)
        self._model.rowsInserted.connect(self._refresh_actions)
        self._model.rowsRemoved.connect(self._refresh_actions)

    # ── Public API ───────────────────────────────────────────────────────

    def load_records(self, records: list[dict], output_path: Path | None = None) -> None:
        self._model.load(records)
        self._output_path = output_path
        self._refresh_actions()
        self._update_stats()

    def get_all_sources(self) -> list[str]:
        return sorted({r.get("source") or "" for r in self._model._rows if r.get("source")})

    def get_all_types(self) -> list[str]:
        return sorted({r.get("match_type") or "" for r in self._model._rows if r.get("match_type")})

    def get_all_bands(self) -> list[str]:
        return sorted({_conf_band(r.get("confidence")) for r in self._model._rows})

    def apply_filters(
        self,
        sources: set[str] | None,
        types: set[str] | None,
        bands: set[str] | None,
    ) -> None:
        self._proxy.set_dimension_filters(
            set(sources or ()), set(types or ()), set(bands or ()),
        )
        self._refresh_actions()
        self._update_stats()

    # ── Actions column (✎ Edit · ↗ View) ─────────────────────────────────

    def _refresh_actions(self) -> None:
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        for row in range(self._proxy.rowCount()):
            idx = self._proxy.index(row, COL_ACTIONS)
            self._table.setIndexWidget(idx, None)

        btn_qss = (
            f"QPushButton {{ background: transparent;"
            f" color: {theme.ui('text')};"
            f" border: 1px solid {theme.ui('border')};"
            f" border-radius: {theme.RADIUS_SM}px;"
            f" font-size: {theme.FONT_BASE}px;"
            f" font-weight: 600; padding: 0 4px;"
            f" min-height: 22px; min-width: 24px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,18);"
            f" border-color: {theme.ui('highlight')}; }}"
        )

        for row in range(self._proxy.rowCount()):
            idx = self._proxy.index(row, COL_ACTIONS)
            container = QWidget()
            h = QHBoxLayout(container)
            h.setContentsMargins(2, 1, 2, 1)
            h.setSpacing(4)

            edit_btn = QPushButton("✎")
            edit_btn.setToolTip("Edit match")
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.setStyleSheet(btn_qss)
            edit_btn.clicked.connect(lambda _=False, r=row: self._on_edit(r))
            h.addWidget(edit_btn)

            view_btn = QPushButton("↗")
            view_btn.setToolTip("View match context")
            view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            view_btn.setStyleSheet(btn_qss)
            view_btn.clicked.connect(lambda _=False, r=row: self._on_view(r))
            h.addWidget(view_btn)

            compare_btn = QPushButton("🧬")
            compare_btn.setToolTip(
                "Compare biographical data (dates, places, names, "
                "occupations) between the MARC record and the matched "
                "authority — makes approval decisions faster."
            )
            compare_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            compare_btn.setStyleSheet(btn_qss)
            compare_btn.clicked.connect(lambda _=False, r=row: self._on_compare(r))
            h.addWidget(compare_btn)

            self._table.setIndexWidget(idx, container)

    def _proxy_to_source(self, proxy_row: int) -> int:
        idx = self._proxy.index(proxy_row, COL_ACTIONS)
        return self._proxy.mapToSource(idx).row()

    def _on_edit(self, proxy_row: int) -> None:
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return
        dlg = MatchEditDialog(self._model._rows[src], parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.edited_row()
        self._model._rows[src] = new_row
        self._model.dataChanged.emit(
            self._model.index(src, 0),
            self._model.index(src, self._model.columnCount() - 1),
        )
        self.entities_changed.emit()
        self._update_stats()

    def _on_view(self, proxy_row: int) -> None:
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return
        MatchSourceViewDialog(self._model._rows[src], parent=self).exec()

    def _on_compare(self, proxy_row: int) -> None:
        """Open the biodata comparison dialog for this match.

        MARC-side data is extracted synchronously from the already-
        loaded record and rendered immediately; the dialog is usable
        even if the async authority fetch is slow or fails. The
        authority side fills in when the VIAF/Mazal fetch resolves
        (or stays blank for ``marc_field`` matches, which have no
        external counterpart). The dialog exposes Approve + Next so
        the reviewer can bulk-process without closing between rows.
        """
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return

        from mhm_pipeline.gui.widgets.match_comparison_dialog import (  # noqa: PLC0415
            MatchComparisonDialog,
        )

        dlg = MatchComparisonDialog(
            self._model._rows[src],
            parent=self,
            on_approve=self._compare_approve_handler,
            on_next=self._compare_next_handler,
        )
        self._hydrate_compare_dialog(dlg, src)
        dlg.exec()

    def _hydrate_compare_dialog(self, dlg, src: int) -> None:
        """Populate *dlg* with MARC + authority data for source row *src*.

        Called both on initial open and when the user clicks → Next.
        """
        from mhm_pipeline.gui.widgets.match_comparison_dialog import (  # noqa: PLC0415
            fetch_biodata_async,
        )
        from converter.authority.biodata import (  # noqa: PLC0415
            BioComparison, BioData, extract_marc_biodata,
        )

        row = self._model._rows[src]

        # Find the MARC record that hosts this match
        cn = str(row.get("_control_number", ""))
        marc_record: dict | None = None
        for r in self._model._records:
            if str(r.get("_control_number", "")) == cn:
                marc_record = r
                break

        source = str(row.get("source", ""))
        auth_id = str(row.get("matched_id", ""))
        if source == "viaf" and "/" in auth_id:
            auth_id = auth_id.rstrip("/").split("/")[-1]

        marc_bio = extract_marc_biodata(marc_record, row=row)
        initial = BioComparison(
            marc=marc_bio, authority=BioData(), source=source,
        )
        dlg.load_row(row, comparison=initial)

        if source in ("marc_field", "") or not auth_id:
            # No async work — keep the dialog synchronous
            dlg._progress.setVisible(False)  # type: ignore[attr-defined]
            return

        viaf_fetcher = self._make_viaf_fetcher()
        mazal_fetcher = self._make_mazal_fetcher()
        kima_fetcher = self._make_kima_fetcher()

        signals = fetch_biodata_async(
            source=source, auth_id=auth_id, marc_record=marc_record,
            viaf_fetcher=viaf_fetcher,
            mazal_fetcher=mazal_fetcher,
            kima_fetcher=kima_fetcher,
        )
        # Reference pinning — without this, Python may GC the signals
        # holder before the QRunnable queues.
        dlg._bio_signals = signals  # type: ignore[attr-defined]

        def _on_ready(_s: str, _i: str, cmp_: object) -> None:
            merged = BioComparison(
                marc=marc_bio, authority=cmp_.authority, source=cmp_.source,
            )
            dlg.show_comparison(merged)

        signals.ready.connect(_on_ready)
        signals.failed.connect(
            lambda _s, _i, msg: dlg.show_error(msg),
        )

    def _compare_approve_handler(self, row: dict) -> None:
        """Flip the approved flag on the row in the model + refresh view."""
        for i, r in enumerate(self._model._rows):
            if r is row:
                r["approved"] = True
                tl = self._model.index(i, 0)
                br = self._model.index(i, self._model.columnCount() - 1)
                self._model.dataChanged.emit(tl, br)
                self.entities_changed.emit()
                self._update_stats()
                break

    def _compare_next_handler(self, row: dict) -> dict | None:
        """Return the next row + hydrate the caller's dialog.

        The returned dict ``{"row": ..., "comparison": BioComparison,
        "show_progress": bool}`` is what :meth:`MatchComparisonDialog
        .load_row` expects. We hydrate the dialog asynchronously; for
        VIAF/Mazal rows the dialog shows the MARC side immediately +
        a spinner.
        """
        current_src = -1
        for i, r in enumerate(self._model._rows):
            if r is row:
                current_src = i
                break
        if current_src < 0:
            return None
        next_src = self._find_next_compare_row(current_src)
        if next_src is None:
            return None

        # Hydrate via the same path used for initial open — pull the
        # parent dialog from the caller's stack via self.focusWidget()
        # fallback. Simpler: we just rebuild the initial comparison
        # here and let load_row apply it.
        from converter.authority.biodata import (  # noqa: PLC0415
            BioComparison, BioData, extract_marc_biodata,
        )

        next_row = self._model._rows[next_src]
        cn = str(next_row.get("_control_number", ""))
        marc_record: dict | None = None
        for r in self._model._records:
            if str(r.get("_control_number", "")) == cn:
                marc_record = r
                break
        marc_bio = extract_marc_biodata(marc_record, row=next_row)
        initial = BioComparison(
            marc=marc_bio, authority=BioData(), source=str(next_row.get("source", "")),
        )

        # Kick off the async fetch so that by the time the dialog's
        # load_row returns, the authority side will start filling in
        # through the dialog's existing signals wiring. To keep the
        # signals attached to THIS dialog we reach up the widget tree
        # — the parent of the caller row is self, and the active modal
        # child of self is the dialog.
        dlg = None
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

        for w in QApplication.topLevelWidgets():
            if w.__class__.__name__ == "MatchComparisonDialog" and w.isVisible():
                dlg = w
                break
        if dlg is not None:
            self._hydrate_compare_dialog(dlg, next_src)

        return {
            "row": next_row,
            "comparison": initial,
            "show_progress": str(next_row.get("source", ""))
                not in ("marc_field", "")
                and bool(next_row.get("matched_id")),
        }

    def _find_next_compare_row(self, current_src: int) -> int | None:
        """Return the index of the next row after *current_src* that has
        a matched entity worth comparing. Wraps at the end."""
        n = len(self._model._rows)
        if n == 0:
            return None
        for offset in range(1, n + 1):
            idx = (current_src + offset) % n
            if idx == current_src:
                return None
            r = self._model._rows[idx]
            if r.get("entity_text") or r.get("matched_name"):
                return idx
        return None

    def _make_viaf_fetcher(self):  # noqa: ANN001
        """Return a callable ``id -> raw_cluster_dict`` or ``None`` if
        the VIAF matcher can't be constructed (offline mode)."""
        try:
            from converter.authority.viaf_matcher import VIAFMatcher  # noqa: PLC0415
            if not hasattr(self, "_viaf_matcher") or self._viaf_matcher is None:
                self._viaf_matcher = VIAFMatcher()
            return self._viaf_matcher.get_cluster_biodata
        except Exception:
            return None

    def _make_mazal_fetcher(self):  # noqa: ANN001
        """Return a thread-safe Mazal fetcher.

        SQLite connections are bound to the thread that created them —
        reusing a single connection across the main thread and a
        QThreadPool worker raises ``SQLite objects created in a thread
        can only be used in that same thread``. Opening a fresh
        connection per call is cheap (~2 ms) and the dialog already
        caches results in :data:`match_comparison_dialog._CACHE`, so
        on the steady state this only fires on the first miss per
        authority ID.
        """
        from pathlib import Path as _Path  # noqa: PLC0415

        db_path = str(
            _Path(__file__).resolve().parents[4]
            / "converter/authority/mazal_index.db"
        )

        def _fetch(auth_id: str) -> dict | None:
            try:
                from converter.authority.mazal_index import MazalIndex  # noqa: PLC0415

                with MazalIndex(db_path) as idx:
                    return idx.get_record(auth_id)
            except Exception:
                return None

        return _fetch

    def _make_kima_fetcher(self):  # noqa: ANN001
        # KIMA currently resolves by name, not by ID; return None until
        # lookup_by_id is added.
        return None

    # ── Auto-approve ─────────────────────────────────────────────────────

    def _on_auto_approve(self) -> None:
        options_for = {
            "source": self.get_all_sources(),
            "match_type": self.get_all_types(),
            "confidence_band": self.get_all_bands(),
        }
        dlg = AuthorityAutoApproveDialog(self, options_for=options_for)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rules = dlg.rules()
        combinator = dlg.combinator_value()
        matched = [
            i for i, r in enumerate(self._model._rows)
            if evaluate_auth_rules(r, rules, combinator)
        ]
        changed = self._model.set_approved_bulk(matched, True)
        self._update_stats()
        self.entities_changed.emit()
        QMessageBox.information(
            self, "Auto-approve",
            f"Approved {changed} match{'es' if changed != 1 else ''} "
            f"matching the rules.",
        )

    def _set_visible(self, approved: bool) -> None:
        rows: list[int] = []
        for r in range(self._proxy.rowCount()):
            rows.append(self._proxy_to_source(r))
        changed = self._model.set_approved_bulk(rows, approved)
        self._update_stats()
        self.entities_changed.emit()
        verb = "Approved" if approved else "Cleared approval on"
        QMessageBox.information(
            self, "Bulk approval",
            f"{verb} {changed} visible match{'es' if changed != 1 else ''}.",
        )

    # ── Stats + CRUD ─────────────────────────────────────────────────────

    def _update_stats(self) -> None:
        total = self._model.rowCount()
        visible = self._proxy.rowCount() if self._proxy else total
        approved = sum(1 for r in self._model._rows if r.get("approved", False))
        dirty = " (modified)" if self._model.is_dirty() else ""
        pct = (approved / total * 100) if total else 0.0
        if visible == total:
            self._stats.setText(f"{total} matches · {approved} approved ({pct:.0f}%){dirty}")
        else:
            self._stats.setText(f"{visible} of {total} visible · {approved} approved ({pct:.0f}%){dirty}")

    def _on_search(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        self._refresh_actions()
        self._update_stats()

    def _on_revert(self) -> None:
        if not self._model.is_dirty():
            return
        if QMessageBox.question(
            self, "Revert Changes",
            "Discard all edits and revert to the original matches?",
        ) == QMessageBox.StandardButton.Yes:
            self._model.revert()
            self._refresh_actions()
            self._update_stats()

    def _on_save(self) -> None:
        if not self._output_path:
            return
        total = len(self._model._rows)
        approved = sum(1 for r in self._model._rows if r.get("approved", False))
        rejected = total - approved
        if rejected > 0:
            if QMessageBox.question(
                self, "Save approved matches only",
                f"Save will keep {approved} approved match"
                f"{'es' if approved != 1 else ''} and drop "
                f"{rejected} unapproved from the output file.\n\nProceed?",
            ) != QMessageBox.StandardButton.Yes:
                return
        records = self._model.to_approved_records()
        self._output_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._model._original = copy.deepcopy(self._model._rows)
        self._update_stats()
        logger.info(
            "Saved %d approved authority matches (%d dropped) to %s",
            approved, rejected, self._output_path,
        )
        QMessageBox.information(
            self, "Saved",
            f"Saved {approved} approved match{'es' if approved != 1 else ''}"
            f" ({rejected} dropped) to\n{self._output_path}",
        )
