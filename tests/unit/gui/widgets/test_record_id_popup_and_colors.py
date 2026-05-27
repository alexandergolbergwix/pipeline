"""Tests for the Record-id MARC popup click + theme-driven confidence colours.

Covers:
* Clicking the Record (control-number) column in
  :class:`ExtractionEditor` opens the friendly MARC popup with that
  record's control number.
* Clicking the Record column in :class:`AuthorityEditor` opens the
  MARC popup (MARC injected via ``set_marc_records``).
* :func:`_ner_confidence_band` (NER editor) returns theme colours.
* :func:`_confidence_band_label` (authority editor) returns theme
  colours.
* Confidence / dates tooltip HTML carries the theme colour (no
  hardcoded band hex).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui import theme  # noqa: E402
from mhm_pipeline.gui.widgets import authority_editor, extraction_editor  # noqa: E402
from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: E402
    COL_RECORD as AUTH_COL_RECORD,
)
from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: E402
    AuthorityEditor,
    _build_authority_confidence_tooltip,
    _build_authority_dates_tooltip,
    _confidence_band_label,
)
from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: E402
    COL_RECORD as NER_COL_RECORD,
)
from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: E402
    ExtractionEditor,
    _build_ner_keyword_conf_tooltip,
    _ner_confidence_band,
)


@pytest.fixture(autouse=True)
def _qapp_offscreen() -> None:
    app = QApplication.instance() or QApplication([])
    yield
    del app


# ─────────────────────────────────────────────────────────────────────
# 1. Record-id click → MARC popup (extraction editor)
# ─────────────────────────────────────────────────────────────────────


def test_extraction_record_click_opens_marc_popup(monkeypatch: Any) -> None:
    calls: list[tuple[str, Any]] = []

    def _fake_open(control_number: str, marc_record: Any, parent: Any = None) -> object:
        calls.append((control_number, marc_record))
        return object()

    import mhm_pipeline.gui.dialogs.widgets.marc_record_popup as popup_mod
    monkeypatch.setattr(popup_mod, "open_marc_popup", _fake_open)

    editor = ExtractionEditor()
    records = [
        {
            "_control_number": "990001",
            "text": "note text",
            "entities": [
                {"text": "משה", "type": "PERSON", "source": "person_ner",
                 "confidence": 0.85, "model_confidence": 0.9},
            ],
        },
    ]
    editor.load_records(records)
    editor._model.set_marc_records(
        [{"_control_number": "990001", "fields": {"245": "Title"}}],
    )

    proxy_idx = editor._proxy.index(0, NER_COL_RECORD)
    editor._on_table_clicked(proxy_idx)

    assert len(calls) == 1
    assert calls[0][0] == "990001"
    assert calls[0][1] == {"_control_number": "990001", "fields": {"245": "Title"}}


# ─────────────────────────────────────────────────────────────────────
# 2. Record-id click → MARC popup (authority editor)
# ─────────────────────────────────────────────────────────────────────


def test_authority_record_click_opens_marc_popup(monkeypatch: Any) -> None:
    calls: list[tuple[str, Any]] = []

    def _fake_open(control_number: str, marc_record: Any, parent: Any = None) -> object:
        calls.append((control_number, marc_record))
        return object()

    import mhm_pipeline.gui.dialogs.widgets.marc_record_popup as popup_mod
    monkeypatch.setattr(popup_mod, "open_marc_popup", _fake_open)

    editor = AuthorityEditor()
    records = [
        {
            "_control_number": "990002",
            "marc_authority_matches": [
                {"name": "משה בן מימון", "role": "AUTHOR", "field": "100",
                 "mazal_id": "M1", "confidence": "high"},
            ],
        },
    ]
    editor.load_records(records)
    editor.set_marc_records(
        [{"_control_number": "990002", "fields": {"100": "author"}}],
    )

    proxy_idx = editor._proxy.index(0, AUTH_COL_RECORD)
    editor._on_table_clicked(proxy_idx)

    assert len(calls) == 1
    assert calls[0][0] == "990002"
    assert calls[0][1] == {"_control_number": "990002", "fields": {"100": "author"}}


def test_authority_set_marc_records_indexes_by_control_number() -> None:
    editor = AuthorityEditor()
    editor.set_marc_records(
        [
            {"_control_number": "A", "x": 1},
            {"_control_number": "B", "x": 2},
            {"no_cn": True},  # skipped — no control number
        ],
    )
    assert set(editor._marc_by_cn.keys()) == {"A", "B"}


# ─────────────────────────────────────────────────────────────────────
# 3. _ner_confidence_band returns theme colours
# ─────────────────────────────────────────────────────────────────────


def test_ner_confidence_band_uses_theme_colours() -> None:
    assert _ner_confidence_band(0.95) == ("HIGH", theme.ui("success"))
    assert _ner_confidence_band(0.70) == ("MEDIUM", theme.ui("warning"))
    assert _ner_confidence_band(0.30) == ("LOW", theme.ui("error"))
    assert _ner_confidence_band(0.0) == ("UNSCORED", theme.ui("subtext"))


# ─────────────────────────────────────────────────────────────────────
# 4. _confidence_band_label (authority) returns theme colours
# ─────────────────────────────────────────────────────────────────────


def test_authority_confidence_band_label_uses_theme_colours() -> None:
    assert _confidence_band_label(0.95) == ("HIGH", theme.ui("success"))
    assert _confidence_band_label(0.60) == ("MEDIUM", theme.ui("warning"))
    assert _confidence_band_label(0.10) == ("LOW", theme.ui("error"))
    # bucket-hint path
    assert _confidence_band_label(0.0, "high") == ("HIGH", theme.ui("success"))
    assert _confidence_band_label(0.0, "low") == ("LOW", theme.ui("error"))


# ─────────────────────────────────────────────────────────────────────
# 5. Tooltip HTML carries the theme colour (no stray band hex)
# ─────────────────────────────────────────────────────────────────────


def test_authority_confidence_tooltip_uses_theme_colour() -> None:
    row = {
        "confidence": 0.95,
        "_confidence_bucket": "high",
        "_auth_kind": "mazal",
        "matched_id": "M1",
        "_sources": ["mazal"],
        "_source_count": 1,
        "_guard_flags": [],
        "wikidata_qid": "",
    }
    html = _build_authority_confidence_tooltip(row)
    assert theme.ui("success") in html
    # The retired band hex must not leak back in.
    assert "#16a34a" not in html
    assert "#dc2626" not in html
    assert "#d97706" not in html


def test_authority_dates_tooltip_uses_theme_colour() -> None:
    row = {
        "_auth_kind": "mazal",
        "role": "author",
        "_ms_year": 1650,
        "_birth_year": 1138,
        "_death_year": 1204,
        "_guard_flags": [],
    }
    html = _build_authority_dates_tooltip(row)
    assert theme.ui("success") in html
    assert "#16a34a" not in html

    conflict_row = dict(row, _guard_flags=["date_conflict"])
    conflict_html = _build_authority_dates_tooltip(conflict_row)
    assert theme.ui("error") in conflict_html
    assert "#dc2626" not in conflict_html


def test_ner_keyword_conf_tooltip_uses_theme_colour() -> None:
    ent = {
        "confidence": 0.85,
        "model_confidence": 0.9,
        "type": "PERSON",
        "role": "AUTHOR",
        "source": "person_ner",
    }
    html = _build_ner_keyword_conf_tooltip(ent)
    assert theme.ui("success") in html
    assert "#16a34a" not in html
