"""Unit tests for the shared approval-store wiring in both editors.

Both the NER editor (``EditableEntityModel``) and the authority editor
(``AuthorityMatchModel``) read AND write the same ``approvals.json``
sidecar via :class:`mhm_pipeline.controller.approval_store.ApprovalStore`,
so a tick in one surface (or in the eval-agent verdict UI) shows up in the
other live.

The tests drive the models directly — toggling ``COL_APPROVED`` through
``setData`` and inspecting the store — so no full editor widget or event
loop is required beyond a QApplication for the theme calls.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402

from mhm_pipeline.controller.approval_store import (  # noqa: E402
    ApprovalStore,
    approval_key,
)
from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: E402
    COL_APPROVED as AUTH_APPROVED,
)
from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: E402
    AuthorityMatchModel,
)
from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: E402
    COL_APPROVED as NER_APPROVED,
)
from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: E402
    EditableEntityModel,
)


@pytest.fixture(autouse=True)
def _qapp_offscreen() -> None:
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

    app = QApplication.instance() or QApplication([])
    yield
    del app


# ── Fixtures: minimal record shapes ─────────────────────────────────────────


def _ner_records() -> list[dict]:
    return [
        {
            "_control_number": "990001",
            "text": "context",
            "entities": [
                {
                    "text": "משה בן מימון",
                    "type": "PERSON",
                    "role": "AUTHOR",
                    "source": "person_ner",
                    "confidence": 0.85,
                    "model_confidence": 0.9,
                    "start": 0,
                    "end": 12,
                },
            ],
        },
    ]


def _authority_records() -> list[dict]:
    return [
        {
            "_control_number": "990001",
            "marc_authority_matches": [
                {
                    "name": "משה בן מימון",
                    "role": "author",
                    "field": "100",
                    "mazal_id": "987001",
                    "confidence": "high",
                    "preferred_name_lat": "Maimonides",
                },
            ],
        },
    ]


def _ner_model(tmp_path: Path) -> EditableEntityModel:
    model = EditableEntityModel()
    model.attach_approval_store(tmp_path)
    model.load_from_records(_ner_records())
    return model


def _auth_model(tmp_path: Path) -> AuthorityMatchModel:
    model = AuthorityMatchModel()
    model.attach_approval_store(tmp_path)
    model.load(_authority_records())
    return model


# ── 1. NER editor write + load round-trip ───────────────────────────────────


class TestNerEditorApprovalSync:
    def test_toggle_writes_canonical_key_to_store(self, tmp_path: Path) -> None:
        model = _ner_model(tmp_path)
        idx = model.index(0, NER_APPROVED)
        model.setData(idx, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)

        key = approval_key("990001", "ner", "AUTHOR", "משה בן מימון")
        store = ApprovalStore(tmp_path)
        assert store.is_approved(key)

    def test_load_reflects_pre_approved_key(self, tmp_path: Path) -> None:
        key = approval_key("990001", "ner", "AUTHOR", "משה בן מימון")
        seed = ApprovalStore(tmp_path)
        seed.set_approved(key, True, by="eval_agent")

        model = _ner_model(tmp_path)
        assert model._entities[0]["approved"] is True

    def test_unticking_persists_false(self, tmp_path: Path) -> None:
        key = approval_key("990001", "ner", "AUTHOR", "משה בן מימון")
        ApprovalStore(tmp_path).set_approved(key, True, by="eval_agent")

        model = _ner_model(tmp_path)
        idx = model.index(0, NER_APPROVED)
        model.setData(idx, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)

        assert ApprovalStore(tmp_path).is_approved(key) is False


# ── 2. Authority editor write + load round-trip ─────────────────────────────


class TestAuthorityEditorApprovalSync:
    def test_toggle_writes_canonical_key_to_store(self, tmp_path: Path) -> None:
        model = _auth_model(tmp_path)
        idx = model.index(0, AUTH_APPROVED)
        model.setData(idx, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)

        key = approval_key("990001", "authority", "author", "משה בן מימון")
        store = ApprovalStore(tmp_path)
        assert store.is_approved(key)

    def test_load_reflects_pre_approved_key(self, tmp_path: Path) -> None:
        key = approval_key("990001", "authority", "author", "משה בן מימון")
        seed = ApprovalStore(tmp_path)
        seed.set_approved(key, True, by="eval_agent")

        model = _auth_model(tmp_path)
        assert model._rows[0]["approved"] is True


# ── 3. Live cross-window reload ──────────────────────────────────────────────


class TestExternalReloadFlipsRow:
    def test_ner_reload_flips_approved(self, tmp_path: Path) -> None:
        model = _ner_model(tmp_path)
        assert model._entities[0]["approved"] is False

        # A second store instance (the eval-agent UI) on the same dir.
        external = ApprovalStore(tmp_path)
        external.set_approved(
            approval_key("990001", "ner", "AUTHOR", "משה בן מימון"),
            True,
            by="eval_agent",
        )
        # Force the editor's store to see the external write, then reload.
        model._approval_store.load()
        model._reload_approvals_from_store()

        assert model._entities[0]["approved"] is True

    def test_authority_reload_flips_approved(self, tmp_path: Path) -> None:
        model = _auth_model(tmp_path)
        assert model._rows[0]["approved"] is False

        external = ApprovalStore(tmp_path)
        external.set_approved(
            approval_key("990001", "authority", "author", "משה בן מימון"),
            True,
            by="eval_agent",
        )
        model._approval_store.load()
        model._reload_approvals_from_store()

        assert model._rows[0]["approved"] is True


# ── 4. Canonical key contract ────────────────────────────────────────────────


class TestCanonicalKey:
    def test_ner_row_key_matches_approval_key_helper(self, tmp_path: Path) -> None:
        model = _ner_model(tmp_path)
        row = model._entities[0]
        expected = approval_key(
            row["_control_number"], "ner", row["role"], row["text"],
        )
        assert model._row_approval_key(row) == expected

    def test_authority_row_key_matches_approval_key_helper(self, tmp_path: Path) -> None:
        model = _auth_model(tmp_path)
        row = model._rows[0]
        expected = approval_key(
            row["_control_number"], "authority", row["role"], row["entity_text"],
        )
        assert model._row_approval_key(row) == expected

    def test_attach_is_idempotent_on_same_dir(self, tmp_path: Path) -> None:
        model = _ner_model(tmp_path)
        store = model._approval_store
        model.attach_approval_store(tmp_path)
        assert model._approval_store is store
