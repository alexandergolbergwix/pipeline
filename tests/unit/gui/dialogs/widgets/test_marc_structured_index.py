"""Tests for ``MarcStructuredIndex`` — novelty detection (Rule 52)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mhm_pipeline.gui.dialogs.widgets.marc_structured_index import (
    MarcStructuredIndex,
    _normalise,
    _record_key,
)


class TestNormalise:
    def test_casefold_and_punctuation(self) -> None:
        assert _normalise("Maimonides,") == "maimonides"
        assert _normalise("  Yosef ben Efrayim  ") == "yosef ben efrayim"

    def test_hebrew_preserved(self) -> None:
        assert _normalise("בירב, יעקב") == "בירב יעקב"

    def test_empty_inputs(self) -> None:
        assert _normalise("") == ""
        assert _normalise("   ") == ""


class TestRecordKey:
    def test_uri_last_segment(self) -> None:
        assert _record_key("https://example/manuscript/990001") == "990001"
        assert _record_key("990001") == "990001"
        assert _record_key("") == ""


class TestIndexLoad:
    def _write(self, path: Path, records: list[dict]) -> Path:
        out = path / "marc_extracted.json"
        out.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return out

    def test_missing_file_yields_empty_index(self, tmp_path: Path) -> None:
        idx = MarcStructuredIndex.load(tmp_path / "nope.json")
        assert len(idx) == 0
        assert idx.is_novel("anything", "anything") is False  # safe default

    def test_indexes_structured_fields(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            [
                {
                    "_control_number": "990001",
                    "contributors": [{"name": "Maimonides", "role": "author"}],
                    "subjects": [
                        {"term": "Responsa", "type": "topic"},
                    ],
                    "title": "Mishneh Torah",
                    "genre_form": [],
                },
            ],
        )
        idx = MarcStructuredIndex.load(path)
        assert len(idx) == 1
        assert idx.has("990001") is True
        assert idx.has("https://x/manuscript/990001") is True  # URI normalised

    def test_malformed_json_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "marc_extracted.json"
        path.write_text("{not json", encoding="utf-8")
        idx = MarcStructuredIndex.load(path)
        assert len(idx) == 0

    def test_dict_shape_supported(self, tmp_path: Path) -> None:
        path = tmp_path / "marc_extracted.json"
        path.write_text(
            json.dumps(
                {
                    "990001": {
                        "_control_number": "990001",
                        "contributors": [{"name": "Karo"}],
                    }
                }
            ),
            encoding="utf-8",
        )
        idx = MarcStructuredIndex.load(path)
        assert idx.has("990001") is True


class TestIsNovel:
    @pytest.fixture
    def index(self, tmp_path: Path) -> MarcStructuredIndex:
        path = tmp_path / "marc_extracted.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "_control_number": "990001",
                        "contributors": [
                            {"name": "Maimonides, Moses", "role": "author"},
                            {"name": "Karo, Yosef ben Efrayim", "role": "author"},
                        ],
                        "subjects": [{"term": "Responsa", "type": "topic"}],
                        "title": "Mishneh Torah",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return MarcStructuredIndex.load(path)

    def test_known_name_not_novel(self, index: MarcStructuredIndex) -> None:
        # Exact match (with the ISBD comma stripped by normalisation).
        assert index.is_novel("990001", "Maimonides, Moses") is False

    def test_substring_of_known_value_not_novel(
        self, index: MarcStructuredIndex,
    ) -> None:
        # NER might emit just the surname — still already in MARC.
        assert index.is_novel("990001", "Maimonides") is False

    def test_known_value_substring_of_candidate_not_novel(
        self, index: MarcStructuredIndex,
    ) -> None:
        # NER might emit a fuller form that contains the MARC value.
        assert index.is_novel("990001", "Moses Maimonides ben Maimon") is False

    def test_truly_novel_name(self, index: MarcStructuredIndex) -> None:
        assert index.is_novel("990001", "Some Other Scribe") is True

    def test_unknown_record_safe_default_false(
        self, index: MarcStructuredIndex,
    ) -> None:
        # Can't prove novelty without a reference → False (safer than True).
        assert index.is_novel("999999", "anything") is False

    def test_empty_candidate_not_novel(self, index: MarcStructuredIndex) -> None:
        assert index.is_novel("990001", "") is False

    def test_uri_record_id_resolves(self, index: MarcStructuredIndex) -> None:
        assert (
            index.is_novel("https://nli/manuscript/990001", "Some Other Scribe")
            is True
        )
