"""Q/P entity browser — editable Wikidata-item review surface.

Shows every ``WikidataItem`` produced by ``WikidataItemBuilder`` as a row:

    Local-ID · Type · Label · #claims · External-ID · Status · Approved · ✎↗

"Status" reflects live Wikidata reconciliation:

    new            — no matching QID found → will be created
    existing-ours  — matching QID exists and first revision is our user →
                     we may update it
    existing-other — matching QID exists but first revision is someone
                     else's → must be skipped (safety rule 23)
    unknown        — not yet checked

Clicking ``✎ Edit`` opens a claim editor; ``↗`` opens a raw-claims
inspector with QID/PID chips, qualifiers, and references.

Follows the same approve-before-flow pattern as ``ExtractionEditor`` and
``AuthorityEditor`` — bulk approval + auto-approve rules + save filters
unapproved rows out of the export.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
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
    QTableView,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# Reuse rule primitives from the NER editor — identical semantics.
from mhm_pipeline.gui.widgets.extraction_editor import (
    _CheckableMultiCombo,
    evaluate_rules,
)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Column indices
# ────────────────────────────────────────────────────────────────────────────

COL_LOCAL_ID = 0
COL_TYPE = 1
COL_LABEL = 2
COL_NCLAIMS = 3
COL_EXT_ID = 4
COL_STATUS = 5
COL_ISSUES = 6
COL_APPROVED = 7
COL_ACTIONS = 8

_STATUS_NEW = "new"
_STATUS_OURS = "existing-ours"
_STATUS_OTHER = "existing-other"
_STATUS_UNKNOWN = "unknown"

_STATUS_COLORS: dict[str, tuple[str, str]] = {
    # (light_bg, light_fg) — dark mode flips these via _status_colors()
    _STATUS_NEW:     ("#dbeafe", "#1e3a8a"),
    _STATUS_OURS:    ("#dcfce7", "#14532d"),
    _STATUS_OTHER:   ("#fee2e2", "#7f1d1d"),
    _STATUS_UNKNOWN: ("#f3f4f6", "#374151"),
}


def _status_colors(status: str) -> tuple[str, str]:
    from mhm_pipeline.gui import theme  # noqa: PLC0415

    if theme.is_dark():
        mapping = {
            _STATUS_NEW:     ("#1e3a8a", "#dbeafe"),
            _STATUS_OURS:    ("#14532d", "#dcfce7"),
            _STATUS_OTHER:   ("#7f1d1d", "#fee2e2"),
            _STATUS_UNKNOWN: ("#374151", "#f3f4f6"),
        }
        return mapping.get(status, mapping[_STATUS_UNKNOWN])
    return _STATUS_COLORS.get(status, _STATUS_COLORS[_STATUS_UNKNOWN])


_SEVERITY_COLORS_LIGHT: dict[str, tuple[str, str]] = {
    "error":   ("#fee2e2", "#7f1d1d"),
    "warning": ("#fef3c7", "#78350f"),
    "ok":      ("#dcfce7", "#14532d"),
}

_SEVERITY_COLORS_DARK: dict[str, tuple[str, str]] = {
    "error":   ("#7f1d1d", "#fee2e2"),
    "warning": ("#78350f", "#fef3c7"),
    "ok":      ("#14532d", "#dcfce7"),
}


def _severity_colors(severity: str) -> tuple[str, str]:
    from mhm_pipeline.gui import theme  # noqa: PLC0415

    src = _SEVERITY_COLORS_DARK if theme.is_dark() else _SEVERITY_COLORS_LIGHT
    return src.get(severity, src["ok"])


def _install_glass_backdrop(dialog: QDialog) -> QWidget:
    """Wrap *dialog* in the same liquid-glass backdrop the main window uses.

    Creates ``GraphBackdrop`` as the dialog's only direct child, then
    returns a translucent content widget that callers can use as the
    parent for their real UI. Matches the pattern in
    :mod:`mhm_pipeline.gui.panels.wikidata_studio_panel` so every popup
    shares the node-and-line aesthetic of the app chrome.
    """
    from mhm_pipeline.gui.widgets.graph_backdrop import GraphBackdrop  # noqa: PLC0415

    backdrop = GraphBackdrop(parent=dialog)
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(backdrop)

    content = QWidget(backdrop)
    content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    backdrop_layout = QVBoxLayout(backdrop)
    backdrop_layout.setContentsMargins(0, 0, 0, 0)
    backdrop_layout.addWidget(content)
    return content


def _glass_table_style(theme_mod: Any) -> str:
    """Translucent QTableView QSS so the graph backdrop lenses through."""
    return (
        f"QTableView {{"
        f" background: rgba(0,0,0, 90);"
        f" alternate-background-color: rgba(255,255,255, 10);"
        f" color: {theme_mod.ui('text')};"
        f" gridline-color: rgba(255,255,255, 18);"
        f" border: 1px solid rgba(255,255,255, 22);"
        f" border-radius: {theme_mod.RADIUS_MD}px;"
        f" selection-background-color: rgba(99, 102, 241, 120);"
        f" selection-color: white;"
        f" }}"
        f"QHeaderView::section {{"
        f" background: rgba(255,255,255, 12);"
        f" color: {theme_mod.ui('text')};"
        f" padding: 6px 8px;"
        f" border: none;"
        f" border-bottom: 1px solid rgba(255,255,255, 22);"
        f" font-weight: 600;"
        f" }}"
        f"QTableView::item {{"
        f" padding: 4px 8px;"
        f" border: none;"
        f" }}"
        f"QTableCornerButton::section {{"
        f" background: rgba(255,255,255, 10);"
        f" border: none;"
        f" }}"
    )


# ────────────────────────────────────────────────────────────────────────────
# Normalisation: flatten WikidataItem → row dict
# ────────────────────────────────────────────────────────────────────────────


def serialize_validated_rows(rows: list[dict]) -> list[dict]:
    """Serialise ``QPEntityModel._rows`` entries to a JSON-friendly list.

    Round-trippable with the uploader's input format: each entry carries
    the full ``WikidataItem`` (labels / descriptions / aliases / statements
    with qualifiers + references), the local ID, entity type, and the
    full validation payload.
    """
    def _item_to_dict(it: Any) -> dict:
        if it is None:
            return {}
        return {
            "local_id": str(getattr(it, "local_id", "") or ""),
            "entity_type": str(getattr(it, "entity_type", "") or ""),
            "existing_qid": getattr(it, "existing_qid", "") or "",
            "labels": dict(getattr(it, "labels", {}) or {}),
            "descriptions": dict(getattr(it, "descriptions", {}) or {}),
            "aliases": {
                k: list(v) for k, v in (getattr(it, "aliases", {}) or {}).items()
            },
            "statements": [
                {
                    "property_id": getattr(s, "property_id", ""),
                    "value": getattr(s, "value", ""),
                    "value_type": getattr(s, "value_type", ""),
                    "qualifiers": list(getattr(s, "qualifiers", []) or []),
                    "references": list(getattr(s, "references", []) or []),
                }
                for s in (getattr(it, "statements", []) or [])
            ],
        }

    def _issues_to_json(issues: list[Any]) -> list[dict]:
        return [
            {
                "severity": getattr(i, "severity", ""),
                "code": getattr(i, "code", ""),
                "message": getattr(i, "message", ""),
                "reference": getattr(i, "reference", ""),
            }
            for i in (issues or [])
        ]

    out: list[dict] = []
    for r in rows:
        validation_payload = r.get("validation")
        if validation_payload is not None:
            payload = dict(validation_payload)
            payload["validator_issues"] = _issues_to_json(
                payload.get("validator_issues") or r.get("issues"),
            )
        else:
            payload = None
        out.append({
            "local_id": r.get("local_id", ""),
            "entity_type": r.get("entity_type", ""),
            "label": r.get("label", ""),
            "description": r.get("description", ""),
            "status": r.get("status", ""),
            "status_reason": r.get("status_reason", ""),
            "severity": r.get("severity", "ok"),
            "approved": bool(r.get("approved", False)),
            "item": _item_to_dict(r.get("_item")),
            "validation": payload,
            "issues": _issues_to_json(r.get("issues") or []),
        })
    return out


def flatten_items(items: list[Any]) -> list[dict]:
    """Convert ``list[WikidataItem]`` into flat row dicts for the model.

    Each row carries the ``_item`` reference, the flattened columns used
    by the model, and the output of the :mod:`converter.wikidata.item_validator`
    which maps 1:1 to the community-raised failure modes
    (prohibited P3959 on humans, kovetz placeholder labels,
    institutional → Q5, etc.).
    """
    from converter.wikidata.item_validator import validate_item, worst_severity  # noqa: PLC0415

    rows: list[dict] = []
    for item in items:
        labels = getattr(item, "labels", {}) or {}
        label = labels.get("he") or labels.get("en") or next(iter(labels.values()), "") or ""
        descriptions = getattr(item, "descriptions", {}) or {}
        desc = descriptions.get("en") or descriptions.get("he") or ""
        statements = getattr(item, "statements", []) or []
        # Pull the most informative external ID for the row summary
        ext_id = ""
        for pid in ("P214", "P8189", "P244", "P227", "P213", "P217"):
            for s in statements:
                if getattr(s, "property_id", "") == pid:
                    ext_id = f"{pid}: {getattr(s, 'value', '')}"
                    break
            if ext_id:
                break
        issues = validate_item(item)
        severity = worst_severity(issues)
        # ── Wikidata completeness signals (based on WikiProject Manuscripts
        #    Data Model + WikiProject Authority Control research) ──
        pids = {getattr(s, "property_id", "") for s in statements}
        id_pids = {"P214", "P8189", "P244", "P227", "P213", "P268"}
        n_identifiers = len(pids & id_pids)
        labels_dict = getattr(item, "labels", {}) or {}
        descs_dict = getattr(item, "descriptions", {}) or {}
        # Minimum reference count across all statements (0 = at least one
        # statement has no reference — Bot sourcing-requirements RfC).
        min_refs = min(
            (len(getattr(s, "references", []) or []) for s in statements),
            default=0,
        )
        rows.append({
            "local_id": str(getattr(item, "local_id", "") or ""),
            "entity_type": str(getattr(item, "entity_type", "") or ""),
            "label": str(label),
            "description": str(desc),
            "n_claims": len(statements),
            "n_identifiers": n_identifiers,
            "n_references_min": min_refs,
            "label_length_he": len(str(labels_dict.get("he") or "")),
            "label_length_en": len(str(labels_dict.get("en") or "")),
            "description_length_en": len(str(descs_dict.get("en") or "")),
            "has_instance_of": "P31" in pids,
            "has_collection": "P195" in pids,
            "has_inventory_number": "P217" in pids,
            "has_title": "P1476" in pids,
            "has_exemplar_of": "P1574" in pids,
            "has_inception": "P571" in pids,
            "has_location_of_creation": "P1071" in pids,
            "has_author": "P50" in pids,
            "ext_id": ext_id,
            "existing_qid": getattr(item, "existing_qid", "") or "",
            "status": _STATUS_OURS if getattr(item, "existing_qid", "") else _STATUS_UNKNOWN,
            "status_reason": "",
            "issues": issues,
            "severity": severity,
            "approved": False,
            # Live-validation payload — filled by _ValidationWorker.row_validated.
            # Presence of ``validation`` != None means the Validate-with-Wikidata
            # pass has run for this row (drives ℹ icon visibility).
            "validation": None,
            "identifier_checks": {},           # {pid: {"proposed", "existing", "verdict"}}
            "label_collision": None,           # {"lang", "proposed_label", "proposed_desc", "collisions": [qid]}
            "sparql_ask": None,
            "sparql_answer": None,             # True / False / None
            "creator_check": None,             # {"auth_user", "first_rev_author", "contribs_new", "verdict"}
            "validated_at": None,              # ISO timestamp
            "validation_latency_ms": None,
            "_item": item,
        })
    return rows


# ────────────────────────────────────────────────────────────────────────────
# Model + proxy
# ────────────────────────────────────────────────────────────────────────────


class _PaginationProxy(QSortFilterProxyModel):
    """Chained proxy that shows only one page of its source.

    Placed AFTER the entity-filter proxy::

        QPEntityModel → QPEntityFilterProxy → _PaginationProxy → QTableView

    Rendering 5000+ rows with per-row action widgets is what kills the
    UI. Truncating rowCount() at the proxy layer means the view itself
    only materialises ``page_size`` rows (25 / 50 / 100), which is where
    Qt model/view performance is actually fast.
    """

    page_changed = pyqtSignal(int, int)   # (page, total_pages)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = 0             # zero-indexed
        self._page_size = 50

    def set_page_size(self, size: int) -> None:
        if size == self._page_size:
            return
        self._page_size = max(1, int(size))
        self._page = 0
        self.invalidateFilter()
        self.page_changed.emit(self._page, self.total_pages())

    def set_page(self, page: int) -> None:
        page = max(0, min(page, self.total_pages() - 1))
        if page == self._page:
            return
        self._page = page
        self.invalidateFilter()
        self.page_changed.emit(self._page, self.total_pages())

    def page(self) -> int:
        return self._page

    def page_size(self) -> int:
        return self._page_size

    def total_rows(self) -> int:
        src = self.sourceModel()
        return src.rowCount() if src is not None else 0

    def total_pages(self) -> int:
        n = self.total_rows()
        if n == 0:
            return 1
        return (n + self._page_size - 1) // self._page_size

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        offset = self._page * self._page_size
        return offset <= source_row < offset + self._page_size

    def sort(  # noqa: D401
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        """Delegate sort to the source proxy so the ordering applies to
        ALL filtered rows, not just the current page's 50-row window.
        Without this override, clicking a column header only re-orders
        whatever is currently on screen — exactly the bug the user hit.
        """
        src = self.sourceModel()
        if src is not None:
            src.sort(column, order)
        # Re-window onto the now-sorted source — jump back to page 0 so
        # the user sees the top of the sorted order, not a random page.
        self._page = 0
        self.invalidateFilter()
        self.page_changed.emit(self._page, self.total_pages())


class QPEntityFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.type_filter: set[str] = set()
        self.status_filter: set[str] = set()

    def set_dimension_filters(
        self,
        types: set[str],
        statuses: set[str],
    ) -> None:
        self.type_filter = set(types)
        self.status_filter = set(statuses)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, QPEntityModel):
            return True
        if source_row >= len(model._rows):
            return True
        row = model._rows[source_row]
        if self.type_filter and str(row.get("entity_type") or "") not in self.type_filter:
            return False
        if self.status_filter and str(row.get("status") or "") not in self.status_filter:
            return False
        return super().filterAcceptsRow(source_row, source_parent)


class QPEntityModel(QAbstractTableModel):
    """Flat model over WikidataItem rows."""

    HEADERS = ["Local ID", "Type", "Label", "#Claims", "Ext. ID",
               "Status", "Issues", "Approved", " "]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._original: list[dict] = []

    def load(self, items: list[Any]) -> None:
        self.beginResetModel()
        self._rows = flatten_items(items)
        self._original = copy.deepcopy([
            {k: v for k, v in r.items() if k != "_item"} for r in self._rows
        ])
        self.endResetModel()

    def items(self) -> list[Any]:
        return [r["_item"] for r in self._rows]

    def approved_items(self) -> list[Any]:
        """Items approved, not owned by others, and without error-level issues."""
        return [
            r["_item"] for r in self._rows
            if r.get("approved", False)
            and r.get("status") != _STATUS_OTHER
            and r.get("severity") != "error"
        ]

    def update_status(self, local_id: str, status: str, qid: str = "", reason: str = "") -> None:
        """Legacy thin-payload setter. Kept for backward-compat with the
        old ``_DuplicateCheckWorker.status`` 4-tuple signal. Internally
        delegates to :meth:`update_validation` with a minimal payload."""
        self.update_validation(local_id, {
            "status": status,
            "status_reason": reason,
            "matched_qid": qid,
        })

    def update_validation(self, local_id: str, payload: dict) -> None:
        """Apply a validation payload to the row identified by *local_id*.

        The payload is the full dict emitted by ``_ValidationWorker.row_validated``.
        Flat columns (status, status_reason, existing_qid) are updated so
        the existing view stays in sync; the whole payload is stored on
        the row so the ℹ-icon popup can surface every detail.

        dataChanged is emitted across the full row so status colour,
        issues cell, and the ℹ-glyph decoration all refresh.
        """
        for i, r in enumerate(self._rows):
            if r["local_id"] != local_id:
                continue
            if "status" in payload:
                r["status"] = payload["status"]
            if "status_reason" in payload:
                r["status_reason"] = payload["status_reason"]
            matched_qid = payload.get("matched_qid")
            if matched_qid:
                r["existing_qid"] = matched_qid
            # Full payload + flat-mirrored sub-fields for fast rendering
            r["validation"] = payload
            for k in (
                "identifier_checks", "label_collision", "sparql_ask",
                "sparql_answer", "creator_check", "validated_at",
                "validation_latency_ms",
            ):
                if k in payload:
                    r[k] = payload[k]
            # Validator issues may have been re-run with live data;
            # refresh severity to match.
            fresh_issues = payload.get("validator_issues")
            if fresh_issues is not None:
                r["issues"] = fresh_issues
                from converter.wikidata.item_validator import worst_severity  # noqa: PLC0415
                r["severity"] = worst_severity(fresh_issues)
            tl = self.index(i, 0)
            br = self.index(i, self.columnCount() - 1)
            self.dataChanged.emit(tl, br)
            break

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

    # ── QAbstractTableModel ──────────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self.HEADERS)

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation,
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
            if col == COL_LOCAL_ID:
                return r["local_id"]
            if col == COL_TYPE:
                return r["entity_type"].title()
            if col == COL_LABEL:
                return r["label"]
            if col == COL_NCLAIMS:
                return str(r["n_claims"])
            if col == COL_EXT_ID:
                return r["ext_id"]
            if col == COL_STATUS:
                # Append an ℹ glyph when a full validation payload exists —
                # tells the user this cell is clickable for details.
                glyph = " ⓘ" if r.get("validation") is not None else ""
                if r["existing_qid"]:
                    return f"{r['status']}  ({r['existing_qid']}){glyph}"
                return f"{r['status']}{glyph}"
            if col == COL_ISSUES:
                issues = r.get("issues") or []
                if not issues:
                    return "✓ clean"
                errors = sum(1 for i in issues if i.severity == "error")
                warnings = sum(1 for i in issues if i.severity == "warning")
                parts = []
                if errors:
                    parts.append(f"✗ {errors} error{'s' if errors != 1 else ''}")
                if warnings:
                    parts.append(f"⚠ {warnings} warning{'s' if warnings != 1 else ''}")
                # Append ⓘ glyph so the cell visually advertises that it is
                # clickable for full issue details (mirrors the Status column).
                return " · ".join(parts) + " ⓘ"

        if role == Qt.ItemDataRole.UserRole:
            if col == COL_NCLAIMS:
                return r["n_claims"]
            if col == COL_APPROVED:
                return int(bool(r.get("approved", False)))
            if col == COL_ISSUES:
                sev = r.get("severity") or "ok"
                # error > warning > ok — sort so problems bubble to the top
                return {"error": 2, "warning": 1, "ok": 0}.get(sev, 0)
            return self.data(index, Qt.ItemDataRole.DisplayRole)

        if role == Qt.ItemDataRole.CheckStateRole and col == COL_APPROVED:
            return (
                Qt.CheckState.Checked if r.get("approved", False)
                else Qt.CheckState.Unchecked
            )

        if role == Qt.ItemDataRole.BackgroundRole and col == COL_STATUS:
            bg, _fg = _status_colors(r["status"])
            return QColor(bg)
        if role == Qt.ItemDataRole.ForegroundRole and col == COL_STATUS:
            _bg, fg = _status_colors(r["status"])
            return QColor(fg)

        if role == Qt.ItemDataRole.BackgroundRole and col == COL_ISSUES:
            bg, _fg = _severity_colors(r.get("severity") or "ok")
            return QColor(bg)
        if role == Qt.ItemDataRole.ForegroundRole and col == COL_ISSUES:
            _bg, fg = _severity_colors(r.get("severity") or "ok")
            return QColor(fg)

        if role == Qt.ItemDataRole.BackgroundRole and r.get("approved", False):
            from mhm_pipeline.gui import theme  # noqa: PLC0415
            return QColor(22, 163, 74, 28 if theme.is_dark() else 18)
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_STATUS:
            parts = [r.get("status_reason", "") or r["status"]]
            if r.get("validation") is not None:
                parts.append("Click for full validation details.")
            return "\n".join(p for p in parts if p)
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_ISSUES:
            issues = r.get("issues") or []
            if not issues:
                return "No validation issues."
            lines = [
                f"[{i.severity.upper()}] {i.code}: {i.message}" for i in issues
            ]
            lines.append("Click for full validation details.")
            return "\n".join(lines)

        return None

    def setData(  # noqa: N802
        self, index: QModelIndex, value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid():
            return False
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == COL_APPROVED:
            r = self._rows[index.row()]
            # Safety rail 1: never allow approval of existing-other items
            if r.get("status") == _STATUS_OTHER:
                return False
            # Safety rail 2: refuse approval when the validator flagged errors
            if r.get("severity") == "error":
                return False
            r["approved"] = (Qt.CheckState(value) == Qt.CheckState.Checked)
            self.dataChanged.emit(index, index.siblingAtColumn(COL_ACTIONS))
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == COL_APPROVED:
            r = self._rows[index.row()]
            if r.get("status") == _STATUS_OTHER or r.get("severity") == "error":
                # Disable the checkbox for community-owned items AND for any
                # item that has error-level validation issues (rule 23+27+29).
                return base & ~Qt.ItemFlag.ItemIsEnabled
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base


# ────────────────────────────────────────────────────────────────────────────
# Claim-detail dialog
# ────────────────────────────────────────────────────────────────────────────


# ── Property-based grouping for statements (Wikidata-style) ────────────

_IDENTIFIER_PIDS: tuple[str, ...] = (
    "P214", "P8189", "P244", "P227", "P213", "P268", "P1566",
    "P3959", "P10832",
)


def _group_statements_by_property(statements: list[Any]) -> dict[str, list[Any]]:
    """Group statements by PID preserving insertion order — the Wikidata UI
    shows one row per property with all its values underneath."""
    groups: dict[str, list[Any]] = {}
    for s in statements or []:
        pid = getattr(s, "property_id", "") or ""
        groups.setdefault(pid, []).append(s)
    return groups


def _render_value_html(val: Any, vt: str, theme_mod: Any) -> str:
    """Render a single statement value the same way wikidata.org does."""
    from converter.wikidata.property_labels import qid_label  # noqa: PLC0415

    s = str(val)
    if vt == "item" and s.startswith("Q"):
        label = qid_label(s)
        if label == s:
            return (
                f"<a href='https://www.wikidata.org/wiki/{s}' "
                f"style='color:{theme_mod.ui('highlight')}; text-decoration:none'>{s}</a>"
            )
        return (
            f"<a href='https://www.wikidata.org/wiki/{s}' "
            f"style='color:{theme_mod.ui('highlight')}; text-decoration:none'>"
            f"{label}</a>&nbsp;<span style='color:{theme_mod.ui('subtext')};"
            f" font-size:{theme_mod.FONT_SM}px'>({s})</span>"
        )
    if vt == "url" or s.startswith(("http://", "https://")):
        return (
            f"<a href='{s}' style='color:{theme_mod.ui('highlight')};"
            f" text-decoration:none'>{s}</a>"
        )
    if vt == "monolingualtext" and isinstance(val, str) and ":" in s:
        lang, _, text = s.partition(":")
        return (
            f"<span>{text}</span>&nbsp;"
            f"<span style='color:{theme_mod.ui('subtext')};"
            f" font-size:{theme_mod.FONT_XS}px'>({lang})</span>"
        )
    if vt == "time":
        return f"<span style='font-family:monospace'>{s}</span>"
    return f"<span>{s}</span>"


def _render_statement_block_html(
    pid: str, stmts: list[Any], theme_mod: Any,
) -> str:
    """One Wikidata-style block: property header + every value + qualifiers
    + references. Mirrors the layout on a real wikidata.org entity page."""
    from converter.wikidata.property_labels import property_label  # noqa: PLC0415

    prop_label = property_label(pid)
    prop_link = (
        f"<a href='https://www.wikidata.org/wiki/Property:{pid}' "
        f"style='color:{theme_mod.ui('text')}; text-decoration:none'>"
        f"<b>{prop_label}</b>&nbsp;<span style='color:{theme_mod.ui('subtext')};"
        f" font-size:{theme_mod.FONT_SM}px'>({pid})</span></a>"
    )

    value_rows: list[str] = []
    for s in stmts:
        val = getattr(s, "value", "")
        vt = getattr(s, "value_type", "")
        value_html = _render_value_html(val, vt, theme_mod)

        qual_bits: list[str] = []
        for q in getattr(s, "qualifiers", []) or []:
            qp = q.get("property_id", "")
            qv = q.get("value", "")
            qvt = q.get("value_type", q.get("type", ""))
            qp_label = property_label(qp) if qp else qp
            qv_html = _render_value_html(qv, qvt, theme_mod)
            qual_bits.append(
                f"<div style='margin:2px 0 2px 28px; color:{theme_mod.ui('subtext')};"
                f" font-size:{theme_mod.FONT_SM}px'>"
                f"<span style='opacity:0.7'>↳&nbsp;{qp_label}</span>:&nbsp;{qv_html}"
                f"</div>"
            )

        ref_bits: list[str] = []
        for ref in getattr(s, "references", []) or []:
            parts: list[str] = []
            for r in (ref if isinstance(ref, list) else [ref]):
                rp = r.get("property_id", "")
                rv = r.get("value", "")
                rvt = r.get("value_type", r.get("type", ""))
                rp_label = property_label(rp) if rp else rp
                rv_html = _render_value_html(rv, rvt, theme_mod)
                parts.append(f"{rp_label}: {rv_html}")
            if parts:
                ref_bits.append(
                    f"<div style='margin:2px 0 2px 28px; color:{theme_mod.ui('subtext')};"
                    f" font-style:italic; font-size:{theme_mod.FONT_SM}px'>"
                    f"▸ {' · '.join(parts)}</div>"
                )

        value_rows.append(
            f"<div style='padding:4px 0; border-bottom:1px dashed"
            f" {theme_mod.ui('border')}'>"
            f"<div style='margin-left:12px; font-size:{theme_mod.FONT_BASE}px'>"
            f"{value_html}</div>"
            f"{''.join(qual_bits)}{''.join(ref_bits)}"
            f"</div>"
        )

    return (
        f"<div style='margin:{theme_mod.SPACE_MD}px 0;"
        f" background:transparent;'>"
        f"<div style='padding:6px 8px; background:{theme_mod.ui('panel_bg')};"
        f" border-left:3px solid {theme_mod.ui('highlight')};"
        f" border-radius:{theme_mod.RADIUS_SM}px'>{prop_link}</div>"
        f"{''.join(value_rows)}"
        f"</div>"
    )


class ItemDetailDialog(QDialog):
    """Wikidata-style entity view.

    Matches the structure of a real https://www.wikidata.org/wiki/Q… page:

        Header    — big label · description · aliases · QID link · type chip
        Terms     — every label / description / alias in every language
        Statements — grouped by property (like the Wikidata right-hand column)
        Identifiers — pulled out into their own section, as Wikidata does
        Issues    — validator findings (shown inline with their severity colour)
    """

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self._row = row
        self._theme = theme

        label = row.get("label", "") or "(no label)"
        qid = row.get("existing_qid", "") or ""
        etype = row.get("entity_type", "") or ""

        self.setWindowTitle(
            f"{etype.title()} · {label}" + (f" ({qid})" if qid else "")
        )
        self.resize(960, 720)
        self.setMinimumSize(640, 480)

        content = _install_glass_backdrop(self)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        outer.addLayout(self._build_header())

        issues = row.get("issues") or []
        if issues:
            outer.addWidget(self._build_issues_banner(issues))

        tabs = QTabWidget()
        tabs.setStyleSheet(
            f"QTabWidget::pane {{"
            f" background: rgba(0,0,0, 75);"
            f" border: 1px solid rgba(255,255,255, 22);"
            f" border-radius: {theme.RADIUS_MD}px; }}"
            f"QTabBar::tab {{"
            f" background: rgba(255,255,255, 12);"
            f" color: {theme.ui('subtext')};"
            f" padding: 6px 14px;"
            f" border-top-left-radius: {theme.RADIUS_SM}px;"
            f" border-top-right-radius: {theme.RADIUS_SM}px;"
            f" margin-right: 2px; }}"
            f"QTabBar::tab:selected {{"
            f" background: rgba(99, 102, 241, 120);"
            f" color: white; }}"
        )
        tabs.addTab(self._build_statements_tab(), "Statements")
        tabs.addTab(self._build_identifiers_tab(), "Identifiers")
        tabs.addTab(self._build_terms_tab(), "Labels / Aliases / Descriptions")
        tabs.addTab(self._build_raw_tab(), "Raw JSON")
        outer.addWidget(tabs, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch()
        if qid:
            open_wd = QPushButton(f"🔗 Open {qid} on Wikidata")
            open_wd.setCursor(Qt.CursorShape.PointingHandCursor)
            open_wd.setStyleSheet(theme.button_style("secondary"))
            open_wd.clicked.connect(
                lambda _=False, q=qid: QDesktopServices.openUrl(
                    QUrl(f"https://www.wikidata.org/wiki/{q}"),
                ),
            )
            bar.addWidget(open_wd)
        close = QPushButton("Close")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(theme.button_style())
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        outer.addLayout(bar)

    # ── Header ──────────────────────────────────────────────────────────

    def _build_header(self) -> QHBoxLayout:
        theme = self._theme
        row = self._row
        item = row.get("_item")

        left = QVBoxLayout()
        left.setSpacing(2)
        title = QLabel(row.get("label", "") or "(no label)")
        title.setStyleSheet(
            f"font-size:{theme.FONT_XL + 4}px; font-weight:600;"
            f" color:{theme.ui('text')};"
        )
        title.setWordWrap(True)
        left.addWidget(title)

        qid = row.get("existing_qid", "") or ""
        etype = row.get("entity_type", "") or ""
        meta_bits: list[str] = []
        if qid:
            meta_bits.append(
                f"<a href='https://www.wikidata.org/wiki/{qid}' "
                f"style='color:{theme.ui('highlight')}; text-decoration:none'>"
                f"<b>{qid}</b></a>"
            )
        if etype:
            meta_bits.append(
                f"<span style='color:{theme.ui('subtext')}'>"
                f"{etype}</span>"
            )
        if row.get("status"):
            bg, fg = _status_colors(row.get("status") or "")
            meta_bits.append(
                f"<span style='background:{bg}; color:{fg};"
                f" padding:2px 8px; border-radius:{theme.RADIUS_SM}px;"
                f" font-size:{theme.FONT_SM}px'>{row.get('status')}</span>"
            )
        meta = QLabel(" · ".join(meta_bits))
        meta.setTextFormat(Qt.TextFormat.RichText)
        meta.setOpenExternalLinks(True)
        meta.setWordWrap(True)
        left.addWidget(meta)

        desc = row.get("description", "") or ""
        if desc:
            d = QLabel(desc)
            d.setStyleSheet(
                f"color:{theme.ui('subtext')}; font-size:{theme.FONT_BASE}px;"
            )
            d.setWordWrap(True)
            left.addWidget(d)

        # aliases shown as inline pills, like the Wikidata header does
        labels_dict = getattr(item, "labels", {}) or {} if item is not None else {}
        aliases_dict = getattr(item, "aliases", {}) or {} if item is not None else {}
        all_aliases: list[str] = []
        for lang_aliases in aliases_dict.values():
            all_aliases.extend(str(a) for a in (lang_aliases or []))
        if all_aliases:
            pills = "&nbsp;&nbsp;".join(
                f"<span style='background:{theme.ui('panel_bg')};"
                f" color:{theme.ui('subtext')}; padding:2px 6px;"
                f" border-radius:{theme.RADIUS_SM}px;"
                f" font-size:{theme.FONT_SM}px'>{a}</span>"
                for a in dict.fromkeys(all_aliases)
            )
            al = QLabel(
                f"<span style='color:{theme.ui('subtext')};"
                f" font-size:{theme.FONT_SM}px'>Also known as:</span>&nbsp;{pills}"
            )
            al.setTextFormat(Qt.TextFormat.RichText)
            al.setWordWrap(True)
            left.addWidget(al)
        _ = labels_dict  # kept for future use

        h = QHBoxLayout()
        h.addLayout(left, stretch=1)
        return h

    # ── Tabs ────────────────────────────────────────────────────────────

    def _scrolling_html(self, html: str) -> QScrollArea:
        theme = self._theme
        inner = QTextEdit()
        inner.setReadOnly(True)
        inner.setAcceptRichText(True)
        inner.setHtml(html)
        inner.setStyleSheet(
            f"QTextEdit {{ background:transparent; color:{theme.ui('text')};"
            f" border:none; }}"
        )
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _build_statements_tab(self) -> QWidget:
        theme = self._theme
        item = self._row.get("_item")
        stmts = getattr(item, "statements", []) or [] if item is not None else []

        # Split identifiers into their own tab
        non_id_stmts = [
            s for s in stmts
            if getattr(s, "property_id", "") not in _IDENTIFIER_PIDS
        ]

        groups = _group_statements_by_property(non_id_stmts)
        if not groups:
            body = (
                f"<i style='color:{theme.ui('subtext')}'>"
                f"This item has no non-identifier statements.</i>"
            )
        else:
            body = "".join(
                _render_statement_block_html(pid, stmts, theme)
                for pid, stmts in groups.items()
            )
        return self._scrolling_html(body)

    def _build_identifiers_tab(self) -> QWidget:
        theme = self._theme
        item = self._row.get("_item")
        stmts = getattr(item, "statements", []) or [] if item is not None else []
        id_stmts = [
            s for s in stmts
            if getattr(s, "property_id", "") in _IDENTIFIER_PIDS
        ]
        groups = _group_statements_by_property(id_stmts)
        if not groups:
            body = (
                f"<i style='color:{theme.ui('subtext')}'>"
                f"No external identifiers attached.</i>"
            )
        else:
            body = "".join(
                _render_statement_block_html(pid, stmts, theme)
                for pid, stmts in groups.items()
            )
        return self._scrolling_html(body)

    def _build_terms_tab(self) -> QWidget:
        theme = self._theme
        item = self._row.get("_item")
        labels = getattr(item, "labels", {}) or {} if item is not None else {}
        descs = getattr(item, "descriptions", {}) or {} if item is not None else {}
        aliases = getattr(item, "aliases", {}) or {} if item is not None else {}

        langs = sorted(set(labels) | set(descs) | set(aliases))
        if not langs:
            return self._scrolling_html(
                f"<i style='color:{theme.ui('subtext')}'>No terms.</i>"
            )

        rows_html = [
            "<table style='width:100%; border-collapse:collapse;'>"
            "<thead><tr>"
            f"<th style='text-align:left; padding:6px 10px; color:{theme.ui('subtext')};"
            f" border-bottom:1px solid {theme.ui('border')}'>Language</th>"
            f"<th style='text-align:left; padding:6px 10px; color:{theme.ui('subtext')};"
            f" border-bottom:1px solid {theme.ui('border')}'>Label</th>"
            f"<th style='text-align:left; padding:6px 10px; color:{theme.ui('subtext')};"
            f" border-bottom:1px solid {theme.ui('border')}'>Description</th>"
            f"<th style='text-align:left; padding:6px 10px; color:{theme.ui('subtext')};"
            f" border-bottom:1px solid {theme.ui('border')}'>Aliases</th>"
            "</tr></thead><tbody>"
        ]
        for lang in langs:
            alias_list = aliases.get(lang) or []
            alias_text = ", ".join(str(a) for a in alias_list) if alias_list else "—"
            rows_html.append(
                f"<tr>"
                f"<td style='padding:6px 10px; color:{theme.ui('subtext')};"
                f" vertical-align:top'><b>{lang}</b></td>"
                f"<td style='padding:6px 10px; vertical-align:top'>"
                f"{labels.get(lang, '—')}</td>"
                f"<td style='padding:6px 10px; vertical-align:top;"
                f" color:{theme.ui('subtext')}'>{descs.get(lang, '—')}</td>"
                f"<td style='padding:6px 10px; vertical-align:top;"
                f" color:{theme.ui('subtext')}'>{alias_text}</td>"
                f"</tr>"
            )
        rows_html.append("</tbody></table>")
        return self._scrolling_html("".join(rows_html))

    def _build_raw_tab(self) -> QWidget:
        theme = self._theme
        item = self._row.get("_item")
        # Serialise best-effort — WikidataItem is a dataclass, statements too
        try:
            raw = {
                "local_id": getattr(item, "local_id", "") if item else "",
                "entity_type": getattr(item, "entity_type", "") if item else "",
                "existing_qid": getattr(item, "existing_qid", "") if item else "",
                "labels": dict(getattr(item, "labels", {}) or {}) if item else {},
                "descriptions": dict(getattr(item, "descriptions", {}) or {}) if item else {},
                "aliases": {k: list(v) for k, v in (getattr(item, "aliases", {}) or {}).items()} if item else {},
                "statements": [
                    {
                        "property_id": getattr(s, "property_id", ""),
                        "value": getattr(s, "value", ""),
                        "value_type": getattr(s, "value_type", ""),
                        "qualifiers": list(getattr(s, "qualifiers", []) or []),
                        "references": list(getattr(s, "references", []) or []),
                    }
                    for s in (getattr(item, "statements", []) or [])
                ],
            }
            body = json.dumps(raw, indent=2, ensure_ascii=False, default=str)
        except Exception as exc:
            body = f"<serialization-error>{exc}</serialization-error>"

        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(body)
        view.setStyleSheet(
            f"QTextEdit {{ font-family:'SF Mono',Menlo,Consolas,monospace;"
            f" font-size:{theme.FONT_SM}px;"
            f" background:transparent; color:{theme.ui('text')};"
            f" border:1px solid {theme.ui('border')};"
            f" border-radius:{theme.RADIUS_SM}px; }}"
        )
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(view)
        return container

    # ── Issues banner ──────────────────────────────────────────────────

    def _build_issues_banner(self, issues: list[Any]) -> QWidget:
        theme = self._theme
        sev_rank = {"error": 2, "warning": 1}
        issues_sorted = sorted(
            issues, key=lambda i: sev_rank.get(i.severity, 0), reverse=True,
        )
        blocks: list[str] = []
        for iss in issues_sorted:
            sev = iss.severity
            bg, fg = _severity_colors(sev)
            icon = "✗" if sev == "error" else ("⚠" if sev == "warning" else "✓")
            ref_html = (
                f" · <a href='{iss.reference}' style='color:{fg}'>policy</a>"
                if iss.reference else ""
            )
            blocks.append(
                f"<div style='background:{bg}; color:{fg};"
                f" padding:{theme.SPACE_SM}px {theme.SPACE_MD}px;"
                f" border-radius:{theme.RADIUS_SM}px; margin:2px 0;'>"
                f"<b>{icon} [{iss.code}]</b>&nbsp;&nbsp;{iss.message}{ref_html}"
                f"</div>"
            )
        banner = QTextEdit()
        banner.setReadOnly(True)
        banner.setAcceptRichText(True)
        banner.setHtml("".join(blocks))
        banner.setFixedHeight(min(220, 32 + 48 * len(issues)))
        banner.setStyleSheet(
            f"QTextEdit {{ background:transparent; border:none; }}"
        )
        return banner


class ClaimsEditDialog(QDialog):
    """Full editable claim table for a WikidataItem.

    Opens when the user clicks the ``#Claims`` cell. Rows show every
    statement with property label, value, qualifiers and references
    summary. Single-row edit and delete are supported. Save commits
    every edit back to the underlying ``WikidataItem.statements`` list.
    """

    _COL_PID = 0
    _COL_LABEL = 1
    _COL_VALUE = 2
    _COL_TYPE = 3
    _COL_QUALS = 4
    _COL_REFS = 5
    _COL_DELETE = 6
    _HEADERS = [
        "Property", "Name", "Value", "Type", "Qual.", "Refs.", "",
    ]

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415
        from converter.wikidata.property_labels import property_label  # noqa: PLC0415

        self._theme = theme
        self._prop_label = property_label
        self._row = row
        self._item = row.get("_item")
        statements = getattr(self._item, "statements", []) or [] if self._item else []
        # Deep-copy so Cancel leaves the underlying item untouched
        self._draft: list[Any] = [copy.copy(s) for s in statements]
        self._deleted_indices: set[int] = set()

        label = row.get("label", "") or "(no label)"
        qid = row.get("existing_qid", "") or ""
        self.setWindowTitle(
            f"Claims — {label}" + (f" ({qid})" if qid else "")
        )
        self.resize(1000, 640)
        self.setMinimumSize(640, 400)

        # Wrap in GraphBackdrop so the dialog picks up the same liquid-glass
        # node/gradient background as the main window — otherwise the dialog
        # sits flat on a solid dark fill and breaks visual continuity.
        content = _install_glass_backdrop(self)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        header = QLabel(
            f"<b style='font-size:{theme.FONT_LG}px'>{label}</b>"
            + (f"&nbsp;&nbsp;<a href='https://www.wikidata.org/wiki/{qid}'"
               f" style='color:{theme.ui('highlight')}'>{qid}</a>" if qid else "")
            + f"<br><span style='color:{theme.ui('subtext')};"
            f" font-size:{theme.FONT_SM}px'>"
            f"{len(self._draft)} statement{'s' if len(self._draft) != 1 else ''}"
            f" — click any Value cell to edit · ✕ removes a row"
            f"</span>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setOpenExternalLinks(True)
        header.setWordWrap(True)
        outer.addWidget(header)

        self._table = QTableView()
        self._model = _ClaimsTableModel(self._draft, self._deleted_indices)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        # Translucent surface so the liquid-glass backdrop reads through the table
        self._table.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._table.setStyleSheet(_glass_table_style(theme))
        self._table.viewport().setAutoFillBackground(False)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        h = self._table.horizontalHeader()
        assert h is not None
        h.setSectionResizeMode(self._COL_PID, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self._COL_LABEL, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self._COL_VALUE, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(self._COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self._COL_QUALS, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self._COL_REFS, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self._COL_DELETE, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(self._COL_DELETE, 44)
        outer.addWidget(self._table, stretch=1)

        self._attach_delete_buttons()
        self._model.modelReset.connect(self._attach_delete_buttons)
        self._model.rowsInserted.connect(self._attach_delete_buttons)
        self._model.rowsRemoved.connect(self._attach_delete_buttons)

        bar = QHBoxLayout()
        bar.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(theme.ghost_button_style())
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save changes")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setStyleSheet(theme.success_btn_style())
        save.clicked.connect(self._on_save)
        bar.addWidget(cancel)
        bar.addWidget(save)
        outer.addLayout(bar)

    def _attach_delete_buttons(self) -> None:
        theme = self._theme
        for row in range(self._model.rowCount()):
            idx = self._model.index(row, self._COL_DELETE)
            self._table.setIndexWidget(idx, None)
            btn = QPushButton("✕")
            btn.setToolTip("Delete this statement")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent;"
                f" color: {theme.ui('warning')};"
                f" border: 1px solid {theme.ui('border')};"
                f" border-radius: {theme.RADIUS_SM}px;"
                f" font-size: {theme.FONT_BASE}px; font-weight: 600;"
                f" min-height: 22px; min-width: 28px; }}"
                f"QPushButton:hover {{ background: rgba(239,68,68,30);"
                f" border-color: {theme.ui('warning')}; }}"
            )
            btn.clicked.connect(lambda _=False, r=row: self._on_delete_row(r))
            self._table.setIndexWidget(idx, btn)

    def _on_delete_row(self, source_row: int) -> None:
        if 0 <= source_row < len(self._draft):
            self._deleted_indices.add(source_row)
            tl = self._model.index(source_row, 0)
            br = self._model.index(source_row, self._model.columnCount() - 1)
            self._model.dataChanged.emit(tl, br)

    def _on_save(self) -> None:
        # Rebuild the item's statements list: drop deleted rows, keep edits.
        if self._item is None:
            self.accept()
            return
        surviving = [
            s for i, s in enumerate(self._draft)
            if i not in self._deleted_indices
        ]
        self._item.statements = surviving  # type: ignore[attr-defined]
        self.accept()


class _ClaimsTableModel(QAbstractTableModel):
    """Backing model for :class:`ClaimsEditDialog` — edits ``value`` in place."""

    _COL_PID = 0
    _COL_LABEL = 1
    _COL_VALUE = 2
    _COL_TYPE = 3
    _COL_QUALS = 4
    _COL_REFS = 5
    _COL_DELETE = 6

    def __init__(self, draft: list[Any], deleted: set[int]) -> None:
        super().__init__()
        self._draft = draft
        self._deleted = deleted

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self._draft)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 7

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return ClaimsEditDialog._HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        from converter.wikidata.property_labels import property_label  # noqa: PLC0415

        if not index.isValid() or index.row() >= len(self._draft):
            return None
        s = self._draft[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.ForegroundRole and index.row() in self._deleted:
            return QColor(160, 160, 160)

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == self._COL_PID:
                return getattr(s, "property_id", "")
            if col == self._COL_LABEL:
                return property_label(getattr(s, "property_id", ""))
            if col == self._COL_VALUE:
                struck = " (removed)" if index.row() in self._deleted else ""
                return f"{getattr(s, 'value', '')}{struck}"
            if col == self._COL_TYPE:
                return getattr(s, "value_type", "")
            if col == self._COL_QUALS:
                n = len(getattr(s, "qualifiers", []) or [])
                return str(n) if n else "—"
            if col == self._COL_REFS:
                n = len(getattr(s, "references", []) or [])
                return str(n) if n else "—"
            if col == self._COL_DELETE:
                return ""

        if role == Qt.ItemDataRole.ToolTipRole:
            if col == self._COL_QUALS:
                quals = getattr(s, "qualifiers", []) or []
                return "\n".join(
                    f"{q.get('property_id','?')}: {q.get('value','?')}" for q in quals
                ) or "No qualifiers"
            if col == self._COL_REFS:
                refs = getattr(s, "references", []) or []
                if not refs:
                    return "No references"
                lines = []
                for ref in refs:
                    group = ref if isinstance(ref, list) else [ref]
                    lines.append(", ".join(
                        f"{r.get('property_id','?')}: {r.get('value','?')}"
                        for r in group
                    ))
                return "\n".join(lines)

        return None

    def setData(  # noqa: N802
        self, index: QModelIndex, value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        if index.column() != self._COL_VALUE:
            return False
        row = index.row()
        if not 0 <= row < len(self._draft):
            return False
        self._draft[row].value = str(value)
        self.dataChanged.emit(index, index)
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == self._COL_VALUE and index.row() not in self._deleted:
            return base | Qt.ItemFlag.ItemIsEditable
        return base


class ItemStatusDialog(QDialog):
    """Rich ``Validate-with-Wikidata`` status popup.

    Surfaces every signal the worker collected for a single item: status,
    matched QID, identifier cross-check, creator three-channel result,
    the actual SPARQL ASK string, and the re-run validator issues.
    Read-only — editing happens through the other per-row dialogs.
    """

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self._row = row
        self._theme = theme

        label = row.get("label", "") or "(no label)"
        qid = row.get("existing_qid", "") or ""
        self.setWindowTitle(
            f"Status — {label}" + (f" ({qid})" if qid else "")
        )
        self.resize(780, 620)
        self.setMinimumSize(560, 420)

        content = _install_glass_backdrop(self)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        outer.addWidget(self._build_header())

        tabs = QTabWidget()
        tabs.setStyleSheet(
            f"QTabWidget::pane {{ background: rgba(0,0,0, 75);"
            f" border: 1px solid rgba(255,255,255, 22);"
            f" border-radius: {theme.RADIUS_MD}px; }}"
            f"QTabBar::tab {{ background: rgba(255,255,255, 12);"
            f" color: {theme.ui('subtext')}; padding: 6px 14px;"
            f" border-top-left-radius: {theme.RADIUS_SM}px;"
            f" border-top-right-radius: {theme.RADIUS_SM}px;"
            f" margin-right: 2px; }}"
            f"QTabBar::tab:selected {{ background: rgba(99, 102, 241, 120);"
            f" color: white; }}"
        )
        tabs.addTab(self._build_summary_tab(), "Summary")
        tabs.addTab(self._build_identifiers_tab(), "Identifiers")
        tabs.addTab(self._build_creator_tab(), "Creator check")
        tabs.addTab(self._build_sparql_tab(), "SPARQL")
        tabs.addTab(self._build_validator_tab(), "Validator")
        outer.addWidget(tabs, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch()
        close = QPushButton("Close")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(theme.button_style())
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        outer.addLayout(bar)

    def _build_header(self) -> QWidget:
        theme = self._theme
        row = self._row
        qid = row.get("existing_qid", "") or ""
        status = row.get("status", "") or "unknown"
        bg, fg = _status_colors(status)
        v = row.get("validated_at", "")
        lat = row.get("validation_latency_ms")
        validated_badge = ""
        if v:
            validated_badge = (
                f"<span style='color:{theme.ui('subtext')};"
                f" font-size:{theme.FONT_SM}px'>"
                f"validated {v[:19]}"
                + (f" · {lat}ms" if isinstance(lat, int) else "")
                + "</span>"
            )
        parts = [
            f"<div style='font-size:{theme.FONT_XL}px; font-weight:600;"
            f" color:{theme.ui('text')}'>{row.get('label','(no label)')}</div>",
            f"<div style='margin-top:4px'>",
            f"<span style='background:{bg}; color:{fg};"
            f" padding:3px 10px; border-radius:{theme.RADIUS_SM}px;"
            f" font-size:{theme.FONT_SM}px; font-weight:600'>{status}</span>",
        ]
        if qid:
            parts.append(
                f"&nbsp;&nbsp;<a href='https://www.wikidata.org/wiki/{qid}'"
                f" style='color:{theme.ui('highlight')}'>{qid}</a>"
            )
        if row.get("status_reason"):
            parts.append(
                f"&nbsp;&nbsp;<span style='color:{theme.ui('subtext')}'>"
                f"— {row.get('status_reason','')}</span>"
            )
        parts.append("</div>")
        if validated_badge:
            parts.append(f"<div style='margin-top:6px'>{validated_badge}</div>")
        lbl = QLabel("".join(parts))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setOpenExternalLinks(True)
        lbl.setWordWrap(True)
        return lbl

    def _scroll_html(self, html: str) -> QWidget:
        edit = QTextEdit()
        edit.setReadOnly(True)
        edit.setAcceptRichText(True)
        edit.setHtml(html)
        edit.setStyleSheet(
            f"QTextEdit {{ background: transparent;"
            f" color: {self._theme.ui('text')}; border: none; }}"
        )
        return edit

    def _build_summary_tab(self) -> QWidget:
        theme = self._theme
        row = self._row
        v = row.get("validation") or {}
        lines = [
            f"<b>Local ID:</b> {row.get('local_id','')}",
            f"<b>Type:</b> {row.get('entity_type','')}",
            f"<b>Status:</b> {row.get('status','')}",
            f"<b>Reason:</b> {row.get('status_reason','')}",
        ]
        if v.get("matched_qid"):
            lines.append(
                f"<b>Matched QID:</b> <a href='https://www.wikidata.org/wiki/"
                f"{v['matched_qid']}' style='color:{theme.ui('highlight')}'>"
                f"{v['matched_qid']}</a>"
            )
        if v.get("validated_at"):
            lines.append(f"<b>Validated:</b> {v['validated_at']}")
        if v.get("validation_latency_ms") is not None:
            lines.append(f"<b>Latency:</b> {v['validation_latency_ms']}ms")
        # Approved/blocked hints
        if row.get("severity") == "error":
            lines.append(
                f"<div style='margin-top:8px; color:{theme.ui('warning')}'>"
                "⚠ Approval is blocked by a validator error.</div>"
            )
        if row.get("status") == _STATUS_OTHER:
            lines.append(
                f"<div style='margin-top:8px; color:{theme.ui('warning')}'>"
                "⚠ Approval is blocked — item was not created by the "
                "authenticated user (Rule 38).</div>"
            )
        body = "<br>".join(lines)
        return self._scroll_html(body)

    def _build_identifiers_tab(self) -> QWidget:
        theme = self._theme
        v = self._row.get("validation") or {}
        checks = (v.get("identifier_checks") or self._row.get("identifier_checks") or {})
        if not checks:
            return self._scroll_html(
                f"<i style='color:{theme.ui('subtext')}'>"
                f"No identifier checks recorded — run Validate with Wikidata first."
                f"</i>"
            )
        verdict_colors = {
            "matched": ("#dcfce7", "#14532d"),
            "conflict": ("#fee2e2", "#7f1d1d"),
            "not-found": ("#fef3c7", "#78350f"),
            "not-checked": ("#f3f4f6", "#374151"),
        }
        rows = []
        for pid, c in checks.items():
            verdict = c.get("verdict") or "not-checked"
            bg, fg = verdict_colors.get(verdict, verdict_colors["not-checked"])
            owner = c.get("existing") or "—"
            rows.append(
                f"<tr>"
                f"<td style='padding:6px 10px'><b>{pid}</b></td>"
                f"<td style='padding:6px 10px'>{c.get('proposed','')}</td>"
                f"<td style='padding:6px 10px; color:{theme.ui('subtext')}'>{owner}</td>"
                f"<td style='padding:6px 10px'>"
                f"<span style='background:{bg}; color:{fg};"
                f" padding:2px 8px; border-radius:{theme.RADIUS_SM}px;"
                f" font-size:{theme.FONT_SM}px'>{verdict}</span>"
                f"</td></tr>"
            )
        html = (
            "<table style='width:100%; border-collapse:collapse;'>"
            "<thead><tr>"
            f"<th style='text-align:left; padding:6px 10px;"
            f" color:{theme.ui('subtext')};"
            f" border-bottom:1px solid {theme.ui('border')}'>Property</th>"
            f"<th style='text-align:left; padding:6px 10px;"
            f" color:{theme.ui('subtext')};"
            f" border-bottom:1px solid {theme.ui('border')}'>Proposed</th>"
            f"<th style='text-align:left; padding:6px 10px;"
            f" color:{theme.ui('subtext')};"
            f" border-bottom:1px solid {theme.ui('border')}'>On Wikidata</th>"
            f"<th style='text-align:left; padding:6px 10px;"
            f" color:{theme.ui('subtext')};"
            f" border-bottom:1px solid {theme.ui('border')}'>Verdict</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
        return self._scroll_html(html)

    def _build_creator_tab(self) -> QWidget:
        theme = self._theme
        v = self._row.get("validation") or {}
        c = (v.get("creator_check") or self._row.get("creator_check") or {})
        if not c:
            return self._scroll_html(
                f"<i style='color:{theme.ui('subtext')}'>No creator check recorded.</i>"
            )
        verdict = c.get("verdict") or "unknown"
        verdict_colors = {
            "ours": ("#dcfce7", "#14532d"),
            "other": ("#fee2e2", "#7f1d1d"),
            "unverified": ("#fef3c7", "#78350f"),
            "unknown-auth": ("#f3f4f6", "#374151"),
        }
        bg, fg = verdict_colors.get(verdict, verdict_colors["unknown-auth"])
        contribs = c.get("contribs_new")
        contribs_label = {
            True: "new-page contribution found",
            False: "no new-page contribution found",
            None: "contribs API unreachable",
        }.get(contribs, "?")
        lines = [
            f"<b>Authenticated user:</b> {c.get('auth_user','') or '(none)'}",
            f"<b>First revision author (API):</b> {c.get('first_rev_author','') or '(unknown)'}",
            f"<b>Contribs cross-check (API):</b> {contribs_label}",
            f"<b>SPARQL ASK result:</b> {v.get('sparql_answer')}",
            f"<br><b>Verdict:</b> <span style='background:{bg}; color:{fg};"
            f" padding:2px 8px; border-radius:{theme.RADIUS_SM}px'>{verdict}</span>",
        ]
        return self._scroll_html("<br>".join(lines))

    def _build_sparql_tab(self) -> QWidget:
        theme = self._theme
        v = self._row.get("validation") or {}
        ask = v.get("sparql_ask") or self._row.get("sparql_ask") or ""
        ans = v.get("sparql_answer")
        if ans is None:
            ans_text = "null (endpoint unreachable or not yet checked)"
        else:
            ans_text = str(ans)
        if not ask:
            return self._scroll_html(
                f"<i style='color:{theme.ui('subtext')}'>"
                "No SPARQL query fired for this row.</i>"
            )
        return self._scroll_html(
            "<b>Query:</b><br>"
            f"<pre style='background: rgba(0,0,0,90);"
            f" color:{theme.ui('text')};"
            f" padding:{theme.SPACE_MD}px;"
            f" border-radius:{theme.RADIUS_SM}px;"
            f" font-family: SF Mono,Menlo,Consolas,monospace;"
            f" font-size:{theme.FONT_SM}px;'>{ask}</pre>"
            f"<br><b>Answer:</b> {ans_text}"
        )

    def _build_validator_tab(self) -> QWidget:
        theme = self._theme
        issues = self._row.get("issues") or []
        if not issues:
            return self._scroll_html(
                f"<i style='color:{theme.ui('subtext')}'>No validator findings.</i>"
            )
        sev_rank = {"error": 2, "warning": 1}
        issues_sorted = sorted(
            issues, key=lambda i: sev_rank.get(i.severity, 0), reverse=True,
        )
        blocks = []
        for iss in issues_sorted:
            sev = iss.severity
            bg, fg = _severity_colors(sev)
            icon = "✗" if sev == "error" else ("⚠" if sev == "warning" else "✓")
            ref_html = (
                f" · <a href='{iss.reference}' style='color:{fg}'>policy</a>"
                if iss.reference else ""
            )
            blocks.append(
                f"<div style='background:{bg}; color:{fg};"
                f" padding:{theme.SPACE_SM}px {theme.SPACE_MD}px;"
                f" border-radius:{theme.RADIUS_SM}px; margin:4px 0;'>"
                f"<b>{icon} [{iss.code}]</b>&nbsp;&nbsp;{iss.message}{ref_html}"
                f"</div>"
            )
        return self._scroll_html("".join(blocks))


class ItemEditDialog(QDialog):
    """Edit the label/description of a Wikidata item (limited for safety)."""

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self._row = row
        self.setWindowTitle(f"Edit item — {row.get('label','')}")
        self.resize(520, 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        form = QFormLayout()
        self._label_edit = QLineEdit(row.get("label", ""))
        form.addRow("Label:", self._label_edit)
        self._desc_edit = QLineEdit(row.get("description", ""))
        form.addRow("Description:", self._desc_edit)
        layout.addLayout(form)

        bar = QHBoxLayout()
        bar.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(theme.ghost_button_style())
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(theme.success_btn_style())
        save.clicked.connect(self.accept)
        bar.addWidget(cancel)
        bar.addWidget(save)
        layout.addLayout(bar)

    def edited(self) -> tuple[str, str]:
        return self._label_edit.text().strip(), self._desc_edit.text().strip()


# ────────────────────────────────────────────────────────────────────────────
# Auto-approve rule dialog (tailored fields)
# ────────────────────────────────────────────────────────────────────────────


# ── Wikidata auto-approve — field catalogue ────────────────────────────
#
# Fields listed in the order the WikiProject Manuscripts data-model + the
# Wikidata community-quality guidelines prioritise them. Every field has
# a tooltip (see ``_QP_FIELD_TOOLTIPS``) citing the motivating policy so
# curators know *why* a check matters.

_QP_FIELDS: list[str] = [
    "entity_type",
    "status",
    "severity",
    "has_issues",
    "has_external_id",
    "has_instance_of",
    "has_collection",
    "has_inventory_number",
    "has_title",
    "has_exemplar_of",
    "has_inception",
    "has_location_of_creation",
    "has_author",
    "n_claims",
    "n_identifiers",
    "n_references_min",
    "label_length_he",
    "label_length_en",
    "description_length_en",
]

_QP_NUMERIC_FIELDS: set[str] = {
    "n_claims", "n_identifiers", "n_references_min",
    "label_length_he", "label_length_en", "description_length_en",
}

# (min, max, step, decimals) per numeric field
_QP_NUMERIC_RANGES: dict[str, tuple[float, float, float, int]] = {
    "n_claims":              (0, 500, 1, 0),
    "n_identifiers":         (0, 20, 1, 0),
    "n_references_min":      (0, 20, 1, 0),
    "label_length_he":       (0, 400, 1, 0),
    "label_length_en":       (0, 400, 1, 0),
    "description_length_en": (0, 400, 1, 0),
}

_QP_FIELD_OPTIONS: dict[str, list[str]] = {
    "entity_type": ["person", "work", "manuscript"],
    "status": [_STATUS_NEW, _STATUS_OURS, _STATUS_UNKNOWN],   # block existing-other
    "severity": ["ok", "warning", "error"],
    "has_issues": ["true", "false"],
    "has_external_id": ["true", "false"],
    "has_instance_of": ["true", "false"],
    "has_collection": ["true", "false"],
    "has_inventory_number": ["true", "false"],
    "has_title": ["true", "false"],
    "has_exemplar_of": ["true", "false"],
    "has_inception": ["true", "false"],
    "has_location_of_creation": ["true", "false"],
    "has_author": ["true", "false"],
}


# Human tooltip per field — cites the Wikidata policy or WikiProject page
# that motivates including the field as an auto-approve signal.
_QP_FIELD_TOOLTIPS: dict[str, str] = {
    "entity_type":             "Local entity class — person, work, or manuscript.",
    "status":                  "Wikidata reconciliation state (new / existing-ours / unknown).",
    "severity":                "Worst validator finding on this item (ok / warning / error).",
    "has_issues":              "True iff the validator raised any issue.",
    "has_external_id":         "At least one of VIAF / NLI / LCCN / GND / ISNI / BnF present.",
    "has_instance_of":         "P31 (instance of) required by every Wikidata item.",
    "has_collection":          "P195 (collection) — anchor property for WikiProject Manuscripts.",
    "has_inventory_number":    "P217 (inventory number / shelfmark) required for manuscripts.",
    "has_title":               "P1476 (title) — canonical manuscript/work title statement.",
    "has_exemplar_of":         "P1574 (exemplar of) — connects manuscript to its work(s).",
    "has_inception":           "P571 (inception) — when was the manuscript produced.",
    "has_location_of_creation":"P1071 (location of creation) — where was it produced.",
    "has_author":              "P50 (author) attached.",
    "n_claims":                "Total number of statements on the item.",
    "n_identifiers":           "Count of authority identifiers (person notability gate).",
    "n_references_min":        "Minimum references across all statements (Sourcing RfC).",
    "label_length_he":         "Length of the Hebrew label (empty/short labels fail disambiguation).",
    "label_length_en":         "Length of the English label.",
    "description_length_en":   "Length of the English description (Help:Description ≤ 250 chars).",
}


def evaluate_qp_rule(row: dict, rule: dict) -> bool:
    from mhm_pipeline.gui.widgets.extraction_editor import evaluate_rule  # noqa: PLC0415

    field = rule.get("field", "")

    # Boolean/presence fields stored as real bools on the row
    if field in {
        "has_issues", "has_instance_of", "has_collection",
        "has_inventory_number", "has_title", "has_exemplar_of",
        "has_inception", "has_location_of_creation", "has_author",
    }:
        present = bool(row.get(field))
        return evaluate_rule({field: "true" if present else "false"}, rule)
    if field == "has_external_id":
        has = bool(row.get("ext_id") or row.get("existing_qid"))
        return evaluate_rule({"has_external_id": "true" if has else "false"}, rule)
    if field == "severity":
        return evaluate_rule({"severity": row.get("severity") or "ok"}, rule)
    # Numeric fields fall through to evaluate_rule's numeric-op branch
    if field in _QP_NUMERIC_FIELDS:
        return evaluate_rule({field: row.get(field, 0)}, rule)
    return evaluate_rule(row, rule)


def evaluate_qp_rules(row: dict, rules: list[dict], combinator: str) -> bool:
    if not rules:
        return False
    results = [evaluate_qp_rule(row, r) for r in rules]
    return all(results) if combinator == "AND" else any(results)


# ────────────────────────────────────────────────────────────────────────────
# Main widget
# ────────────────────────────────────────────────────────────────────────────


class QPEntityBrowser(QWidget):
    """Browse + approve WikidataItem rows; auto-rules; save exports approved."""

    items_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)

        # Header: stats + bulk actions
        header = QHBoxLayout()
        header.setSpacing(theme.SPACE_MD)
        self._stats = QLabel("No items loaded")
        header.addWidget(self._stats)
        header.addStretch()

        def _btn(text: str, variant: str, slot: Any) -> QPushButton:
            b = QPushButton(text)
            b.setStyleSheet(theme.button_style(variant))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        header.addWidget(_btn("⚡ Auto-approve…", "config", self._on_auto_approve))
        header.addWidget(_btn("Approve visible", "success", lambda: self._set_visible(True)))
        header.addWidget(_btn("Clear approval", "danger", lambda: self._set_visible(False)))
        layout.addLayout(header)

        # Search
        search = QHBoxLayout()
        search.setSpacing(theme.SPACE_SM)
        search.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by local-ID, label, ext-ID, status…")
        self._search.textChanged.connect(self._on_search)
        search.addWidget(self._search)
        layout.addLayout(search)

        # Filter chips: Type + Status. Each chip toggles inclusion of that
        # dimension value in the filter proxy's dimension filter.
        filter_row = QHBoxLayout()
        filter_row.setSpacing(theme.SPACE_SM)
        filter_row.addWidget(QLabel("Type:"))
        self._type_chips: dict[str, QPushButton] = {}
        for t in ("person", "work", "manuscript"):
            chip = self._make_filter_chip(t, "type")
            filter_row.addWidget(chip)
            self._type_chips[t] = chip
        filter_row.addSpacing(theme.SPACE_LG)
        filter_row.addWidget(QLabel("Status:"))
        self._status_chips: dict[str, QPushButton] = {}
        for s in (_STATUS_NEW, _STATUS_OURS, _STATUS_OTHER, _STATUS_UNKNOWN):
            chip = self._make_filter_chip(s, "status")
            filter_row.addWidget(chip)
            self._status_chips[s] = chip
        filter_row.addStretch()
        self._clear_filters_btn = QPushButton("Clear filters")
        self._clear_filters_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_filters_btn.setStyleSheet(theme.ghost_button_style())
        self._clear_filters_btn.clicked.connect(self._on_clear_filters)
        filter_row.addWidget(self._clear_filters_btn)
        layout.addLayout(filter_row)

        # Table — chained proxies:
        #   model → _proxy (filter/search/sort) → _page_proxy (paginate) → view
        # Pagination at the proxy layer keeps rowCount() at page_size, so
        # the view materialises ≤ 100 rows regardless of backing size.
        self._model = QPEntityModel()
        self._proxy = QPEntityFilterProxy()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)
        self._proxy.setSortRole(Qt.ItemDataRole.UserRole)

        self._page_proxy = _PaginationProxy()
        self._page_proxy.setSourceModel(self._proxy)

        self._table = QTableView()
        self._table.setModel(self._page_proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        # Click the #Claims cell → open the editable claims dialog
        self._table.clicked.connect(self._on_cell_clicked)

        h = self._table.horizontalHeader()
        assert h is not None
        h.setSectionResizeMode(COL_LOCAL_ID, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_LABEL, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_NCLAIMS, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_EXT_ID, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_ISSUES, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_APPROVED, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_ACTIONS, 118)

        # ── Pagination bar (25 / 50 / 100 per page) ─────────────────────
        page_bar = QHBoxLayout()
        page_bar.setSpacing(theme.SPACE_SM)
        page_bar.addWidget(QLabel("Page size:"))
        self._page_size_combo = QComboBox()
        self._page_size_combo.addItems(["25", "50", "100"])
        self._page_size_combo.setCurrentText("50")
        self._page_size_combo.currentTextChanged.connect(self._on_page_size_changed)
        page_bar.addWidget(self._page_size_combo)
        page_bar.addSpacing(theme.SPACE_LG)

        self._first_btn = QPushButton("«")
        self._first_btn.setToolTip("First page")
        self._first_btn.setFixedWidth(36)
        self._first_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._first_btn.setStyleSheet(theme.ghost_button_style())
        self._first_btn.clicked.connect(lambda: self._page_proxy.set_page(0))
        page_bar.addWidget(self._first_btn)

        self._prev_btn = QPushButton("‹")
        self._prev_btn.setToolTip("Previous page")
        self._prev_btn.setFixedWidth(36)
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.setStyleSheet(theme.ghost_button_style())
        self._prev_btn.clicked.connect(
            lambda: self._page_proxy.set_page(self._page_proxy.page() - 1),
        )
        page_bar.addWidget(self._prev_btn)

        self._page_label = QLabel("Page 1 of 1")
        self._page_label.setStyleSheet(
            f"color: {theme.ui('subtext')}; min-width: 120px;"
            " qproperty-alignment: AlignCenter;",
        )
        page_bar.addWidget(self._page_label)

        self._next_btn = QPushButton("›")
        self._next_btn.setToolTip("Next page")
        self._next_btn.setFixedWidth(36)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setStyleSheet(theme.ghost_button_style())
        self._next_btn.clicked.connect(
            lambda: self._page_proxy.set_page(self._page_proxy.page() + 1),
        )
        page_bar.addWidget(self._next_btn)

        self._last_btn = QPushButton("»")
        self._last_btn.setToolTip("Last page")
        self._last_btn.setFixedWidth(36)
        self._last_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._last_btn.setStyleSheet(theme.ghost_button_style())
        self._last_btn.clicked.connect(
            lambda: self._page_proxy.set_page(self._page_proxy.total_pages() - 1),
        )
        page_bar.addWidget(self._last_btn)
        page_bar.addStretch()

        layout.addLayout(page_bar)
        layout.addWidget(self._table, stretch=1)

        # Initialise page size + wire pagination signals
        self._page_proxy.set_page_size(50)
        self._page_proxy.page_changed.connect(self._on_page_changed)
        self._proxy.rowsInserted.connect(self._on_filter_changed)
        self._proxy.rowsRemoved.connect(self._on_filter_changed)
        self._proxy.modelReset.connect(self._on_filter_changed)
        # Refresh actions + stats whenever the view's content set changes,
        # which now includes page navigation.
        self._page_proxy.rowsInserted.connect(self._refresh_actions)
        self._page_proxy.rowsRemoved.connect(self._refresh_actions)
        self._page_proxy.modelReset.connect(self._refresh_actions)

        self._model.dataChanged.connect(self._update_stats)
        self._model.modelReset.connect(self._refresh_actions)
        self._model.rowsInserted.connect(self._refresh_actions)
        self._model.rowsRemoved.connect(self._refresh_actions)

    # ── Public API ───────────────────────────────────────────────────────

    def load_items(self, items: list[Any]) -> None:
        self._model.load(items)
        self._refresh_actions()
        self._update_stats()

    def approved_items(self) -> list[Any]:
        return self._model.approved_items()

    def all_items(self) -> list[Any]:
        return self._model.items()

    def get_all_types(self) -> list[str]:
        return sorted({str(r.get("entity_type") or "") for r in self._model._rows if r.get("entity_type")})

    def get_all_statuses(self) -> list[str]:
        return sorted({str(r.get("status") or "") for r in self._model._rows if r.get("status")})

    def apply_filters(
        self,
        types: set[str] | None,
        statuses: set[str] | None,
    ) -> None:
        self._proxy.set_dimension_filters(
            set(types or ()), set(statuses or ()),
        )
        self._refresh_actions()
        self._update_stats()

    def update_status(self, local_id: str, status: str, qid: str = "", reason: str = "") -> None:
        self._model.update_status(local_id, status, qid=qid, reason=reason)
        self._update_stats()

    def update_validation(self, local_id: str, payload: dict) -> None:
        """Apply a validation payload emitted by _ValidationWorker.row_validated."""
        self._model.update_validation(local_id, payload)
        self._update_stats()

    def rows_snapshot(self) -> list[dict]:
        """Expose a shallow copy of the model's rows for serialisation."""
        return list(self._model._rows)

    # ── Per-row action widgets ──────────────────────────────────────────

    def _refresh_actions(self) -> None:
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        # Operate on the VIEW's model (the page proxy) so we only attach
        # action widgets to the currently-visible page.
        view_model = self._page_proxy
        for row in range(view_model.rowCount()):
            idx = view_model.index(row, COL_ACTIONS)
            self._table.setIndexWidget(idx, None)

        btn_qss = (
            f"QPushButton {{ background: transparent;"
            f" color: {theme.ui('text')};"
            f" border: 1px solid {theme.ui('border')};"
            f" border-radius: {theme.RADIUS_SM}px;"
            f" font-size: {theme.FONT_BASE}px; font-weight: 600;"
            f" padding: 0 4px; min-height: 22px; min-width: 24px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,18);"
            f" border-color: {theme.ui('highlight')}; }}"
        )

        for row in range(view_model.rowCount()):
            idx = view_model.index(row, COL_ACTIONS)
            container = QWidget()
            h = QHBoxLayout(container)
            h.setContentsMargins(2, 1, 2, 1)
            h.setSpacing(4)

            edit_btn = QPushButton("✎")
            edit_btn.setToolTip("Edit label / description")
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.setStyleSheet(btn_qss)
            edit_btn.clicked.connect(lambda _=False, r=row: self._on_edit(r))
            h.addWidget(edit_btn)

            claims_btn = QPushButton("📋")
            claims_btn.setToolTip("Edit claims (add / remove / modify values)")
            claims_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            claims_btn.setStyleSheet(btn_qss)
            claims_btn.clicked.connect(lambda _=False, r=row: self._open_claims_dialog(r))
            h.addWidget(claims_btn)

            view_btn = QPushButton("↗")
            view_btn.setToolTip("View full Wikidata-style entity page")
            view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            view_btn.setStyleSheet(btn_qss)
            view_btn.clicked.connect(lambda _=False, r=row: self._on_view(r))
            h.addWidget(view_btn)

            self._table.setIndexWidget(idx, container)

    def _proxy_to_source(self, view_row: int) -> int:
        """Map a row index from the view (page proxy) back to the source model."""
        page_idx = self._page_proxy.index(view_row, COL_ACTIONS)
        filter_idx = self._page_proxy.mapToSource(page_idx)
        return self._proxy.mapToSource(filter_idx).row()

    def _on_cell_clicked(self, proxy_idx: QModelIndex) -> None:
        """Click routing — #Claims cell → ClaimsEditDialog,
        Status cell → ItemStatusDialog,
        Issues cell → ItemStatusDialog (Validator tab carries the full payload)."""
        if not proxy_idx.isValid():
            return
        col = proxy_idx.column()
        if col == COL_NCLAIMS:
            self._open_claims_dialog(proxy_idx.row())
            return
        if col == COL_STATUS:
            self._open_status_dialog(proxy_idx.row())
            return
        if col == COL_ISSUES:
            self._open_status_dialog(proxy_idx.row())
            return

    def _open_status_dialog(self, proxy_row: int) -> None:
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return
        row = self._model._rows[src]
        ItemStatusDialog(row, parent=self).exec()

    def _open_claims_dialog(self, proxy_row: int) -> None:
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return
        row = self._model._rows[src]
        dlg = ClaimsEditDialog(row, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # Reload: row["_item"].statements was mutated in place; refresh
        # dependent columns (n_claims, ext_id, issues, severity).
        from converter.wikidata.item_validator import validate_item, worst_severity  # noqa: PLC0415

        item = row.get("_item")
        stmts = getattr(item, "statements", []) if item is not None else []
        row["n_claims"] = len(stmts)
        ext_id = ""
        for pid in ("P214", "P8189", "P244", "P227", "P213", "P217"):
            for s in stmts:
                if getattr(s, "property_id", "") == pid:
                    ext_id = f"{pid}: {getattr(s, 'value', '')}"
                    break
            if ext_id:
                break
        row["ext_id"] = ext_id
        issues = validate_item(item) if item is not None else []
        row["issues"] = issues
        row["severity"] = worst_severity(issues)
        tl = self._model.index(src, 0)
        br = self._model.index(src, self._model.columnCount() - 1)
        self._model.dataChanged.emit(tl, br)
        self._update_stats()
        self.items_changed.emit()

    def _on_view(self, proxy_row: int) -> None:
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return
        ItemDetailDialog(self._model._rows[src], parent=self).exec()

    def _on_edit(self, proxy_row: int) -> None:
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return
        row = self._model._rows[src]
        dlg = ItemEditDialog(row, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        label, desc = dlg.edited()
        row["label"] = label
        row["description"] = desc
        # Propagate to the underlying WikidataItem (best-effort)
        item = row.get("_item")
        if item is not None:
            labels = getattr(item, "labels", None)
            descs = getattr(item, "descriptions", None)
            if isinstance(labels, dict):
                key = "he" if "he" in labels else (next(iter(labels), "en"))
                labels[key] = label
            if isinstance(descs, dict):
                key = "en" if "en" in descs else (next(iter(descs), "en"))
                descs[key] = desc
        tl = self._model.index(src, COL_LABEL)
        br = self._model.index(src, COL_LABEL)
        self._model.dataChanged.emit(tl, br)
        self._update_stats()
        self.items_changed.emit()

    # ── Filters + bulk ──────────────────────────────────────────────────

    def _make_filter_chip(self, value: str, kind: str) -> QPushButton:
        """Create a toggle chip that contributes *value* to the type-
        or status-filter set when checked."""
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        chip = QPushButton(value)
        chip.setCheckable(True)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        # Colour chips by meaning: types stay neutral; statuses inherit
        # their pill colour so checked/unchecked reads at a glance.
        if kind == "status":
            bg, fg = _status_colors(value)
            chip.setStyleSheet(
                f"QPushButton {{ background: rgba(255,255,255, 10);"
                f" color: {theme.ui('subtext')};"
                f" border: 1px solid rgba(255,255,255, 30);"
                f" border-radius: {theme.RADIUS_SM}px;"
                f" padding: 4px 10px;"
                f" font-size: {theme.FONT_SM}px; font-weight: 600; }}"
                f"QPushButton:checked {{ background: {bg}; color: {fg};"
                f" border-color: {bg}; }}"
                f"QPushButton:hover {{ border-color: {theme.ui('highlight')}; }}"
            )
        else:
            chip.setStyleSheet(
                f"QPushButton {{ background: rgba(255,255,255, 10);"
                f" color: {theme.ui('subtext')};"
                f" border: 1px solid rgba(255,255,255, 30);"
                f" border-radius: {theme.RADIUS_SM}px;"
                f" padding: 4px 10px;"
                f" font-size: {theme.FONT_SM}px; font-weight: 600; }}"
                f"QPushButton:checked {{ background: rgba(99, 102, 241, 160);"
                f" color: white; border-color: rgba(99, 102, 241, 220); }}"
                f"QPushButton:hover {{ border-color: {theme.ui('highlight')}; }}"
            )
        chip.toggled.connect(self._on_filter_chip_toggled)
        return chip

    def _on_filter_chip_toggled(self, _checked: bool) -> None:
        types = {t for t, c in self._type_chips.items() if c.isChecked()}
        statuses = {s for s, c in self._status_chips.items() if c.isChecked()}
        self._proxy.set_dimension_filters(types, statuses)
        # Force-reset + re-invalidate the page window. set_page(0) is a
        # no-op when already at page 0, which would leave the proxy
        # looking at stale row indices from before the filter change.
        self._page_proxy._page = 0
        self._page_proxy.invalidateFilter()
        self._page_proxy.page_changed.emit(0, self._page_proxy.total_pages())
        self._refresh_actions()
        self._update_stats()

    def _on_clear_filters(self) -> None:
        for c in list(self._type_chips.values()) + list(self._status_chips.values()):
            c.blockSignals(True)
            c.setChecked(False)
            c.blockSignals(False)
        self._proxy.set_dimension_filters(set(), set())
        self._search.clear()
        self._page_proxy._page = 0
        self._page_proxy.invalidateFilter()
        self._page_proxy.page_changed.emit(0, self._page_proxy.total_pages())
        self._refresh_actions()
        self._update_stats()

    def _on_search(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        self._page_proxy._page = 0
        self._page_proxy.invalidateFilter()
        self._page_proxy.page_changed.emit(0, self._page_proxy.total_pages())
        self._refresh_actions()
        self._update_stats()

    def _on_auto_approve(self) -> None:
        # Reuse the extraction_editor auto-approve dialog — we only need a
        # different field set, which we pass as options_for.
        from mhm_pipeline.gui.widgets.extraction_editor import AutoApproveDialog  # noqa: PLC0415

        options = dict(_QP_FIELD_OPTIONS)
        # Override with LIVE values so rules can't target entity types /
        # statuses that are not currently loaded.
        options["entity_type"] = self.get_all_types() or options["entity_type"]
        options["status"] = [
            s for s in (self.get_all_statuses() or options["status"])
            if s != _STATUS_OTHER
        ]
        dlg = AutoApproveDialog(
            self,
            options_for=options,
            fields=_QP_FIELDS,
            numeric_fields=_QP_NUMERIC_FIELDS,
            numeric_field_ranges=_QP_NUMERIC_RANGES,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rules = dlg.rules()
        combinator = dlg.combinator_value()
        matched: list[int] = []
        for i, r in enumerate(self._model._rows):
            # Safety rails: community-owned items and validator-flagged errors
            # can NEVER be auto-approved, regardless of matching rules.
            if r.get("status") == _STATUS_OTHER or r.get("severity") == "error":
                continue
            if evaluate_qp_rules(r, rules, combinator):
                matched.append(i)
        changed = self._model.set_approved_bulk(matched, True)
        self._update_stats()
        self.items_changed.emit()
        QMessageBox.information(
            self, "Auto-approve",
            f"Approved {changed} item{'s' if changed != 1 else ''}.",
        )

    def _set_visible(self, approved: bool) -> None:
        # "Visible" = passes the filter proxy, irrespective of pagination.
        # We want bulk-approve to cover the whole filtered set, not just
        # the currently-rendered page.
        rows: list[int] = []
        for r in range(self._proxy.rowCount()):
            src_idx = self._proxy.mapToSource(self._proxy.index(r, 0))
            rows.append(src_idx.row())
        # Skip rows blocked by safety: community-owned items and validator errors.
        rows = [
            i for i in rows
            if 0 <= i < len(self._model._rows)
            and self._model._rows[i].get("status") != _STATUS_OTHER
            and self._model._rows[i].get("severity") != "error"
        ]
        changed = self._model.set_approved_bulk(rows, approved)
        del changed  # bulk feedback surfaces via _update_stats
        self._update_stats()
        self.items_changed.emit()

    # ── Pagination handlers ─────────────────────────────────────────────

    def _on_page_size_changed(self, text: str) -> None:
        try:
            size = int(text)
        except ValueError:
            return
        self._page_proxy.set_page_size(size)

    def _on_page_changed(self, page: int, total_pages: int) -> None:
        self._page_label.setText(f"Page {page + 1} of {max(1, total_pages)}")
        self._prev_btn.setEnabled(page > 0)
        self._first_btn.setEnabled(page > 0)
        self._next_btn.setEnabled(page + 1 < total_pages)
        self._last_btn.setEnabled(page + 1 < total_pages)

    def _on_filter_changed(self) -> None:
        """Called whenever the underlying filter proxy's row set changes.
        Clamp the current page into range and refresh the page label."""
        total_pages = self._page_proxy.total_pages()
        if self._page_proxy.page() >= total_pages:
            self._page_proxy.set_page(max(0, total_pages - 1))
        else:
            # Same page, but label may have changed (fewer / more total
            # rows after a filter re-evaluation).
            self._on_page_changed(self._page_proxy.page(), total_pages)

    # ── Stats ────────────────────────────────────────────────────────────

    def _update_stats(self) -> None:
        total = self._model.rowCount()
        visible = self._proxy.rowCount() if self._proxy else total
        approved = sum(1 for r in self._model._rows if r.get("approved", False))
        blocked_other = sum(
            1 for r in self._model._rows if r.get("status") == _STATUS_OTHER
        )
        blocked_err = sum(
            1 for r in self._model._rows if r.get("severity") == "error"
        )
        warn = sum(1 for r in self._model._rows if r.get("severity") == "warning")
        pct = (approved / total * 100) if total else 0.0
        base = f"{visible} of {total} visible" if visible != total else f"{total} items"
        extra = f" · {approved} approved ({pct:.0f}%)"
        if blocked_err:
            extra += f" · ✗ {blocked_err} blocked (errors)"
        if blocked_other:
            extra += f" · {blocked_other} blocked (others' items)"
        if warn:
            extra += f" · ⚠ {warn} warnings"
        self._stats.setText(base + extra)
