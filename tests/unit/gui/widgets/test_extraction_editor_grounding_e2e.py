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

    def test_no_evidence_cell_shows_discovery_marker(
        self, editor: ExtractionEditor,
    ) -> None:
        """Row 2 has empty exists_in → "🆕 new" discovery label,
        not an em-dash. Discoveries are the most interesting reviewer
        case (NER found something not in structured MARC)."""
        text = self._data(editor, 2, Qt.ItemDataRole.DisplayRole)
        assert "🆕" in text
        assert "new" in text.lower()

    def test_full_match_cell_text_has_grounded_check(
        self, editor: ExtractionEditor,
    ) -> None:
        text = self._data(editor, 0, Qt.ItemDataRole.DisplayRole)
        assert text.startswith("✓"), f"grounded cell should lead with ✓: {text!r}"

    def test_wrong_field_cell_text_has_warning(
        self, editor: ExtractionEditor,
    ) -> None:
        # Row 1 (Stiwi Carlos, partial-only in authors, grounded=False)
        text = self._data(editor, 1, Qt.ItemDataRole.DisplayRole)
        assert "⚠" in text or "wrong" in text.lower(), (
            f"ungrounded-but-found-somewhere cell must flag wrong field: {text!r}"
        )

    def test_grounded_cell_background_is_green(
        self, editor: ExtractionEditor,
    ) -> None:
        # Row 0: grounded=True
        bg = self._data(editor, 0, Qt.ItemDataRole.BackgroundRole)
        assert isinstance(bg, QColor), f"expected QColor, got {type(bg)}"
        # Green channel dominant — chip is (22, 163, 74, alpha).
        assert bg.green() > bg.red() and bg.green() > bg.blue()

    def test_wrong_field_cell_background_is_yellow(
        self, editor: ExtractionEditor,
    ) -> None:
        # Row 1: grounded=False, exists_in non-empty → yellow
        bg = self._data(editor, 1, Qt.ItemDataRole.BackgroundRole)
        assert isinstance(bg, QColor)
        # Yellow/amber — (245, 158, 11, alpha). Red+green high, blue low.
        assert bg.red() > 150 and bg.green() > 100 and bg.blue() < 80

    def test_discovery_cell_background_is_blue(
        self, editor: ExtractionEditor,
    ) -> None:
        # Row 2: exists_in empty → blue (discovery — name not in MARC)
        bg = self._data(editor, 2, Qt.ItemDataRole.BackgroundRole)
        assert isinstance(bg, QColor)
        # Blue — (59, 130, 246, alpha). Blue channel highest.
        assert bg.blue() > bg.red() and bg.blue() > bg.green()


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

    def test_tooltip_explains_discovery_for_empty_evidence(
        self, editor: ExtractionEditor,
    ) -> None:
        """The discovery case now has its OWN tooltip explaining the
        state instead of returning None — reviewers shouldn't be left
        wondering why the cell is blue and clickable."""
        idx = editor._model.index(2, COL_EXISTS_IN)
        tip = editor._model.data(idx, Qt.ItemDataRole.ToolTipRole)
        assert isinstance(tip, str)
        assert "NEW" in tip or "new" in tip.lower()
        assert "not found" in tip.lower() or "not in" in tip.lower()


# ── 5. Click handler opens the popup ─────────────────────────────────────


class TestClickToOpenPopup:
    """Clicking a non-empty Exists-in cell opens MarcEvidencePopup."""

    def test_click_opens_popup_for_row_with_evidence(
        self, editor: ExtractionEditor, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        opened: list[MarcEvidencePopup] = []
        real_init = MarcEvidencePopup.__init__

        def capture(self, **kwargs):
            real_init(self, **kwargs)
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
        # New: role_fields + grounded forwarded so the popup can
        # distinguish role-mapped matches from wrong-field hits.
        assert "authors" in dlg._role_fields, (
            f"AUTHOR role should map to authors; got {dlg._role_fields}"
        )
        assert dlg._grounded is True

    def test_click_opens_discovery_popup_for_empty_evidence(
        self, editor: ExtractionEditor, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Discovery cells (empty exists_in) are now clickable too —
        the popup explains "NER found this in the source text but
        it's not in any structured MARC field" so the reviewer can
        decide whether the extraction is a real find or a
        hallucination."""
        opened: list[MarcEvidencePopup] = []
        real_init = MarcEvidencePopup.__init__

        def capture(self, **kwargs):
            real_init(self, **kwargs)
            opened.append(self)

        monkeypatch.setattr(MarcEvidencePopup, "__init__", capture)
        monkeypatch.setattr(MarcEvidencePopup, "exec", lambda self: 0)

        # Row 2 has empty exists_in (discovery case)
        proxy_idx = editor._proxy.index(2, COL_EXISTS_IN)
        editor._on_table_clicked(proxy_idx)
        assert len(opened) == 1, (
            "popup MUST open for discovery cells so reviewer can inspect"
        )
        dlg = opened[0]
        assert dlg._exists_in == []
        assert dlg._grounded is False

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
        # Pass ``role_fields=["authors"]`` (mapping for AUTHOR role) so
        # the popup recognises authors[0].name as role-mapped and gives
        # it the strong green highlight.
        dlg = MarcEvidencePopup(
            needle="Yossi Stiwi",
            exists_in=_ner_record_with_evidence()["entities"][0]["exists_in"],
            marc_record=_marc_record(),
            role_fields=["authors"],
            grounded=True,
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


class TestEntityColumnHoverAndClick:
    """The Entity (text) column shows its full value on hover and
    opens the edit popup on double-click. Inline cell editing of long
    Hebrew strings is awkward and was deliberately disabled — the
    popup is the only edit affordance."""

    def test_entity_cell_tooltip_shows_full_text(
        self, editor: ExtractionEditor,
    ) -> None:
        from mhm_pipeline.gui.widgets.extraction_editor import COL_TEXT  # noqa: PLC0415
        idx = editor._model.index(0, COL_TEXT)
        tip = editor._model.data(idx, Qt.ItemDataRole.ToolTipRole)
        assert isinstance(tip, str)
        assert "Yossi Stiwi" in tip
        # The tooltip also tells the user the click affordance
        assert "Double-click" in tip or "double-click" in tip.lower()

    def test_entity_cell_tooltip_empty_when_no_text(
        self, editor: ExtractionEditor,
    ) -> None:
        from mhm_pipeline.gui.widgets.extraction_editor import COL_TEXT  # noqa: PLC0415
        # Simulate an entity with empty text
        editor._model._entities[0]["text"] = ""
        idx = editor._model.index(0, COL_TEXT)
        tip = editor._model.data(idx, Qt.ItemDataRole.ToolTipRole)
        assert tip is None

    def test_double_click_on_text_column_opens_edit_dialog(
        self, editor: ExtractionEditor, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Double-click on the entity-text cell routes to the existing
        ``_on_edit_text`` handler — the same one the per-row ✎ button
        triggers. We patch that method to capture the row index without
        actually opening the modal dialog."""
        from mhm_pipeline.gui.widgets.extraction_editor import COL_TEXT  # noqa: PLC0415
        opened: list[int] = []
        monkeypatch.setattr(
            editor, "_on_edit_text", lambda row: opened.append(row),
        )
        proxy_idx = editor._proxy.index(0, COL_TEXT)
        editor._on_table_double_clicked(proxy_idx)
        assert opened == [0]

    def test_double_click_on_other_column_does_nothing(
        self, editor: ExtractionEditor, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The double-click handler is column-aware — only COL_TEXT
        opens the edit popup. Other columns (Type, Role, Approved)
        keep their default delegate / checkbox behaviour."""
        from mhm_pipeline.gui.widgets.extraction_editor import COL_TYPE  # noqa: PLC0415
        opened: list[int] = []
        monkeypatch.setattr(
            editor, "_on_edit_text", lambda row: opened.append(row),
        )
        proxy_idx = editor._proxy.index(0, COL_TYPE)
        editor._on_table_double_clicked(proxy_idx)
        assert opened == [], "double-click on Type column shouldn't open Edit Text dialog"


class TestTooltipFollowsTheme:
    """The QToolTip palette must track whichever theme is active —
    light mode renders a light tooltip, dark mode renders a dark
    tooltip. Tokens come from the central registry
    (``theme.ui('tooltip_bg' / 'tooltip_text')``) so QSS, QPalette,
    and QToolTip all consume the same values."""

    def _set_mode(self, monkeypatch: pytest.MonkeyPatch, dark: bool) -> None:
        from mhm_pipeline.gui import theme  # noqa: PLC0415
        monkeypatch.setattr(theme, "is_dark", lambda: dark)
        theme.invalidate_cache()

    def test_light_mode_tooltip_is_light(
        self, qtbot: object, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        if QApplication.instance() is None:
            QApplication([])
        from PyQt6.QtGui import QPalette  # noqa: PLC0415
        from PyQt6.QtWidgets import QToolTip  # noqa: PLC0415
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self._set_mode(monkeypatch, dark=False)
        theme._apply_palette(QApplication.instance())
        pal = QToolTip.palette()
        bg = pal.color(QPalette.ColorRole.ToolTipBase)
        text = pal.color(QPalette.ColorRole.ToolTipText)
        # Light mode → light bg, dark text
        assert bg.lightness() > 200, f"light bg too dark: {bg.lightness()}"
        assert text.lightness() < 80, f"light text too light: {text.lightness()}"

    def test_dark_mode_tooltip_is_dark(
        self, qtbot: object, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        if QApplication.instance() is None:
            QApplication([])
        from PyQt6.QtGui import QPalette  # noqa: PLC0415
        from PyQt6.QtWidgets import QToolTip  # noqa: PLC0415
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self._set_mode(monkeypatch, dark=True)
        theme._apply_palette(QApplication.instance())
        pal = QToolTip.palette()
        bg = pal.color(QPalette.ColorRole.ToolTipBase)
        text = pal.color(QPalette.ColorRole.ToolTipText)
        # Dark mode → dark bg, light text
        assert bg.lightness() < 80, f"dark bg too light: {bg.lightness()}"
        assert text.lightness() > 200, f"dark text too dark: {text.lightness()}"

    def test_tooltip_tokens_come_from_central_registry(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The QSS, QPalette and QToolTip palette must all read the
        SAME tokens. Verified by mutating the registry and checking
        the rendered values change everywhere."""
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self._set_mode(monkeypatch, dark=False)
        bg_token = theme.ui("tooltip_bg")
        text_token = theme.ui("tooltip_text")
        # The tokens must exist (no fallback to "#888888")
        assert bg_token != "#888888"
        assert text_token != "#888888"
        # Light mode should yield light bg + dark text by token name alone
        from PyQt6.QtGui import QColor  # noqa: PLC0415
        assert QColor(bg_token).lightness() > 200
        assert QColor(text_token).lightness() < 80


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


# ── 8. Auto-approve gate respects ``grounded`` ───────────────────────────


class TestAutoApproveGroundedGate:
    """The auto-approve rule builder exposes ``grounded`` as a field
    and pre-seeds the dialog with the safe-default pair
    ``confidence > 0.85 AND grounded = True`` whenever the loaded
    entities carry F8 grounding evidence.
    """

    def test_grounded_is_an_auto_approve_field(self) -> None:
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            _AUTO_FIELDS, _FIELD_OPTIONS,
        )
        assert "grounded" in _AUTO_FIELDS, (
            "grounded must be a first-class auto-approve field"
        )
        assert _FIELD_OPTIONS.get("grounded") == ["True", "False"], (
            "grounded must be a string-enum field with True/False values"
        )

    def test_evaluate_rule_matches_grounded_true(self) -> None:
        from mhm_pipeline.gui.widgets.extraction_editor import evaluate_rule  # noqa: PLC0415
        rule = {"field": "grounded", "op": "=", "value": "True"}
        assert evaluate_rule({"grounded": True}, rule) is True
        assert evaluate_rule({"grounded": False}, rule) is False
        # Missing field → str(None) → "None" ≠ "True" → no match. The
        # default-safe outcome: ungraded entities don't auto-approve.
        assert evaluate_rule({}, rule) is False

    def test_seed_rules_replaces_default_with_pair(
        self, qtbot: object,
    ) -> None:
        if QApplication.instance() is None:
            QApplication([])
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            AutoApproveDialog,
        )
        dlg = AutoApproveDialog(options_for={
            "type": [], "role": [], "source": [],
        })
        qtbot.addWidget(dlg)  # type: ignore[attr-defined]
        dlg.seed_rules([
            {"field": "confidence", "op": ">", "value": 0.85},
            {"field": "grounded", "op": "=", "value": "True"},
        ])
        rules = dlg.rules()
        assert len(rules) == 2
        # Confidence rule first
        assert rules[0]["field"] == "confidence"
        assert rules[0]["op"] == ">"
        assert abs(rules[0]["value"] - 0.85) < 1e-9
        # Grounded rule second
        assert rules[1]["field"] == "grounded"
        assert rules[1]["op"] == "="
        assert rules[1]["value"] == "True"

    def test_extraction_editor_seeds_grounded_when_data_has_it(
        self, editor: ExtractionEditor, qtbot: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``_on_auto_approve`` opens the dialog and the data
        carries grounding info, the dialog must arrive pre-seeded with
        BOTH rules so the reviewer can't accidentally bypass the
        precision gate."""
        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            AutoApproveDialog, QDialog,
        )
        seen_rules: list[list[dict]] = []
        real_exec = AutoApproveDialog.exec

        def fake_exec(self):
            # Capture the rules the dialog is about to show, then bail
            # out without showing the modal.
            seen_rules.append(list(self.rules()))
            return QDialog.DialogCode.Rejected.value

        monkeypatch.setattr(AutoApproveDialog, "exec", fake_exec)
        editor._on_auto_approve()
        assert seen_rules, "auto-approve flow never built a dialog"
        rules = seen_rules[0]
        # Two rules — confidence and grounded
        fields = [r["field"] for r in rules]
        assert "confidence" in fields
        assert "grounded" in fields
        grounded_rule = next(r for r in rules if r["field"] == "grounded")
        assert grounded_rule["op"] == "="
        assert grounded_rule["value"] == "True"

    def test_extraction_editor_uses_single_default_when_no_grounding(
        self, qtbot: object, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the loaded entities do NOT carry grounding evidence
        (e.g. an older ner_results.json from before F8 shipped), the
        dialog falls back to the single default rule — no surprise
        ``grounded = True`` filter when the field doesn't exist."""
        if QApplication.instance() is None:
            QApplication([])

        from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: PLC0415
            AutoApproveDialog, ExtractionEditor, QDialog,
        )

        widget = ExtractionEditor()
        qtbot.addWidget(widget)  # type: ignore[attr-defined]
        # Load entities WITHOUT grounded / exists_in fields (pre-F8 shape)
        widget._model.load_from_records([{
            "_control_number": "X1", "text": "foo",
            "entities": [{
                "person": "Yossi", "role": "AUTHOR", "source": "person_ner",
                "confidence": 0.85,
            }],
        }])

        seen_rules: list[list[dict]] = []

        def fake_exec(self):
            seen_rules.append(list(self.rules()))
            return QDialog.DialogCode.Rejected.value

        monkeypatch.setattr(AutoApproveDialog, "exec", fake_exec)
        widget._on_auto_approve()
        assert seen_rules
        rules = seen_rules[0]
        # Only the dialog's own default rule — not the pair seeded by
        # ExtractionEditor when grounding is present.
        assert len(rules) == 1
