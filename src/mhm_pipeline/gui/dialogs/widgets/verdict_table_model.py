"""``QAbstractTableModel`` over the eval-agent's ``results.jsonl``.

The eval-agent emits one JSON-Lines record per AI verdict (see
``eval_agent.evaluators._base.Verdict.to_jsonl_record``). This model
reads them lazily, exposes a friendly default set of columns to the
curator, and an advanced set for engineers debugging through the
**Show advanced details** toggle in the dialog footer.

Filtering is intentionally inside this model rather than via a
``QSortFilterProxyModel`` because the filter logic operates on the
*meaning* of a record (failure / unsure / reused / error) rather
than on textual cell content. The visible-rows list is recomputed
in-place on each filter change.

Row cap: 5000. The dialog renders a "showing 5000 of N — narrow your
filters to see more" hint at the table footer when the cap is hit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from mhm_pipeline.gui.dialogs.widgets.friendly_copy import (
    humanise_evaluator,
    humanise_verdict,
)

_ROW_CAP: int = 5000

# ── Column descriptors ───────────────────────────────────────────────


class _Column:
    """Descriptor for one column rendered by the model."""

    def __init__(
        self,
        key: str,
        header: str,
        *,
        advanced: bool = False,
        tooltip: str | None = None,
    ) -> None:
        self.key = key
        self.header = header
        self.advanced = advanced
        self.tooltip = tooltip


# Order matters — left-to-right in the table. Default columns first,
# advanced columns appended; ``set_advanced(True)`` flips visibility.
_COLUMNS: list[_Column] = [
    _Column("manuscript",  "Manuscript",
            tooltip="The NLI control number of the manuscript record."),
    _Column("checker",     "Which AI checker",
            tooltip="Which Stage-2 model produced this prediction."),
    _Column("candidate",   "What it looked at",
            tooltip="The text the AI judged. Hover for the full value."),
    _Column("verdict",     "Verdict",
            tooltip="The AI's overall call: looks right / partly / got it wrong."),
    _Column("name_ok",     "Name",     tooltip="Did the AI think the name itself was right?"),
    _Column("type_ok",     "Type",     tooltip="Did the AI think the entity type was right?"),
    _Column("role_ok",     "Role",     tooltip="Did the AI think the role / occupation was right?"),
    _Column("why",         "Why",      tooltip="The AI's short explanation."),
    _Column("reused",      "Reused",
            tooltip="Shows when the AI's answer was reused from a previous run."),
    # ── Advanced columns ────────────────────────────────────────
    _Column("record_id",    "Record ID",    advanced=True),
    _Column("evaluator_id", "Evaluator ID", advanced=True),
    _Column("sub_type",     "Sub-type",     advanced=True),
    _Column("confidence",   "Confidence",   advanced=True),
    _Column("cache_key",    "Cache key",    advanced=True),
    _Column("judged_at",    "Judged at",    advanced=True),
    _Column("error",        "Error",        advanced=True),
]


def _candidate_text(candidate: Any) -> str:
    """Return a short, human-readable rendering of the candidate payload."""
    if isinstance(candidate, dict):
        for key in ("text", "name", "value", "label", "candidate"):
            value = candidate.get(key)
            if value:
                return str(value)
        # Fall through to JSON compaction so the user at least sees
        # something rather than "{}".
        try:
            return json.dumps(candidate, ensure_ascii=False)[:200]
        except (TypeError, ValueError):
            return repr(candidate)
    if candidate is None:
        return ""
    return str(candidate)


class VerdictTableModel(QAbstractTableModel):
    """Table model over a ``results.jsonl`` file."""

    # Custom data role used by the table view to render the verdict
    # cells as ``StatusPill`` widgets instead of plain text. The view
    # asks for ``Qt.ItemDataRole.UserRole`` and receives the raw
    # verdict status string.
    StatusRole = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._all_rows: list[dict[str, Any]] = []
        self._visible_rows: list[dict[str, Any]] = []
        self._advanced: bool = False

        # Filter toggles — independent (AND-combined when multiple active).
        self._filter_failures: bool = False
        self._filter_unsure: bool = False
        self._filter_reused: bool = False
        self._filter_errors: bool = False

    # ── Loading ─────────────────────────────────────────────────────

    def load(self, jsonl_path: Path) -> None:
        """Load every record from *jsonl_path*. Tolerates a missing file."""
        self.beginResetModel()
        self._all_rows = []
        try:
            path = Path(jsonl_path)
            if path.exists():
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            # Skip malformed lines rather than abort.
                            continue
                        if isinstance(payload, dict):
                            self._all_rows.append(payload)
        finally:
            self._recompute_visible()
            self.endResetModel()

    def total_row_count(self) -> int:
        """Total parsed rows (vs ``rowCount`` which reflects filters)."""
        return len(self._all_rows)

    def is_capped(self) -> bool:
        """Return True when the visible row count was clipped by the cap."""
        return len(self._visible_rows) >= _ROW_CAP and self._matches_count() > _ROW_CAP

    # ── Visibility / filters ────────────────────────────────────────

    def set_advanced(self, advanced: bool) -> None:
        """Toggle visibility of the advanced-only columns."""
        self.beginResetModel()
        self._advanced = bool(advanced)
        self.endResetModel()

    def advanced(self) -> bool:
        return self._advanced

    def filter_failures_only(self, on: bool) -> None:
        self.beginResetModel()
        self._filter_failures = bool(on)
        self._recompute_visible()
        self.endResetModel()

    def filter_unsure_only(self, on: bool) -> None:
        self.beginResetModel()
        self._filter_unsure = bool(on)
        self._recompute_visible()
        self.endResetModel()

    def filter_reused_only(self, on: bool) -> None:
        self.beginResetModel()
        self._filter_reused = bool(on)
        self._recompute_visible()
        self.endResetModel()

    def filter_errors_only(self, on: bool) -> None:
        self.beginResetModel()
        self._filter_errors = bool(on)
        self._recompute_visible()
        self.endResetModel()

    # ── Row access (used by the detail pane) ────────────────────────

    def raw_row(self, row: int) -> dict[str, Any] | None:
        """Return the raw parsed record at *row* (visible index)."""
        if 0 <= row < len(self._visible_rows):
            return self._visible_rows[row]
        return None

    # ── QAbstractTableModel ─────────────────────────────────────────

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._visible_rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._active_columns())

    def headerData(  # noqa: N802 — Qt-style camelCase
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if role != int(Qt.ItemDataRole.DisplayRole):
            if role == int(Qt.ItemDataRole.ToolTipRole) and orientation == Qt.Orientation.Horizontal:
                cols = self._active_columns()
                if 0 <= section < len(cols):
                    return cols[section].tooltip or ""
            return None
        if orientation == Qt.Orientation.Horizontal:
            cols = self._active_columns()
            if 0 <= section < len(cols):
                return cols[section].header
            return None
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        col_idx = index.column()
        if row < 0 or row >= len(self._visible_rows):
            return None
        cols = self._active_columns()
        if col_idx < 0 or col_idx >= len(cols):
            return None
        column = cols[col_idx]
        record = self._visible_rows[row]

        if role == int(VerdictTableModel.StatusRole):
            return self._status_for(column.key, record)

        if role == int(Qt.ItemDataRole.DisplayRole):
            return self._display_for(column.key, record)

        if role == int(Qt.ItemDataRole.ToolTipRole):
            return self._tooltip_for(column.key, record)

        return None

    # ── Internal helpers ────────────────────────────────────────────

    def _active_columns(self) -> list[_Column]:
        if self._advanced:
            return _COLUMNS
        return [c for c in _COLUMNS if not c.advanced]

    def _recompute_visible(self) -> None:
        rows: list[dict[str, Any]] = []
        for record in self._all_rows:
            if not self._matches_filters(record):
                continue
            rows.append(record)
            if len(rows) >= _ROW_CAP:
                break
        self._visible_rows = rows

    def _matches_count(self) -> int:
        return sum(1 for r in self._all_rows if self._matches_filters(r))

    def _matches_filters(self, record: dict[str, Any]) -> bool:
        verdict = record.get("verdict") or {}
        overall = str(verdict.get("overall", "")).lower()
        if self._filter_failures and overall not in {"fail", "no"}:
            return False
        if self._filter_unsure and overall not in {"abstain", "unsure", "unknown"}:
            return False
        if self._filter_reused and not record.get("cache_key"):
            return False
        if self._filter_errors and not record.get("error"):
            return False
        return True

    # ── Cell renderers ──────────────────────────────────────────────

    def _display_for(self, key: str, record: dict[str, Any]) -> str:
        verdict = record.get("verdict") or {}

        if key == "manuscript":
            raw = str(record.get("record_id") or "")
            return raw.split("/")[-1] or raw
        if key == "checker":
            return humanise_evaluator(str(record.get("evaluator_id") or ""))
        if key == "candidate":
            text = _candidate_text(record.get("candidate"))
            return text[:80] + ("…" if len(text) > 80 else "")
        if key == "verdict":
            return humanise_verdict(str(verdict.get("overall") or ""))
        if key in {"name_ok", "type_ok", "role_ok"}:
            raw = str(verdict.get(key, ""))
            return _aspect_glyph(raw)
        if key == "why":
            why = str(verdict.get("reasoning") or "").strip()
            compact = " ".join(why.split())
            return compact[:120] + ("…" if len(compact) > 120 else "")
        if key == "reused":
            return "♻ reused" if record.get("cache_key") else ""
        if key == "record_id":
            return str(record.get("record_id") or "")
        if key == "evaluator_id":
            return str(record.get("evaluator_id") or "")
        if key == "sub_type":
            return str(record.get("sub_type") or "")
        if key == "confidence":
            conf = record.get("confidence")
            if isinstance(conf, (int, float)):
                return f"{float(conf):.2f}"
            return ""
        if key == "cache_key":
            return str(record.get("cache_key") or "")
        if key == "judged_at":
            return str(record.get("judged_at") or "")
        if key == "error":
            return str(record.get("error") or "")
        return ""

    def _tooltip_for(self, key: str, record: dict[str, Any]) -> str:
        verdict = record.get("verdict") or {}
        if key == "candidate":
            return _candidate_text(record.get("candidate"))
        if key == "why":
            return str(verdict.get("reasoning") or "")
        if key == "verdict":
            return humanise_verdict(str(verdict.get("overall") or ""))
        if key == "reused" and record.get("cache_key"):
            return (
                "This answer was reused from a previous run with the same "
                "prediction — no Gemini call was made."
            )
        if key == "manuscript":
            return str(record.get("record_id") or "")
        return ""

    def _status_for(self, key: str, record: dict[str, Any]) -> str:
        """Return the raw status string for cells rendered via ``StatusPill``."""
        verdict = record.get("verdict") or {}
        if key == "verdict":
            return str(verdict.get("overall") or "")
        if key in {"name_ok", "type_ok", "role_ok"}:
            return str(verdict.get(key) or "")
        return ""


def _aspect_glyph(raw: str) -> str:
    """Compact one-character glyph for the Name/Type/Role aspect cells."""
    key = (raw or "").strip().lower()
    if key in {"yes", "ok", "full"}:
        return "✓"
    if key in {"no", "fail"}:
        return "✗"
    return "—"


__all__ = ["VerdictTableModel"]
