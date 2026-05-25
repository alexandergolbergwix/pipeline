"""E2E tests for the in-app Help & Documentation browser."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui.widgets.help_browser import HelpBrowser  # noqa: E402


@pytest.fixture()
def help_browser(qtbot: object) -> HelpBrowser:
    if QApplication.instance() is None:
        QApplication([])
    dlg = HelpBrowser()
    qtbot.addWidget(dlg)  # type: ignore[attr-defined]
    dlg.show()
    return dlg


class TestHelpBrowser:
    def test_required_topics_present(self) -> None:
        """The exact topic count varies as the help browser grows
        (Rule 50 added "credentials"). Lock the set of topics we
        promise to keep instead — counts are brittle as the doc
        evolves."""
        keys = HelpBrowser.topic_keys()
        for required in ("marc-grounding", "review-and-edit",
                          "auto-approve", "shortcuts", "errors",
                          "credentials"):
            assert required in keys, f"expected '{required}' in {keys}"

    def test_default_opens_on_first_topic(
        self, help_browser: HelpBrowser,
    ) -> None:
        assert help_browser._list.currentRow() == 0
        # The first topic should have markdown content rendered.
        assert help_browser._content.toPlainText().strip()

    def test_initial_topic_navigates_to_that_row(self, qtbot: object) -> None:
        if QApplication.instance() is None:
            QApplication([])
        dlg = HelpBrowser(initial_topic="marc-grounding")
        qtbot.addWidget(dlg)  # type: ignore[attr-defined]
        dlg.show()
        keys = HelpBrowser.topic_keys()
        assert keys[dlg._list.currentRow()] == "marc-grounding"
        # Should contain the three-state content
        text = dlg._content.toPlainText()
        assert "Role-grounded" in text or "role-grounded" in text.lower()
        assert "Wrong field" in text or "wrong field" in text.lower()
        assert "Discovery" in text or "discovery" in text.lower()

    def test_clicking_a_topic_changes_content(
        self, help_browser: HelpBrowser,
    ) -> None:
        first_content = help_browser._content.toPlainText()
        help_browser._list.setCurrentRow(2)  # marc-grounding
        second_content = help_browser._content.toPlainText()
        assert first_content != second_content
        assert "Exists in" in second_content or "grounding" in second_content.lower()

    def test_each_topic_has_non_empty_body(
        self, help_browser: HelpBrowser,
    ) -> None:
        for i in range(help_browser._list.count()):
            help_browser._list.setCurrentRow(i)
            body = help_browser._content.toPlainText().strip()
            assert body, f"topic at row {i} has empty body"
            # Body must be at least a paragraph — sanity check against
            # a future regression where a topic ships empty
            assert len(body) > 80, f"topic at row {i} suspiciously short: {body!r}"

    def test_unknown_initial_topic_falls_back_to_first(
        self, qtbot: object,
    ) -> None:
        if QApplication.instance() is None:
            QApplication([])
        dlg = HelpBrowser(initial_topic="does-not-exist")
        qtbot.addWidget(dlg)  # type: ignore[attr-defined]
        dlg.show()
        assert dlg._list.currentRow() == 0
