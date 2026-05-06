"""Wikidata Preview Panel — review and edit enriched items before RDF/upload.

Displayed as Stage 3 (interactive, no background worker).  Users can:
  - Browse all manuscript records in the left list
  - See per-record Wikidata properties with source highlighting in the right table
    · MARC field        → light gray
    · Person NER 🤖     → light blue
    · Provenance NER 🤖 → light purple
    · Contents NER 🤖   → light teal
    · Colophon ML ⚡     → amber (new — from MARC 500 classifier)
    · VIAF / NLI/Mazal  → light green
    · KIMA              → lighter green
  - Double-click any Value cell to correct a mistake
  - Click "Save & Continue to RDF →" to write reviewed JSON and advance
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.widgets.base_visualization_widget import is_dark_mode
from mhm_pipeline.gui.widgets.dynamic_progress_bar import DynamicProgressBar

_ML_SOURCES = {"Colophon ML"}
_NER_SOURCES = {"Person NER", "Provenance NER", "Contents NER"}


# ── Field extraction ──────────────────────────────────────────────────

_ROLE_PROP: dict[str, tuple[str, str]] = {
    "SCRIBE": ("P11603", "transcribed by"),
    "TRANSCRIBED_BY": ("P11603", "transcribed by"),
    "OWNER": ("P127", "owned by"),
}


def _extract_fields(record: dict) -> list[tuple[str, str, str, str]]:
    """Return [(wikidata_prop, field_label, value, source)] for *record*."""
    out: list[tuple[str, str, str, str]] = []

    def add(prop: str, label: str, value: str, source: str) -> None:
        v = str(value).strip()
        if v:
            out.append((prop, label, v, source))

    # ── Core MARC fields ──────────────────────────────────────────────
    add("P1476", "title",    str(record.get("title") or ""),    "MARC")
    add("P571",  "inception", str(record.get("date") or ""),    "MARC")
    add("P407",  "language",  str(record.get("language") or ""), "MARC")

    # Genres
    for genre in record.get("genres") or []:
        add("P136", "genre", str(genre), "MARC")

    # Colophon — amber when colophon ML sentences contributed
    colophon = str(record.get("colophon_text") or "").strip()
    if colophon:
        ml_sents = record.get("ml_colophon_sentences") or []
        add("P1684", "inscription", colophon[:400], "Colophon ML" if ml_sents else "MARC")

    # Notes (first 3)
    for note in list(record.get("notes") or [])[:3]:
        add("P7535", "note", str(note)[:200], "MARC")

    # ── Authority-matched persons (MARC name fields) ───────────────────
    for m in record.get("marc_authority_matches") or []:
        name = str(m.get("display_name") or m.get("name") or "").strip()
        if not name:
            continue
        role_key = str(m.get("role") or "AUTHOR").upper().replace(" ", "_")
        prop, lbl = _ROLE_PROP.get(role_key, ("P50", "author"))
        has_viaf = bool(m.get("viaf_uri") or m.get("viaf_id"))
        has_nli  = bool(m.get("mazal_id") or m.get("nli_id"))
        src = "VIAF" if has_viaf else ("NLI/Mazal" if has_nli else "MARC")
        add(prop, lbl, name, src)

    # ── NER entities ──────────────────────────────────────────────────
    seen: set[str] = set()
    for ent in record.get("entities") or []:
        text = str(ent.get("text") or ent.get("person") or "").strip()
        src_tag = str(ent.get("source") or "")
        ent_type = str(ent.get("label") or ent.get("type") or "").upper()
        role = str(ent.get("role") or "").upper().replace(" ", "_")
        if not text or text in seen:
            continue
        seen.add(text)
        if src_tag == "person_ner":
            prop, lbl = _ROLE_PROP.get(role, ("P50", "author"))
            add(prop, lbl, text, "Person NER")
        elif src_tag == "provenance_ner" and ent_type == "OWNER":
            add("P127", "owned by", text, "Provenance NER")

    # ── KIMA places ───────────────────────────────────────────────────
    for place in record.get("kima_places") or []:
        name = str(place.get("name") or place.get("hebrew_name") or "").strip()
        add("P1071", "location created", name, "KIMA")

    return out


# ── Panel ─────────────────────────────────────────────────────────────


class WikidataPreviewPanel(QWidget):
    """Interactive review of wikidata-ready items with source highlighting."""

    #: Emitted when the user clicks "Save & Continue".
    #: Carries the path to the reviewed JSON written to disk.
    continue_clicked = pyqtSignal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[dict] = []
        self._authority_path: Path | None = None
        # {control_number: {row_key: edited_value}}
        self._edits: dict[str, dict[str, str]] = {}
        self._current_cn: str = ""
        self._current_row_keys: list[str] = []
        self._setup_ui()

    # ── Setup ──────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        # ── Info banner ───────────────────────────────────────────────
        banner = QFrame()
        banner.setFrameShape(QFrame.Shape.StyledPanel)
        banner.setStyleSheet(theme.info_banner_style())
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(10, 4, 10, 4)
        bl.addWidget(
            QLabel(
                "Review enriched Wikidata properties before building the RDF graph.  "
                "⚡ = ML model  🤖 = NER model.  Double-click a value to edit."
            )
        )
        bl.addStretch()
        root.addWidget(banner)

        # ── Dynamic progress bar (synchronous load — driven manually) ──
        self._progress = DynamicProgressBar()
        root.addWidget(self._progress)

        # ── Splitter: record list | property table ────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(4)
        ll.addWidget(QLabel("Records:"))
        self._record_list = QListWidget()
        self._record_list.currentRowChanged.connect(self._on_record_selected)
        ll.addWidget(self._record_list)
        splitter.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.setSpacing(4)
        rl.addWidget(QLabel("Wikidata properties — double-click Value to edit:"))
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Prop", "Field", "Value", "Source"])
        hh = self._table.horizontalHeader()
        hh.resizeSection(0, 68)
        hh.resizeSection(1, 120)
        hh.setSectionResizeMode(2, hh.ResizeMode.Stretch)
        hh.resizeSection(3, 148)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._on_cell_changed)
        rl.addWidget(self._table)
        splitter.addWidget(right)

        splitter.setSizes([220, 600])
        root.addWidget(splitter, 1)

        # ── Bottom bar ────────────────────────────────────────────────
        bottom = QHBoxLayout()
        self._status_lbl = QLabel("No data loaded.")
        self._status_lbl.setStyleSheet("color: palette(mid);")
        bottom.addWidget(self._status_lbl)
        bottom.addStretch()
        self._continue_btn = QPushButton("Save & Continue to RDF  →")
        self._continue_btn.setEnabled(False)
        self._continue_btn.setStyleSheet(theme.success_btn_style())
        self._continue_btn.clicked.connect(self._on_continue)
        bottom.addWidget(self._continue_btn)
        root.addLayout(bottom)

    # ── Public API ─────────────────────────────────────────────────────

    def load_authority_output(self, path: Path) -> None:
        """Load authority-enriched JSON and populate the review panel."""
        # Synchronous load — drive the DynamicProgressBar manually around
        # the operation. Indeterminate mode covers the file-read + parse;
        # once we know the record count we switch to a determinate bar.
        self._progress.reset()
        self._progress.set_substep("Loading preview…")
        self._progress.set_total(0)  # indeterminate while reading
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._records = data if isinstance(data, list) else data.get("records", [])
            self._authority_path = path
            self._edits.clear()
            self._populate_record_list()
            n = len(self._records)
            self._status_lbl.setText(
                f"{n} record{'s' if n != 1 else ''} — review, then click Continue."
            )
            self._continue_btn.setEnabled(n > 0)
            if n > 0:
                self._record_list.setCurrentRow(0)
            self._progress.finish("Preview loaded", success=True)
        except Exception as exc:
            self._status_lbl.setText(f"Could not load: {exc}")
            self._progress.finish("Preview load failed", success=False)

    # ── Internal ───────────────────────────────────────────────────────

    def _populate_record_list(self) -> None:
        self._record_list.clear()
        for rec in self._records:
            cn = str(rec.get("_control_number") or "?")
            title = str(rec.get("title") or "").strip()[:55]
            self._record_list.addItem(f"{cn}  {title}")

    def _on_record_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._records):
            return
        rec = self._records[row]
        self._current_cn = str(rec.get("_control_number") or str(row))
        self._populate_table(rec)

    def _populate_table(self, record: dict) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._current_row_keys = []

        dark = theme.is_dark()
        fields = _extract_fields(record)
        cn = self._current_cn
        overrides = self._edits.get(cn, {})

        prop_color = "#94a3b8" if dark else "#475569"
        ml_color   = "#fcd34d" if dark else "#92400e"
        ner_color  = "#93c5fd" if dark else "#1e40af"

        for idx, (prop, label, value, source) in enumerate(fields):
            r = self._table.rowCount()
            self._table.insertRow(r)

            row_key = f"{idx}|{prop}|{label}"
            self._current_row_keys.append(row_key)
            display_value = overrides.get(row_key, value)

            # Prop (locked)
            p = QTableWidgetItem(prop)
            p.setFlags(p.flags() & ~Qt.ItemFlag.ItemIsEditable)
            p.setForeground(QBrush(QColor(prop_color)))
            self._table.setItem(r, 0, p)

            # Field label (locked)
            lbl_item = QTableWidgetItem(label)
            lbl_item.setFlags(lbl_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 1, lbl_item)

            # Value (editable, carries row_key in UserRole)
            v = QTableWidgetItem(display_value)
            v.setData(Qt.ItemDataRole.UserRole, row_key)
            if source in _ML_SOURCES:
                v.setForeground(QBrush(QColor(ml_color)))
            elif source in _NER_SOURCES:
                v.setForeground(QBrush(QColor(ner_color)))
            self._table.setItem(r, 2, v)

            # Source badge (locked, colored background)
            s = QTableWidgetItem(theme.source_label(source))
            s.setFlags(s.flags() & ~Qt.ItemFlag.ItemIsEditable)
            s.setBackground(QBrush(QColor(theme.source_bg(source))))
            self._table.setItem(r, 3, s)

        self._table.blockSignals(False)
        self._table.resizeRowsToContents()

    def _on_cell_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 2:
            return
        row_key = item.data(Qt.ItemDataRole.UserRole)
        if row_key is None:
            return
        cn = self._current_cn
        if cn not in self._edits:
            self._edits[cn] = {}
        self._edits[cn][row_key] = item.text()

    def _apply_edits_to_records(self) -> list[dict]:
        """Return a deep copy of records with user edits applied."""
        edited = copy.deepcopy(self._records)
        if not self._edits:
            return edited

        for rec in edited:
            cn = str(rec.get("_control_number") or "")
            overrides = self._edits.get(cn)
            if not overrides:
                continue
            fields = _extract_fields(rec)
            for row_key, new_val in overrides.items():
                try:
                    idx = int(row_key.split("|")[0])
                    if idx >= len(fields):
                        continue
                    prop, label, _, _ = fields[idx]
                    # Apply edit to the matching record field
                    if prop == "P1476":
                        rec["title"] = new_val
                    elif prop == "P571":
                        rec["date"] = new_val
                    elif prop == "P407":
                        rec["language"] = new_val
                    elif prop == "P1684":
                        rec["colophon_text"] = new_val
                except (ValueError, IndexError):
                    pass
        return edited

    def _on_continue(self) -> None:
        if not self._authority_path:
            return
        output_path = self._authority_path.parent / "authority_enriched_reviewed.json"
        try:
            reviewed = self._apply_edits_to_records()
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(reviewed, f, ensure_ascii=False, indent=2, default=str)
            n_edited = sum(len(v) for v in self._edits.values())
            self._status_lbl.setText(
                f"Saved {n_edited} edit{'s' if n_edited != 1 else ''} → {output_path.name}"
            )
            self.continue_clicked.emit(output_path)
        except Exception as exc:
            self._status_lbl.setText(f"Save error: {exc}")
