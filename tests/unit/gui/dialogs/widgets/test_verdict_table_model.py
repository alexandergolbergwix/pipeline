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


class TestNoveltyColumn:
    """The "New info" column lights up only for full-pass verdicts on
    candidate text that's NOT already in the manuscript's structured
    MARC fields."""

    @pytest.fixture
    def marc_extracted(self, tmp_path: Path) -> Path:
        path = tmp_path / "marc_extracted.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "_control_number": "990001",
                        "contributors": [{"name": "Maimonides, Moses", "role": "author"}],
                        "title": "Mishneh Torah",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def _new_info_col(self, model: VerdictTableModel) -> int:
        for col in range(model.columnCount()):
            header = model.headerData(
                col, Qt.Orientation.Horizontal, int(Qt.ItemDataRole.DisplayRole)
            )
            if header == "New info":
                return col
        raise AssertionError("New info column not present")

    def test_full_pass_with_novel_text_renders_pill(
        self, tmp_path: Path, marc_extracted: Path,
    ) -> None:
        from mhm_pipeline.gui.dialogs.widgets.marc_structured_index import (
            MarcStructuredIndex,
        )

        results = _write_jsonl(
            tmp_path / "r.jsonl",
            [
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "candidate": "Some Other Scribe",
                    "verdict": {"overall": "full"},
                }
            ],
        )
        model = VerdictTableModel()
        model.load(results, marc_index=MarcStructuredIndex.load(marc_extracted))
        col = self._new_info_col(model)
        assert _row_text(model, 0, col) == "✨ New"

    def test_full_pass_with_known_text_renders_blank(
        self, tmp_path: Path, marc_extracted: Path,
    ) -> None:
        from mhm_pipeline.gui.dialogs.widgets.marc_structured_index import (
            MarcStructuredIndex,
        )

        results = _write_jsonl(
            tmp_path / "r.jsonl",
            [
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "candidate": "Maimonides",  # substring of MARC value
                    "verdict": {"overall": "full"},
                }
            ],
        )
        model = VerdictTableModel()
        model.load(results, marc_index=MarcStructuredIndex.load(marc_extracted))
        col = self._new_info_col(model)
        assert _row_text(model, 0, col) == ""

    def test_failed_verdict_with_novel_text_renders_blank(
        self, tmp_path: Path, marc_extracted: Path,
    ) -> None:
        """Even if the text is genuinely new, a wrong verdict should not
        get a "New info" badge — it would mislead the curator."""
        from mhm_pipeline.gui.dialogs.widgets.marc_structured_index import (
            MarcStructuredIndex,
        )

        results = _write_jsonl(
            tmp_path / "r.jsonl",
            [
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "candidate": "Some Other Scribe",
                    "verdict": {"overall": "fail"},
                }
            ],
        )
        model = VerdictTableModel()
        model.load(results, marc_index=MarcStructuredIndex.load(marc_extracted))
        col = self._new_info_col(model)
        assert _row_text(model, 0, col) == ""

    def test_load_without_index_keeps_column_blank(
        self, tmp_path: Path,
    ) -> None:
        """No marc_index → column simply renders blank for every row,
        no crash, no false flags."""
        results = _write_jsonl(
            tmp_path / "r.jsonl",
            [
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "candidate": "Anyone",
                    "verdict": {"overall": "full"},
                }
            ],
        )
        model = VerdictTableModel()
        model.load(results)  # No marc_index.
        col = self._new_info_col(model)
        assert _row_text(model, 0, col) == ""

    def test_filter_novel_only(
        self, tmp_path: Path, marc_extracted: Path,
    ) -> None:
        from mhm_pipeline.gui.dialogs.widgets.marc_structured_index import (
            MarcStructuredIndex,
        )

        results = _write_jsonl(
            tmp_path / "r.jsonl",
            [
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "candidate": "Maimonides",
                    "verdict": {"overall": "full"},
                },
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "candidate": "Brand New Scribe",
                    "verdict": {"overall": "full"},
                },
            ],
        )
        model = VerdictTableModel()
        model.load(results, marc_index=MarcStructuredIndex.load(marc_extracted))
        assert model.rowCount() == 2
        model.filter_novel_only(True)
        assert model.rowCount() == 1
        # Confirm it's the novel one.
        cand_col = next(
            c for c in range(model.columnCount())
            if model.headerData(
                c, Qt.Orientation.Horizontal, int(Qt.ItemDataRole.DisplayRole)
            ) == "What it looked at"
        )
        assert _row_text(model, 0, cand_col) == "Brand New Scribe"
