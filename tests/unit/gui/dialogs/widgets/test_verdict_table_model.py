"""Unit tests for ``mhm_pipeline.gui.dialogs.widgets.verdict_table_model``.

Tests the JSONL loader, the default-vs-advanced column visibility flip,
friendly evaluator labelling, ``cache_key``-driven "Reused" derivation,
candidate-text truncation, and the 5000-row hard cap.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QModelIndex, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui.dialogs.widgets.verdict_table_model import (  # noqa: E402
    VerdictTableModel,
)


@pytest.fixture(autouse=True)
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    with path.open("w", encoding="utf-8") as handle:
        for r in records:
            handle.write(json.dumps(r) + "\n")
    return path


def _row_text(model: VerdictTableModel, row: int, col: int) -> str:
    index = model.index(row, col)
    return str(model.data(index, int(Qt.ItemDataRole.DisplayRole)) or "")


def _headers(model: VerdictTableModel) -> list[str]:
    return [
        str(
            model.headerData(
                col, Qt.Orientation.Horizontal, int(Qt.ItemDataRole.DisplayRole),
            ),
        )
        for col in range(model.columnCount())
    ]


class TestVerdictTableModelLoad:
    def test_load_matches_jsonl_line_count(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path / "results.jsonl",
            [
                {"record_id": "990001",  "evaluator_id": "person_ner",
                 "candidate": {"text": "Alpha"}, "verdict": {"overall": "full"}},
                {"record_id": "990002",  "evaluator_id": "person_ner",
                 "candidate": {"text": "Beta"},  "verdict": {"overall": "fail"}},
                {"record_id": "990003",  "evaluator_id": "provenance_ner",
                 "candidate": {"text": "Gamma"}, "verdict": {"overall": "partial"}},
            ],
        )
        model = VerdictTableModel()
        model.load(path)
        assert model.rowCount() == 3
        assert model.total_row_count() == 3


class TestVerdictTableModelColumns:
    def test_default_columns_hide_advanced_set(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path / "r.jsonl",
            [{"record_id": "x", "evaluator_id": "person_ner",
              "candidate": "a", "verdict": {"overall": "full"}}],
        )
        model = VerdictTableModel()
        model.load(path)
        default_headers = _headers(model)

        model.set_advanced(True)
        advanced_headers = _headers(model)

        assert len(advanced_headers) > len(default_headers)
        # Cache key + Confidence + Record ID are advanced-only.
        for advanced_only in ("Cache key", "Confidence", "Record ID"):
            assert advanced_only in advanced_headers
            assert advanced_only not in default_headers

    def test_friendly_evaluator_label_in_checker_column(
        self, tmp_path: Path,
    ) -> None:
        path = _write_jsonl(
            tmp_path / "r.jsonl",
            [
                {"record_id": "x", "evaluator_id": "person_ner",
                 "candidate": "a", "verdict": {"overall": "full"}},
                {"record_id": "y", "evaluator_id": "provenance_ner",
                 "candidate": "b", "verdict": {"overall": "fail"}},
            ],
        )
        model = VerdictTableModel()
        model.load(path)
        headers = _headers(model)
        col = headers.index("Which AI checker")
        assert _row_text(model, 0, col) == "Person AI"
        assert _row_text(model, 1, col) == "Owner AI"


class TestVerdictTableModelReusedDerivation:
    def test_reused_glyph_derived_from_cache_key_presence(
        self, tmp_path: Path,
    ) -> None:
        path = _write_jsonl(
            tmp_path / "r.jsonl",
            [
                {"record_id": "with-key", "evaluator_id": "person_ner",
                 "candidate": "a", "verdict": {"overall": "full"},
                 "cache_key": "sha256:abc123"},
                {"record_id": "no-key",   "evaluator_id": "person_ner",
                 "candidate": "b", "verdict": {"overall": "full"}},
            ],
        )
        model = VerdictTableModel()
        model.load(path)
        col = _headers(model).index("Reused")

        # Row 0 has a cache_key → "Reused" cell is non-empty.
        assert _row_text(model, 0, col).strip() != ""
        # Row 1 has no cache_key → "Reused" cell is empty.
        assert _row_text(model, 1, col).strip() == ""


class TestVerdictTableModelTruncation:
    def test_long_candidate_text_truncated_with_ellipsis(
        self, tmp_path: Path,
    ) -> None:
        long_text = "X" * 200  # well over the 80-char cap
        path = _write_jsonl(
            tmp_path / "r.jsonl",
            [{"record_id": "x", "evaluator_id": "person_ner",
              "candidate": {"text": long_text},
              "verdict": {"overall": "full"}}],
        )
        model = VerdictTableModel()
        model.load(path)
        col = _headers(model).index("What it looked at")
        text = _row_text(model, 0, col)
        # ≤ 80 visible chars + 1 ellipsis glyph.
        assert len(text) <= 81
        assert text.endswith("…")


class TestVerdictTableModelRowCap:
    def test_row_count_capped_at_5000(self, tmp_path: Path) -> None:
        records = [
            {"record_id": f"r{i}", "evaluator_id": "person_ner",
             "candidate": "a", "verdict": {"overall": "full"}}
            for i in range(6000)
        ]
        path = _write_jsonl(tmp_path / "r.jsonl", records)
        model = VerdictTableModel()
        model.load(path)
        assert model.rowCount() == 5000
        # And the "showing N of M" advisory flag fires.
        assert model.is_capped() is True
        assert model.total_row_count() == 6000

    def test_modelindex_outside_visible_range_returns_none(
        self, tmp_path: Path,
    ) -> None:
        # Safety net for the row cap — ``data`` should never raise when
        # asked for an index past ``rowCount``.
        path = _write_jsonl(
            tmp_path / "r.jsonl",
            [{"record_id": "x", "evaluator_id": "person_ner",
              "candidate": "a", "verdict": {"overall": "full"}}],
        )
        model = VerdictTableModel()
        model.load(path)
        bogus = model.createIndex(99, 0)
        assert model.data(bogus, int(Qt.ItemDataRole.DisplayRole)) is None

        # Invalid index — also no crash.
        assert model.data(QModelIndex(), int(Qt.ItemDataRole.DisplayRole)) is None
