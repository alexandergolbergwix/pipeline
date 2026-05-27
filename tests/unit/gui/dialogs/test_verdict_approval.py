"""Approve/Reject on the eval-agent verdict UI, backed by the shared
``ApprovalStore`` so approvals sync live with the editor UIs.

Covers:

1. ``StatusPill`` renders "approved"/"not_approved" with the right
   friendly label + severity palette.
2. ``VerdictTableModel.approve_row`` writes the canonical key into the
   store and the Approved column's StatusRole flips to "approved".
3. The approval key for a ``person_ner`` verdict equals the editor-side
   ``approval_key(record_id, "ner", sub_type, candidate_text)`` — proving
   the cross-surface join.
4. ``filter_approved_only(True)`` hides unapproved rows.
5. The dialog builds an ``ApprovalStore`` from its pipeline output dir;
   an external ``set_approved`` + ``refresh_from_store`` flips the row's
   StatusRole to "approved".
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.controller.approval_store import (  # noqa: E402
    ApprovalStore,
    approval_key,
)
from mhm_pipeline.gui.dialogs.ai_verification_dialog import (  # noqa: E402
    AiVerificationDialog,
)
from mhm_pipeline.gui.dialogs.widgets.status_pill import (  # noqa: E402
    StatusPill,
    _resolve_palette,
)
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


def _approved_col(model: VerdictTableModel) -> int:
    for col in range(model.columnCount()):
        header = model.headerData(
            col, Qt.Orientation.Horizontal, int(Qt.ItemDataRole.DisplayRole),
        )
        if header == "Approved":
            return col
    raise AssertionError("Approved column not present")


def _status(model: VerdictTableModel, row: int, col: int) -> str:
    index = model.index(row, col)
    return str(model.data(index, int(VerdictTableModel.StatusRole)) or "")


# ── 1. StatusPill renders approved/not_approved ─────────────────────


class TestStatusPillApproval:
    def test_approved_palette(self) -> None:
        label, glyph, _bg, _fg = _resolve_palette("approved")
        assert label == "Approved"
        assert glyph == "✓"

    def test_not_approved_palette(self) -> None:
        label, glyph, _bg, _fg = _resolve_palette("not_approved")
        assert label == "Not approved"
        assert glyph == "—"

    def test_pill_widget_sets_status(self) -> None:
        pill = StatusPill("approved")
        assert pill.status() == "approved"
        assert pill.text() == "Approved"


# ── 2. approve_row writes the canonical key + flips StatusRole ──────


class TestApproveRow:
    def test_approve_row_writes_key_and_flips_status(
        self, tmp_path: Path,
    ) -> None:
        path = _write_jsonl(
            tmp_path / "results.jsonl",
            [
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "sub_type": "AUTHOR",
                    "candidate": {"text": "Maimonides"},
                    "verdict": {"overall": "full"},
                }
            ],
        )
        store = ApprovalStore(tmp_path)
        model = VerdictTableModel()
        model.set_approval_store(store)
        model.load(path)

        col = _approved_col(model)
        assert _status(model, 0, col) == "not_approved"

        row = model.raw_row(0)
        assert row is not None
        key = model.approval_key_for(row)

        model.approve_row(0, True)
        assert store.is_approved(key) is True
        assert _status(model, 0, col) == "approved"

    def test_reject_row_clears_approval(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path / "results.jsonl",
            [
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "sub_type": "AUTHOR",
                    "candidate": {"text": "Maimonides"},
                    "verdict": {"overall": "full"},
                }
            ],
        )
        store = ApprovalStore(tmp_path)
        model = VerdictTableModel()
        model.set_approval_store(store)
        model.load(path)
        col = _approved_col(model)

        model.approve_row(0, True)
        assert _status(model, 0, col) == "approved"
        model.approve_row(0, False)
        assert _status(model, 0, col) == "not_approved"


# ── 3. cross-surface key join with the editors ─────────────────────


class TestApprovalKeyCrossSurface:
    def test_person_ner_key_matches_editor_ner_group_key(
        self, tmp_path: Path,
    ) -> None:
        record = {
            "record_id": "990001",
            "evaluator_id": "person_ner",
            "sub_type": "AUTHOR",
            "candidate": {"text": "Maimonides"},
            "verdict": {"overall": "full"},
        }
        model = VerdictTableModel()
        expected = approval_key("990001", "ner", "AUTHOR", "Maimonides")
        assert model.approval_key_for(record) == expected


# ── 4. filter_approved_only hides unapproved rows ──────────────────


class TestApprovedFilter:
    def test_filter_approved_only_hides_unapproved(
        self, tmp_path: Path,
    ) -> None:
        path = _write_jsonl(
            tmp_path / "results.jsonl",
            [
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "sub_type": "AUTHOR",
                    "candidate": {"text": "Alpha"},
                    "verdict": {"overall": "full"},
                },
                {
                    "record_id": "990002",
                    "evaluator_id": "person_ner",
                    "sub_type": "AUTHOR",
                    "candidate": {"text": "Beta"},
                    "verdict": {"overall": "full"},
                },
            ],
        )
        store = ApprovalStore(tmp_path)
        model = VerdictTableModel()
        model.set_approval_store(store)
        model.load(path)
        assert model.rowCount() == 2

        model.approve_row(0, True)
        model.filter_approved_only(True)
        assert model.rowCount() == 1

        col = _approved_col(model)
        assert _status(model, 0, col) == "approved"

    def test_filter_approved_only_off_shows_all(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path / "results.jsonl",
            [
                {
                    "record_id": "990001",
                    "evaluator_id": "person_ner",
                    "sub_type": "AUTHOR",
                    "candidate": {"text": "Alpha"},
                    "verdict": {"overall": "full"},
                },
                {
                    "record_id": "990002",
                    "evaluator_id": "person_ner",
                    "sub_type": "AUTHOR",
                    "candidate": {"text": "Beta"},
                    "verdict": {"overall": "full"},
                },
            ],
        )
        store = ApprovalStore(tmp_path)
        model = VerdictTableModel()
        model.set_approval_store(store)
        model.load(path)
        model.approve_row(0, True)
        model.filter_approved_only(True)
        assert model.rowCount() == 1
        model.filter_approved_only(False)
        assert model.rowCount() == 2


# ── 5. Dialog builds a store + live external sync ──────────────────


def _seed_run_dir(root: Path) -> Path:
    run = root / "run-eval-approval"
    run.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        run / "results.jsonl",
        [
            {
                "record_id": "990001",
                "evaluator_id": "person_ner",
                "sub_type": "AUTHOR",
                "candidate": {"text": "Maimonides"},
                "verdict": {"overall": "full"},
            }
        ],
    )
    (run / "summary.csv").write_text(
        "evaluator_id,candidates_total,full,partial,fail\n"
        "person_ner,1,1,0,0\n",
        encoding="utf-8",
    )
    (run / "report.md").write_text("## Summary\nok\n", encoding="utf-8")
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    return run


class TestDialogApprovalStore:
    def test_dialog_builds_store_from_pipeline_output_dir(
        self, tmp_path: Path,
    ) -> None:
        dialog = AiVerificationDialog(tmp_path, worker=None)
        assert isinstance(dialog._approval_store, ApprovalStore)
        assert dialog._approval_store.path.parent == tmp_path

    def test_external_set_approved_flips_dialog_status(
        self, tmp_path: Path,
    ) -> None:
        run = _seed_run_dir(tmp_path)
        dialog = AiVerificationDialog(tmp_path, worker=None, run_dir=run)
        model = dialog._verdict_model
        col = _approved_col(model)
        assert _status(model, 0, col) == "not_approved"

        row = model.raw_row(0)
        assert row is not None
        key = model.approval_key_for(row)

        # Simulate an editor (or any other surface) approving the same
        # entity, then the watcher firing refresh_from_store().
        dialog._approval_store.set_approved(key, True, by="ner_editor")
        model.refresh_from_store()
        assert _status(model, 0, col) == "approved"
