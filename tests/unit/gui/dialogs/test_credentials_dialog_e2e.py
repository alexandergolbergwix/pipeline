"""End-to-end UI tests for :class:`CredentialsDialog` (Rule 50).

Drives the real Qt widget against the in-memory keyring stub. Tests:

* Three sections render with the right titles.
* When a value is already stored, the input is EMPTY and the
  placeholder reads ``"stored — type to replace"`` — i.e. no read-back.
* Show/Hide toggle flips the input's echo mode but never reveals a
  stored value (because the input is empty until the user types).
* Save persists typed values to the keychain via SettingsManager.
* Empty input + Save preserves the existing stored value (does NOT
  delete it accidentally).
* Clear button deletes the stored value.
* The saved signal fires with the set of changed credential ids.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui.dialogs.credentials_dialog import (  # noqa: E402
    _STORED_PLACEHOLDER,
    CredentialsDialog,
)
from mhm_pipeline.settings import credential_store  # noqa: E402
from mhm_pipeline.settings.credential_store import (  # noqa: E402
    GEMINI_API_KEY,
    SERVICE_NAME,
    WIKIBASE_CLOUD_BOT_PASSWORD,
    WIKIDATA_TOKEN,
)
from mhm_pipeline.settings.settings_manager import SettingsManager  # noqa: E402


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
def settings(stub_keyring: _StubKeyring) -> SettingsManager:
    """SettingsManager wired against the in-memory keyring."""
    return SettingsManager()


# ── Rendering + no-read-back UX ─────────────────────────────────────


class TestCredentialsDialogRender:
    def test_three_sections_present(self, settings: SettingsManager) -> None:
        dialog = CredentialsDialog(settings)
        assert dialog._gemini_input is not None
        assert dialog._wikidata_input is not None
        assert dialog._wb_password is not None
        # Inputs all start empty regardless of what's stored.
        assert dialog._gemini_input.text() == ""
        assert dialog._wikidata_input.text() == ""
        assert dialog._wb_password.text() == ""

    def test_placeholders_when_nothing_stored(
        self, settings: SettingsManager
    ) -> None:
        dialog = CredentialsDialog(settings)
        # No "stored" placeholder when the keychain is empty.
        assert dialog._gemini_input.placeholderText() != _STORED_PLACEHOLDER
        assert dialog._wikidata_input.placeholderText() != _STORED_PLACEHOLDER
        assert dialog._wb_password.placeholderText() != _STORED_PLACEHOLDER

    def test_placeholders_when_each_stored(
        self, settings: SettingsManager, stub_keyring: _StubKeyring
    ) -> None:
        stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] = "AIzaStored"
        stub_keyring.store[(SERVICE_NAME, WIKIDATA_TOKEN)] = "User@Bot:hex"
        stub_keyring.store[(SERVICE_NAME, WIKIBASE_CLOUD_BOT_PASSWORD)] = "hex123"

        dialog = CredentialsDialog(settings)
        assert dialog._gemini_input.placeholderText() == _STORED_PLACEHOLDER
        assert dialog._wikidata_input.placeholderText() == _STORED_PLACEHOLDER
        assert dialog._wb_password.placeholderText() == _STORED_PLACEHOLDER

    def test_input_never_contains_stored_value(
        self, settings: SettingsManager, stub_keyring: _StubKeyring
    ) -> None:
        """The cardinal no-read-back invariant."""
        stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] = "AIzaSECRET"
        dialog = CredentialsDialog(settings)
        # The dialog must NOT populate the input with the stored secret.
        assert "AIzaSECRET" not in dialog._gemini_input.text()
        assert dialog._gemini_input.text() == ""


class TestShowHideToggle:
    def test_show_toggle_flips_echo_mode(
        self, settings: SettingsManager
    ) -> None:
        dialog = CredentialsDialog(settings)
        from PyQt6.QtWidgets import QLineEdit

        assert dialog._gemini_input.echoMode() == QLineEdit.EchoMode.Password
        dialog._gemini_show.setChecked(True)
        assert dialog._gemini_input.echoMode() == QLineEdit.EchoMode.Normal
        assert dialog._gemini_show.text() == "Hide"
        dialog._gemini_show.setChecked(False)
        assert dialog._gemini_input.echoMode() == QLineEdit.EchoMode.Password
        assert dialog._gemini_show.text() == "Show"

    def test_show_toggle_never_reveals_a_stored_value(
        self, settings: SettingsManager, stub_keyring: _StubKeyring
    ) -> None:
        stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] = "AIzaSECRET"
        dialog = CredentialsDialog(settings)
        # Even with echo set to Normal, the input is empty — there's
        # nothing to reveal.
        dialog._gemini_show.setChecked(True)
        assert dialog._gemini_input.text() == ""
        assert "AIzaSECRET" not in dialog._gemini_input.text()


# ── AI-verification model selection (Rule 52 model-id hardening) ────


class TestModelSuggestions:
    def test_suggestions_are_all_valid_ids(self) -> None:
        from mhm_pipeline.gui.dialogs import credentials_dialog as cd
        # The bare (suffix-less) ids 404 on the API — never suggest them.
        assert "gemini-3-pro" not in cd._MODEL_SUGGESTIONS
        assert "gemini-3-flash" not in cd._MODEL_SUGGESTIONS
        # The user explicitly rejected the gemini-2.5 family.
        assert not any(m.startswith("gemini-2.5") for m in cd._MODEL_SUGGESTIONS)
        # Real ids carry the -preview suffix or are stable flash.
        assert "gemini-3.1-pro-preview" in cd._MODEL_SUGGESTIONS
        assert "gemini-3.5-flash" in cd._MODEL_SUGGESTIONS


class TestFetchAvailableModels:
    def _fake_urlopen(self, payload: dict) -> object:
        import json as _json

        class _Resp:
            def __enter__(self_inner):  # noqa: N805
                return self_inner

            def __exit__(self_inner, *a):  # noqa: N805, ANN001
                return False

            def read(self_inner):  # noqa: N805
                return _json.dumps(payload).encode("utf-8")

        return _Resp()

    def test_filters_to_generatecontent_gemini_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.request as _ur

        from mhm_pipeline.gui.dialogs.credentials_dialog import (
            fetch_available_gemini_models,
        )
        payload = {
            "models": [
                {"name": "models/gemini-3.1-pro-preview",
                 "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.5-flash",
                 "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004",
                 "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/gemini-3.1-flash-tts-preview",
                 "supportedGenerationMethods": ["bidiGenerateContent"]},
            ]
        }
        monkeypatch.setattr(_ur, "urlopen", lambda *a, **k: self._fake_urlopen(payload))
        models = fetch_available_gemini_models("AIzaKey")
        assert "gemini-3.5-flash" in models
        assert "gemini-3.1-pro-preview" in models
        assert "text-embedding-004" not in models       # not gemini-
        assert "gemini-3.1-flash-tts-preview" not in models  # no generateContent

    def test_empty_key_returns_empty(self) -> None:
        from mhm_pipeline.gui.dialogs.credentials_dialog import (
            fetch_available_gemini_models,
        )
        assert fetch_available_gemini_models("") == []
        assert fetch_available_gemini_models("   ") == []

    def test_network_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.request as _ur

        from mhm_pipeline.gui.dialogs.credentials_dialog import (
            fetch_available_gemini_models,
        )

        def _boom(*a, **k):  # noqa: ANN001, ANN202
            raise OSError("no network")

        monkeypatch.setattr(_ur, "urlopen", _boom)
        assert fetch_available_gemini_models("AIzaKey") == []


class TestRefreshModelsApply:
    def test_apply_repopulates_and_preserves_selection(
        self, settings: SettingsManager
    ) -> None:
        dialog = CredentialsDialog(settings)
        dialog._tier_model_combo.setCurrentText("gemini-3.5-flash")
        dialog._escalate_model_combo.setCurrentText("my-custom-model")
        dialog._apply_fetched_models(
            ["gemini-3.5-flash", "gemini-3.1-pro-preview"]
        )
        # Combo items replaced with the live list…
        items = [dialog._tier_model_combo.itemText(i)
                 for i in range(dialog._tier_model_combo.count())]
        assert items == ["gemini-3.5-flash", "gemini-3.1-pro-preview"]
        # …but the user's current selections are preserved (even a custom one).
        assert dialog._tier_model_combo.currentText() == "gemini-3.5-flash"
        assert dialog._escalate_model_combo.currentText() == "my-custom-model"

    def test_apply_empty_keeps_existing_items(
        self, settings: SettingsManager
    ) -> None:
        dialog = CredentialsDialog(settings)
        before = dialog._tier_model_combo.count()
        dialog._apply_fetched_models([])  # offline / invalid-key path
        assert dialog._tier_model_combo.count() == before


# ── Save + Clear ────────────────────────────────────────────────────


class TestSaveAndClear:
    def test_save_persists_typed_value(
        self, settings: SettingsManager, stub_keyring: _StubKeyring
    ) -> None:
        dialog = CredentialsDialog(settings)
        captured: list[set[str]] = []
        dialog.saved.connect(lambda s: captured.append(s))

        dialog._gemini_input.setText("AIzaNew")
        dialog._on_save()

        assert stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] == "AIzaNew"
        assert captured == [{GEMINI_API_KEY}]

    def test_empty_input_preserves_existing_stored_value(
        self, settings: SettingsManager, stub_keyring: _StubKeyring
    ) -> None:
        stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] = "AIzaExisting"
        dialog = CredentialsDialog(settings)
        captured: list[set[str]] = []
        dialog.saved.connect(lambda s: captured.append(s))

        # Touch Wikidata, leave Gemini empty.
        dialog._wikidata_input.setText("User@Bot:new")
        dialog._on_save()

        # Gemini value still there.
        assert stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] == "AIzaExisting"
        # Wikidata changed.
        assert stub_keyring.store[(SERVICE_NAME, WIKIDATA_TOKEN)] == "User@Bot:new"
        # saved set names what changed only.
        assert captured == [{WIKIDATA_TOKEN}]

    def test_clear_button_deletes_stored_value(
        self, settings: SettingsManager, stub_keyring: _StubKeyring
    ) -> None:
        stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] = "AIzaWillBeGone"
        dialog = CredentialsDialog(settings)
        dialog._on_clear(GEMINI_API_KEY)
        assert (SERVICE_NAME, GEMINI_API_KEY) not in stub_keyring.store

    def test_clear_button_grays_after_clearing(
        self, settings: SettingsManager, stub_keyring: _StubKeyring
    ) -> None:
        stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] = "AIzaA"
        dialog = CredentialsDialog(settings)
        assert dialog._gemini_clear.isEnabled() is True
        dialog._on_clear(GEMINI_API_KEY)
        assert dialog._gemini_clear.isEnabled() is False

    def test_save_then_reopen_keeps_input_blank(
        self, settings: SettingsManager, stub_keyring: _StubKeyring
    ) -> None:
        """Surface contract: even after a successful save, reopening
        the dialog still hides the value behind the empty input +
        placeholder pattern. No round-tripping the secret to the UI."""
        dialog1 = CredentialsDialog(settings)
        dialog1._gemini_input.setText("AIzaNeverShown")
        dialog1._on_save()

        dialog2 = CredentialsDialog(settings)
        assert dialog2._gemini_input.text() == ""
        assert dialog2._gemini_input.placeholderText() == _STORED_PLACEHOLDER


# ── Wikibase Cloud non-secret fields persist directly ───────────────


class TestWikibaseUsernameAndBotName:
    def test_save_persists_non_secret_fields(
        self, settings: SettingsManager
    ) -> None:
        dialog = CredentialsDialog(settings)
        dialog._wb_username.setText("Alexander Goldberg IL")
        dialog._wb_botname.setText("MHMPipelineBot")
        dialog._on_save()
        assert settings.wikibase_cloud_bot_username == "Alexander Goldberg IL"
        assert settings.wikibase_cloud_bot_name == "MHMPipelineBot"


# ── AI-verification model combos (non-secret, prefilled) ────────────


class TestAiVerificationModelCombos:
    def test_combos_render_and_are_editable(
        self, settings: SettingsManager
    ) -> None:
        dialog = CredentialsDialog(settings)
        assert dialog._tier_model_combo is not None
        assert dialog._escalate_model_combo is not None
        assert dialog._tier_model_combo.isEditable()
        assert dialog._escalate_model_combo.isEditable()

    def test_combos_prefill_from_settings(
        self, settings: SettingsManager
    ) -> None:
        settings.eval_agent_tier_model = "gemini-3.5-flash"
        settings.eval_agent_escalate_model = "gemini-2.5-pro"
        dialog = CredentialsDialog(settings)
        assert dialog._tier_model_combo.currentText() == "gemini-3.5-flash"
        assert dialog._escalate_model_combo.currentText() == "gemini-2.5-pro"

    def test_combo_accepts_custom_value_not_in_suggestions(
        self, settings: SettingsManager
    ) -> None:
        settings.eval_agent_tier_model = "my-custom-model-id"
        dialog = CredentialsDialog(settings)
        assert dialog._tier_model_combo.currentText() == "my-custom-model-id"

    def test_save_persists_typed_model_values(
        self, settings: SettingsManager
    ) -> None:
        dialog = CredentialsDialog(settings)
        dialog._tier_model_combo.setCurrentText("gemini-3-flash")
        dialog._escalate_model_combo.setCurrentText("gemini-3-pro")
        dialog._on_save()
        assert settings.eval_agent_tier_model == "gemini-3-flash"
        assert settings.eval_agent_escalate_model == "gemini-3-pro"

    def test_empty_model_value_preserves_stored(
        self, settings: SettingsManager
    ) -> None:
        settings.eval_agent_tier_model = "gemini-3.5-flash"
        dialog = CredentialsDialog(settings)
        dialog._tier_model_combo.setCurrentText("   ")
        dialog._on_save()
        assert settings.eval_agent_tier_model == "gemini-3.5-flash"
