"""Tests for the shared ApprovalStore + canonical approval key."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.controller.approval_store import (  # noqa: E402
    APPROVALS_FILENAME,
    ApprovalStore,
    approval_key,
    control_number_of,
    group_for_evaluator,
    normalize_text,
)


@pytest.fixture(autouse=True)
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app


class TestCanonicalKey:
    def test_normalize_strips_punct_casefolds(self) -> None:
        assert normalize_text("Maimonides, Moses") == "maimonides moses"
        assert normalize_text("  Karo;  ") == "karo"

    def test_normalize_preserves_hebrew(self) -> None:
        assert normalize_text("קארו, יוסף") == "קארו יוסף"

    def test_control_number_takes_last_uri_segment(self) -> None:
        assert control_number_of("https://nli/manuscript/990001") == "990001"
        assert control_number_of("990001") == "990001"

    def test_key_is_stable_across_surfaces(self) -> None:
        # Editor row (group ner) and a verdict computing the same key.
        k1 = approval_key("990001", "ner", "author", "Maimonides, Moses")
        k2 = approval_key("https://x/990001", "NER", "AUTHOR", "maimonides   moses")
        assert k1 == k2

    def test_group_for_evaluator(self) -> None:
        assert group_for_evaluator("person_ner") == "ner"
        assert group_for_evaluator("genre_classifier") == "ner"
        assert group_for_evaluator("authority") == "authority"
        assert group_for_evaluator("unknown") == "ner"


class TestApprovalStoreRoundTrip:
    def test_set_and_read(self, tmp_path: Path) -> None:
        store = ApprovalStore(tmp_path)
        k = approval_key("990001", "ner", "AUTHOR", "Karo")
        assert store.is_approved(k) is False
        store.set_approved(k, True, by="ner_editor")
        assert store.is_approved(k) is True
        # persisted to disk
        data = json.loads((tmp_path / APPROVALS_FILENAME).read_text())
        assert data[k]["approved"] is True
        assert data[k]["by"] == "ner_editor"

    def test_reload_from_disk_sees_external_write(self, tmp_path: Path) -> None:
        store = ApprovalStore(tmp_path)
        k = approval_key("990001", "authority", "PLACE", "Safed")
        # Simulate another window writing the file directly.
        (tmp_path / APPROVALS_FILENAME).write_text(
            json.dumps({k: {"approved": True, "by": "eval_agent", "at": "x"}})
        )
        store.load()
        assert store.is_approved(k) is True

    def test_approved_keys(self, tmp_path: Path) -> None:
        store = ApprovalStore(tmp_path)
        a = approval_key("990001", "ner", "AUTHOR", "A")
        b = approval_key("990001", "ner", "AUTHOR", "B")
        store.set_approved(a, True)
        store.set_approved(b, False)
        assert store.approved_keys() == {a}

    def test_bulk_set_single_write(self, tmp_path: Path) -> None:
        store = ApprovalStore(tmp_path)
        keys = {approval_key("990001", "ner", "AUTHOR", str(i)): True for i in range(5)}
        store.bulk_set(keys, by="eval_agent")
        assert len(store.approved_keys()) == 5

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        store = ApprovalStore(tmp_path / "does_not_exist_dir")
        assert store.approved_keys() == set()

    def test_corrupt_file_tolerated(self, tmp_path: Path) -> None:
        (tmp_path / APPROVALS_FILENAME).write_text("{not json")
        store = ApprovalStore(tmp_path)
        assert store.approved_keys() == set()

    def test_flat_bool_shape_tolerated(self, tmp_path: Path) -> None:
        k = approval_key("990001", "ner", "AUTHOR", "X")
        (tmp_path / APPROVALS_FILENAME).write_text(json.dumps({k: True}))
        store = ApprovalStore(tmp_path)
        assert store.is_approved(k) is True

    def test_no_op_when_unchanged_keeps_one_entry(self, tmp_path: Path) -> None:
        store = ApprovalStore(tmp_path)
        k = approval_key("990001", "ner", "AUTHOR", "Karo")
        store.set_approved(k, True)
        store.set_approved(k, True)  # no-op
        data = json.loads((tmp_path / APPROVALS_FILENAME).read_text())
        assert list(data.keys()) == [k]
