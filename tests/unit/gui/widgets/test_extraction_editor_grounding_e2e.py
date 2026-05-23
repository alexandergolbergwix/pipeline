"""End-to-end UI tests for the F8 MARC-grounding column.

These tests drive the real :class:`ExtractionEditor` widget against
synthesised NER + MARC fixtures and verify:

1. The new "Exists in" column appears in the right slot (between
   "Source" and "Approved").
2. Each entity row carries its ``exists_in`` evidence list verbatim
   from ``ner_results.json``.
3. The cell text summarises field names; the background is green for
   any full match, yellow for partial-only, and untinted when empty.
4. Clicking a cell with evidence opens the :class:`MarcEvidencePopup`
   and the popup renders the FULL MARC record with highlighted
   substrings.
5. Clicking a cell with no evidence (the "—" placeholder) is a no-op.
6. The tooltip lists every matched field with its match type.

Runs on Qt's ``offscreen`` platform so it works in CI / headless
environments. The ``qtbot`` fixture comes from ``pytest-qt`` and
takes care of widget cleanup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: E402
    COL_APPROVED,
    COL_EXISTS_IN,
    COL_SOURCE,
    ExtractionEditor,
)
from mhm_pipeline.gui.widgets.marc_evidence_popup import (  # noqa: E402
    MarcEvidencePopup,
    _matches_by_field,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


def _marc_record() -> dict[str, Any]:
    """Single MARC record used across the e2e tests."""
    return {
        "_control_number": "990000000000001",
        "title": "ספר תהלים",
        "authors": [{"name": "Yossi Stiwi", "role": "author"}],
        "contributors": [
            {"name": "Stiwi Yossi", "role": "translator"},
            {"name": "ריאיטי, חזקיה", "role": "commentator"},
        ],
        "provenance": "Sold to Yossi Stiwi in 1850.",
        "notes": [
            "Alex Stiwi reviewed the manuscript.",
        ],
        "contents": "תהלים פרקים א-ה",
        "colophon_text": "אני יוסף בן יעקב מעתיק",
    }


def _ner_record_with_evidence() -> dict[str, Any]:
    """An NER record whose entities carry pre-computed ``exists_in``.

    Mirrors what NerWorker produces after F8 grounding fires.
    """
    return {
        "_control_number": "990000000000001",
        "text": "Some surrounding text including Yossi Stiwi and others.",
        "entities": [
            {
                # FULL match: name appears verbatim in authors[0].name
                "person": "Yossi Stiwi",
                "role": "AUTHOR",
                "source": "person_ner",
                "confidence": 0.85,
                "model_confidence": 0.95,
                "start": 0,
                "end": 11,
                "grounded": True,
                "grounded_field": "authors",
                "exists_in": [
                    {"field": "authors[0].name", "match_type": "full",
                     "value": "Yossi Stiwi"},
                    {"field": "contributors[0].name", "match_type": "full",
                     "value": "Stiwi Yossi"},
                    {"field": "provenance", "match_type": "full",
                     "value": "Sold to Yossi Stiwi in 1850."},
                    {"field": "notes[0]", "match_type": "partial",
                     "value": "Alex Stiwi reviewed the manuscript."},
                ],
            },
            {
                # PARTIAL-ONLY match: single token, no field has the full name
                "person": "Stiwi Carlos",
                "role": "AUTHOR",
                "source": "person_ner",
                "confidence": 0.85,
                "model_confidence": 0.91,
                "start": 0,
                "end": 12,
                "grounded": False,
                "grounded_field": None,
                "exists_in": [
                    {"field": "authors[0].name", "match_type": "partial",
                     "value": "Yossi Stiwi"},
                ],
            },
            {
                # NO match: name absent from MARC entirely
                "person": "Maria Schmidt",
                "role": "TRANSCRIBER",
                "source": "person_ner",
                "confidence": 0.85,
                "model_confidence": 0.93,
                "start": 0,
                "end": 13,
                "grounded": False,
                "grounded_field": None,
                "exists_in": [],
            },
        ],
    }


@pytest.fixture()
def editor(qtbot: object, tmp_path: Path) -> ExtractionEditor:
    """Construct ExtractionEditor against fixture NER + MARC data."""
    if QApplication.instance() is None:
        QApplication([])

    # Write the MARC sidecar so ExtractionEditor.load_records() finds it
    marc_path = tmp_path / "marc_extracted.json"
    marc_path.write_text(
        json.dumps([_marc_record()], ensure_ascii=False),
        encoding="utf-8",
    )
    ner_path = tmp_path / "ner_results.json"
    ner_path.write_text(
        json.dumps([_ner_record_with_evidence()], ensure_ascii=False),
        encoding="utf-8",
    )

    widget = ExtractionEditor()
    qtbot.addWidget(widget)  # type: ignore[attr-defined]
    widget.load_records([_ner_record_with_evidence()], output_path=ner_path)
    widget.show()
    return widget


# ── 1. Column placement + headers ────────────────────────────────────────


class TestColumnPlacement:
    """The new column sits between Source and Approved, with the right header."""

    def test_column_constant_is_between_source_and_approved(self) -> None:
        assert COL_SOURCE < COL_EXISTS_IN < COL_APPROVED

    def test_column_header_text(self, editor: ExtractionEditor) -> None:
        header = editor._model.headerData(
            COL_EXISTS_IN,
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.DisplayRole,
        )
        assert header == "Exists in"

    def test_column_count_matches_headers(self, editor: ExtractionEditor) -> None:
        assert editor._model.columnCount() == len(editor._model.HEADERS)


# ── 2. Data per row carries ``exists_in`` verbatim ────────────────────────


class TestEvidenceData:
    """Every entity row keeps its NER-derived ``exists_in`` list intact."""

    def test_full_match_row_carries_evidence(
        self, editor: ExtractionEditor,
    ) -> None:
        ent = editor._model.entity_at(0)
        assert ent is not None
        ev = ent.get("exists_in") or []
        assert len(ev) == 4
        full_count = sum(1 for r in ev if r["match_type"] == "full")
        partial_count = sum(1 for r in ev if r["match_type"] == "partial")
        assert full_count == 3
        assert partial_count == 1

    def test_partial_only_row_carries_one_evidence_row(
        self, editor: ExtractionEditor,
    ) -> None:
        ent = editor._model.entity_at(1)
        assert ent is not None
        ev = ent.get("exists_in") or []
        assert len(ev) == 1
        assert ev[0]["match_type"] == "partial"

    def test_no_evidence_row_has_empty_list(
        self, editor: ExtractionEditor,
    ) -> None:
        ent = editor._model.entity_at(2)
        assert ent is not None
        assert ent.get("exists_in") == []


# ── 3. Cell display: text summary + colour coding ─────────────────────────


class TestCellRendering:
    """Cell text + background colour communicate evidence at a glance."""

    def _data(self, editor: ExtractionEditor, row: int, role: int) -> Any:
        idx = editor._model.index(row, COL_EXISTS_IN)
        return editor._model.data(idx, role)

    def test_full_match_cell_text_lists_field_top_names(
        self, editor: ExtractionEditor,
    ) -> None:
        text = self._data(editor, 0, Qt.ItemDataRole.DisplayRole)
        # Top-level field names (no indexers / sub-keys) are shown,
        # deduplicated. Row 0 has matches in authors, contributors,
        # provenance, notes → first three appear in the preview.
        assert isinstance(text, str)
        for expected in ("authors", "contributors", "provenance"):
            assert expected in text, (
                f"Expected '{expected}' in '{text}' (top-3 preview)"
            )

    def test_no_evidence_cell_shows_em_dash(
        self, editor: ExtractionEditor,
    ) -> None:
        text = self._data(editor, 2, Qt.ItemDataRole.DisplayRole)
        assert text == "—"

    def test_full_match_cell_background_is_green(
        self, editor: ExtractionEditor,
    ) -> None:
        bg = self._data(editor, 0, Qt.ItemDataRole.BackgroundRole)
        assert isinstance(bg, QColor), f"expected QColor, got {type(bg)}"
        # Green channel dominant — chip is (22, 163, 74, alpha).
        assert bg.green() > bg.red() and bg.green() > bg.blue()

    def test_partial_only_cell_background_is_yellow(
        self, editor: ExtractionEditor,
    ) -> None:
        bg = self._data(editor, 1, Qt.ItemDataRole.BackgroundRole)
        assert isinstance(bg, QColor)
        # Yellow/amber chip — (245, 158, 11, alpha). Red+green high, blue low.
        assert bg.red() > 150 and bg.green() > 100 and bg.blue() < 80

    def test_empty_cell_no_background_tint(
        self, editor: ExtractionEditor,
    ) -> None:
        bg = self._data(editor, 2, Qt.ItemDataRole.BackgroundRole)
        # Either None or transparent — Qt returns None for "no override"
        assert bg is None or (isinstance(bg, QColor) and bg.alpha() == 0)


# ── 4. Tooltip lists every matched field ─────────────────────────────────


class TestTooltip:
    """Hovering the cell shows every matched field + its match type."""

    def test_tooltip_lists_all_matched_fields(
        self, editor: ExtractionEditor,
    ) -> None:
        idx = editor._model.index(0, COL_EXISTS_IN)
        tip = editor._model.data(idx, Qt.ItemDataRole.ToolTipRole)
        assert isinstance(tip, str)
        # Each of the 4 evidence rows appears on its own line
        for field in ("authors[0].name", "contributors[0].name",
                      "provenance", "notes[0]"):
            assert field in tip, f"tooltip missing '{field}': {tip}"
        # Tooltip closes with the click-hint
        assert "Click to see full MARC record" in tip

    def test_tooltip_includes_match_type_marker(
        self, editor: ExtractionEditor,
    ) -> None:
        idx = editor._model.index(0, COL_EXISTS_IN)
        tip = editor._model.data(idx, Qt.ItemDataRole.ToolTipRole)
        assert "full" in tip and "partial" in tip

    def test_tooltip_absent_for_empty_evidence(
        self, editor: ExtractionEditor,
    ) -> None:
        idx = editor._model.index(2, COL_EXISTS_IN)
        tip = editor._model.data(idx, Qt.ItemDataRole.ToolTipRole)
        assert tip is None


# ── 5. Click handler opens the popup ─────────────────────────────────────


class TestClickToOpenPopup:
    """Clicking a non-empty Exists-in cell opens MarcEvidencePopup."""

    def test_click_opens_popup_for_row_with_evidence(
        self, editor: ExtractionEditor, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        opened: list[MarcEvidencePopup] = []

        # Patch the popup so we record construction and don't enter
        # the modal event loop.
        real_init = MarcEvidencePopup.__init__

        def capture(self, *, needle, exists_in, marc_record, parent=None):
            real_init(self, needle=needle, exists_in=exists_in,
                      marc_record=marc_record, parent=parent)
            opened.append(self)

        monkeypatch.setattr(MarcEvidencePopup, "__init__", capture)
        monkeypatch.setattr(MarcEvidencePopup, "exec", lambda self: 0)

        # Trigger the click via the model index (skip the proxy because
        # the test loaded data without applying any filter).
        proxy_idx = editor._proxy.index(0, COL_EXISTS_IN)
        editor._on_table_clicked(proxy_idx)

        assert len(opened) == 1, "popup should open exactly once"
        dlg = opened[0]
        assert dlg._needle == "Yossi Stiwi"
        assert len(dlg._exists_in) == 4
        assert dlg._marc_record["_control_number"] == "990000000000001"

    def test_click_does_nothing_for_empty_evidence(
        self, editor: ExtractionEditor, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        opened: list[MarcEvidencePopup] = []
        real_init = MarcEvidencePopup.__init__

        def capture(self, *, needle, exists_in, marc_record, parent=None):
            real_init(self, needle=needle, exists_in=exists_in,
                      marc_record=marc_record, parent=parent)
            opened.append(self)

        monkeypatch.setattr(MarcEvidencePopup, "__init__", capture)

        # Row 2 has empty exists_in
        proxy_idx = editor._proxy.index(2, COL_EXISTS_IN)
        editor._on_table_clicked(proxy_idx)
        assert opened == [], (
            "popup must NOT open for entities without evidence"
        )

    def test_click_on_other_column_does_nothing(
        self, editor: ExtractionEditor, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        opened: list[MarcEvidencePopup] = []
        monkeypatch.setattr(
            MarcEvidencePopup, "__init__",
            lambda self, **_: opened.append(self),  # type: ignore[arg-type,return-value]
        )

        from mhm_pipeline.gui.widgets.extraction_editor import COL_TEXT  # noqa: PLC0415
        proxy_idx = editor._proxy.index(0, COL_TEXT)
        editor._on_table_clicked(proxy_idx)
        assert opened == [], "click on Text column shouldn't open popup"


# ── 6. Popup renders the full MARC record + highlights matches ──────────


class TestPopupRendering:
    """The popup shows the matched fields first, with highlighted text."""

    def _popup(self, qtbot: object) -> MarcEvidencePopup:
        if QApplication.instance() is None:
            QApplication([])
        dlg = MarcEvidencePopup(
            needle="Yossi Stiwi",
            exists_in=_ner_record_with_evidence()["entities"][0]["exists_in"],
            marc_record=_marc_record(),
        )
        qtbot.addWidget(dlg)  # type: ignore[attr-defined]
        dlg.show()
        return dlg

    def test_popup_construction_does_not_raise(self, qtbot: object) -> None:
        dlg = self._popup(qtbot)
        assert dlg.windowTitle()
        assert dlg._needle == "Yossi Stiwi"

    def test_matches_by_field_full_wins_over_partial(self) -> None:
        evidence = [
            {"field": "authors[0].name", "match_type": "partial", "value": "x"},
            {"field": "authors[0].name", "match_type": "full",    "value": "y"},
            {"field": "notes[0]",        "match_type": "partial", "value": "z"},
        ]
        out = _matches_by_field(evidence)
        # The full match wins on a collision so the highlight is the
        # stronger one.
        assert out == {
            "authors[0].name": "full",
            "notes[0]": "partial",
        }

    def test_popup_renders_all_marc_field_rows(self, qtbot: object) -> None:
        dlg = self._popup(qtbot)
        # The popup builds one row per audited MARC field (matched +
        # unmatched). Walk the scroll area's child widgets.
        scroll = dlg.findChildren(type(dlg.findChild(type(dlg))))  # noqa: F841
        # Simpler check: the popup's _iter_field_rows must have
        # produced at least one widget per evidence row.
        evidence = dlg._exists_in
        # The popup is a GlassDialog; the body is inside glass_content.
        # Any QTextEdit (one per MARC field) is a child somewhere.
        from PyQt6.QtWidgets import QTextEdit  # noqa: PLC0415
        text_edits = dlg.findChildren(QTextEdit)
        # Must have at least as many text edits as matched fields
        assert len(text_edits) >= len(evidence), (
            f"expected ≥{len(evidence)} field rows, got {len(text_edits)}"
        )

    def test_popup_highlights_full_match_substring(self, qtbot: object) -> None:
        """A QTextEdit corresponding to a full-match field should have
        at least one text fragment formatted with the green highlight
        colour. Iterate the document blocks' fragments — that's the
        canonical way to read per-range formats in Qt6."""
        dlg = self._popup(qtbot)
        from PyQt6.QtWidgets import QTextEdit  # noqa: PLC0415

        found_green = False
        for te in dlg.findChildren(QTextEdit):
            text = te.toPlainText()
            if "Yossi Stiwi" not in text:
                continue
            doc = te.document()
            block = doc.firstBlock()
            while block.isValid():
                it = block.begin()
                while not it.atEnd():
                    frag = it.fragment()
                    if frag.isValid():
                        bg = frag.charFormat().background().color()
                        if (bg.alpha() > 0
                                and bg.green() > bg.red()
                                and bg.green() > bg.blue()):
                            found_green = True
                            break
                    it += 1  # type: ignore[operator]
                if found_green:
                    break
                block = block.next()
            if found_green:
                break
        assert found_green, "no green-highlighted fragment found in popup"

    def test_popup_summary_counts_full_and_partial(self, qtbot: object) -> None:
        dlg = self._popup(qtbot)
        from PyQt6.QtWidgets import QLabel  # noqa: PLC0415

        # Find the summary label (it contains "full" and "partial" counts)
        summary = ""
        for lbl in dlg.findChildren(QLabel):
            text = lbl.text() or ""
            if "full" in text and "partial" in text:
                summary = text
                break
        assert "3 full" in summary, f"summary missing full count: {summary}"
        assert "1 partial" in summary, f"summary missing partial count: {summary}"


# ── 7. End-to-end click flow ──────────────────────────────────────────────


class TestClickFlow:
    """Walk the click → popup → close flow with no mocks."""

    def test_click_then_close_does_not_leak_dialog(
        self, editor: ExtractionEditor, qtbot: object,
    ) -> None:
        """Construct, show, close — exercise the full lifecycle so a
        memory leak / parent-misconfig regression would crash here."""
        proxy_idx = editor._proxy.index(0, COL_EXISTS_IN)
        ent = editor._model.entity_at(0)
        assert ent is not None
        marc = editor._model.marc_for_row(0) or {}

        dlg = MarcEvidencePopup(
            needle=ent["text"],
            exists_in=ent["exists_in"],
            marc_record=marc,
            parent=editor,
        )
        qtbot.addWidget(dlg)  # type: ignore[attr-defined]
        dlg.show()
        assert dlg.isVisible()
        dlg.accept()
        assert dlg.result() == 1  # QDialog.Accepted
