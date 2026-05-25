"""End-to-end UI tests for Rule 50 main-window wiring.

* Sidebar label for Stage 2 reads "AI-based Enrichment".
* Stage-progress success message for Stage 2 reads
  "AI-based enrichment complete".
* Settings menu contains a "Credentials…" action.
* The NER panel exposes a "Verify with AI agent" button + a
  ``verify_requested`` pyqtSignal.
* Clicking the Verify button when no Gemini key is set opens the
  Credentials dialog (mocked) instead of calling the controller.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtGui import QAction  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.settings import credential_store  # noqa: E402


class _StubKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str) -> str | None:
        return self.store.get((service, user))

    def set_password(self, service: str, user: str, value: str) -> None:
        self.store[(service, user)] = value

    def delete_password(self, service: str, user: str) -> None:
        self.store.pop((service, user), None)


@pytest.fixture
def stub_keyring(monkeypatch: pytest.MonkeyPatch) -> _StubKeyring:
    stub = _StubKeyring()
    monkeypatch.setattr(credential_store, "_import_keyring", lambda: stub)
    return stub


@pytest.fixture(autouse=True)
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def main_window(stub_keyring: _StubKeyring) -> object:
    from mhm_pipeline.controller.pipeline_controller import PipelineController
    from mhm_pipeline.gui.main_window import MainWindow
    from mhm_pipeline.settings.settings_manager import SettingsManager

    settings = SettingsManager()
    window = MainWindow(settings, PipelineController(SettingsManager()))
    window.show()
    return window


class TestStageRename:
    def test_sidebar_stage_2_says_ai_based_enrichment(
        self, main_window: object
    ) -> None:
        from mhm_pipeline.gui.main_window import _STAGE_LABELS

        assert _STAGE_LABELS[1] == "AI-based Enrichment"

    def test_stage_2_progress_label_renamed(self) -> None:
        from mhm_pipeline.gui.main_window import MainWindow

        labels = MainWindow._STAGE_PROGRESS_LABELS
        assert labels[1][0] == "AI-based enrichment complete"
        assert labels[1][1] == "AI-based enrichment failed"
        # The historical "NER extraction" wording is gone.
        assert "NER extraction" not in labels[1][0]
        assert "NER extraction" not in labels[1][1]


class TestSettingsMenuCredentialsAction:
    def test_credentials_menu_item_present(
        self, main_window: object
    ) -> None:
        actions: list[QAction] = []
        for menu in main_window.menuBar().findChildren(type(QAction())):
            actions.append(menu)
        # Walk the Settings menu specifically.
        settings_menu = None
        for action in main_window.menuBar().actions():
            if action.text() in {"&Settings", "Settings"}:
                settings_menu = action.menu()
                break
        assert settings_menu is not None, "Settings menu missing"
        texts = {a.text() for a in settings_menu.actions()}
        assert "&Credentials…" in texts


class TestNerPanelVerifyButton:
    def test_verify_with_ai_button_exists(
        self, main_window: object
    ) -> None:
        from PyQt6.QtWidgets import QPushButton

        from mhm_pipeline.gui.panels.ner_panel import NerPanel

        ner_panel = main_window.findChild(NerPanel)
        assert ner_panel is not None
        buttons = ner_panel.findChildren(QPushButton)
        labels = {b.text() for b in buttons}
        assert "Verify with AI agent" in labels

    def test_verify_button_starts_disabled_with_no_results(
        self, main_window: object
    ) -> None:
        from mhm_pipeline.gui.panels.ner_panel import NerPanel

        ner_panel = main_window.findChild(NerPanel)
        verify_btn = getattr(ner_panel, "_verify_btn", None)
        assert verify_btn is not None
        # No output dir → no ner_results.json → button disabled.
        assert verify_btn.isEnabled() is False

    def test_verify_requested_signal_exists(
        self, main_window: object
    ) -> None:
        from mhm_pipeline.gui.panels.ner_panel import NerPanel

        ner_panel = main_window.findChild(NerPanel)
        assert ner_panel is not None
        # Signal is declared on the class.
        assert hasattr(NerPanel, "verify_requested")


class TestOnVerifyWithAiSlot:
    def test_missing_gemini_key_opens_credentials_dialog(
        self,
        main_window: object,
        tmp_path: object,
        stub_keyring: _StubKeyring,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the user fires Verify without a key, the slot opens the
        Credentials dialog instead of calling the controller."""
        # Patch the dialog so we just observe whether it gets opened.
        from mhm_pipeline.gui.dialogs import credentials_dialog as cd_module

        captured: dict[str, object] = {}

        class _SpyDialog:
            def __init__(self, settings: object, parent: object | None = None) -> None:
                captured["constructed"] = True

            def exec(self) -> int:
                return 0

        monkeypatch.setattr(cd_module, "CredentialsDialog", _SpyDialog)
        # Patch MessageBox to skip the modal popup.
        from mhm_pipeline.gui import main_window as mw_module
        monkeypatch.setattr(
            mw_module.QMessageBox, "information", lambda *a, **kw: None
        )
        # Patch the controller so the test doesn't actually start a
        # worker when the slot proceeds.
        main_window._controller.start_eval_agent = MagicMock()

        # No key stored → slot should NOT call start_eval_agent.
        main_window._on_verify_with_ai(tmp_path)

        assert captured.get("constructed") is True
        main_window._controller.start_eval_agent.assert_not_called()
