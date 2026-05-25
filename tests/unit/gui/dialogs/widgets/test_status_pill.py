"""Unit tests for ``mhm_pipeline.gui.dialogs.widgets.status_pill.StatusPill``.

Verifies the raw verdict status → friendly label/glyph/colour mapping
and the ``glyph_only`` constructor mode used in the per-aspect Name /
Type / Role columns of the verdicts table.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui import theme  # noqa: E402
from mhm_pipeline.gui.dialogs.widgets.status_pill import StatusPill  # noqa: E402


@pytest.fixture(autouse=True)
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app


class TestStatusPillRendering:
    def test_full_renders_looks_right_with_success_palette(self) -> None:
        pill = StatusPill("")
        pill.setStatus("full")
        assert pill.text() == "Looks right"
        # The pill stylesheet embeds theme.severity("success") colours.
        success = theme.severity("success")
        assert success.bg in pill.styleSheet()

    def test_fail_renders_got_it_wrong_with_violation_palette(self) -> None:
        pill = StatusPill("")
        pill.setStatus("fail")
        assert pill.text() == "Got it wrong"
        violation = theme.severity("violation")
        assert violation.bg in pill.styleSheet()

    def test_partial_renders_partly_right_with_warning_palette(self) -> None:
        pill = StatusPill("")
        pill.setStatus("partial")
        assert pill.text() == "Partly right"
        warning = theme.severity("warning")
        assert warning.bg in pill.styleSheet()

    def test_unsure_family_all_render_couldnt_tell(self) -> None:
        for status in ("abstain", "unsure", "unknown"):
            pill = StatusPill("")
            pill.setStatus(status)
            assert pill.text() == "Couldn't tell", status
            warning = theme.severity("warning")
            assert warning.bg in pill.styleSheet(), status

    def test_glyph_only_renders_glyph_not_text(self) -> None:
        pill_full = StatusPill("full", glyph_only=True)
        assert pill_full.text() == "✓"
        # Tooltip carries the friendly label so curators can still
        # discover the meaning on hover.
        assert pill_full.toolTip() == "Looks right"

        pill_fail = StatusPill("fail", glyph_only=True)
        assert pill_fail.text() == "✗"
        assert pill_fail.toolTip() == "Got it wrong"

        pill_abstain = StatusPill("abstain", glyph_only=True)
        assert pill_abstain.text() == "—"
        assert pill_abstain.toolTip() == "Couldn't tell"

        pill_error = StatusPill("error", glyph_only=True)
        # "error" maps to the bang glyph in the source palette table.
        assert pill_error.text() == "!"
