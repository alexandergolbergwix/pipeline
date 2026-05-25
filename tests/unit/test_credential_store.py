"""Unit tests for :mod:`mhm_pipeline.settings.credential_store`.

Tests use a stubbed :mod:`keyring` backend (an in-memory dict) so they
work in any environment, including CI containers without a real OS
keychain. The stub is injected via :meth:`monkeypatch.setattr` into
the module-level ``_import_keyring`` so the production code path is
unchanged.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from mhm_pipeline.settings import credential_store
from mhm_pipeline.settings.credential_store import (
    GEMINI_API_KEY,
    SERVICE_NAME,
    WIKIBASE_CLOUD_BOT_PASSWORD,
    WIKIDATA_TOKEN,
    CredentialStore,
    CredentialStoreError,
    migrate_from_qsettings,
)


class _StubKeyring:
    """In-memory stand-in for :mod:`keyring`. Stores by (service, user)."""

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
    """Replace ``_import_keyring`` with one that returns the stub."""
    stub = _StubKeyring()
    monkeypatch.setattr(credential_store, "_import_keyring", lambda: stub)
    return stub


# ── Round-trip + closed-set validation ──────────────────────────────


class TestCredentialStoreRoundTrip:
    def test_set_then_get_returns_stored_value(
        self, stub_keyring: _StubKeyring
    ) -> None:
        store = CredentialStore()
        store.set(GEMINI_API_KEY, "AIzaTEST")
        assert store.get(GEMINI_API_KEY) == "AIzaTEST"
        assert stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] == "AIzaTEST"

    def test_get_returns_empty_string_when_unset(
        self, stub_keyring: _StubKeyring
    ) -> None:
        store = CredentialStore()
        assert store.get(WIKIDATA_TOKEN) == ""

    def test_has_returns_false_when_unset(
        self, stub_keyring: _StubKeyring
    ) -> None:
        store = CredentialStore()
        assert store.has(GEMINI_API_KEY) is False

    def test_has_returns_true_when_set(self, stub_keyring: _StubKeyring) -> None:
        store = CredentialStore()
        store.set(GEMINI_API_KEY, "AIzaABC")
        assert store.has(GEMINI_API_KEY) is True

    def test_delete_clears_the_value(self, stub_keyring: _StubKeyring) -> None:
        store = CredentialStore()
        store.set(WIKIBASE_CLOUD_BOT_PASSWORD, "hex123")
        store.delete(WIKIBASE_CLOUD_BOT_PASSWORD)
        assert store.has(WIKIBASE_CLOUD_BOT_PASSWORD) is False

    def test_delete_is_noop_when_unset(self, stub_keyring: _StubKeyring) -> None:
        store = CredentialStore()
        # Should not raise.
        store.delete(GEMINI_API_KEY)


class TestCredentialStoreClosedSet:
    def test_unknown_key_rejected_on_get(
        self, stub_keyring: _StubKeyring
    ) -> None:
        store = CredentialStore()
        with pytest.raises(CredentialStoreError):
            store.get("rogue_key")

    def test_unknown_key_rejected_on_set(
        self, stub_keyring: _StubKeyring
    ) -> None:
        store = CredentialStore()
        with pytest.raises(CredentialStoreError):
            store.set("rogue_key", "value")

    def test_empty_value_rejected_on_set(
        self, stub_keyring: _StubKeyring
    ) -> None:
        store = CredentialStore()
        with pytest.raises(CredentialStoreError):
            store.set(GEMINI_API_KEY, "")


class TestCredentialStoreNoBackend:
    """When keyring is unavailable the store still functions in
    read-only / delete-tolerant mode, but refuses to ``set`` so we
    never accidentally lose a secret to /dev/null."""

    def test_get_returns_empty_when_keyring_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(credential_store, "_import_keyring", lambda: None)
        store = CredentialStore()
        assert store.get(GEMINI_API_KEY) == ""

    def test_set_raises_when_keyring_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(credential_store, "_import_keyring", lambda: None)
        store = CredentialStore()
        with pytest.raises(CredentialStoreError):
            store.set(GEMINI_API_KEY, "AIzaX")

    def test_delete_is_noop_when_keyring_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(credential_store, "_import_keyring", lambda: None)
        store = CredentialStore()
        store.delete(GEMINI_API_KEY)  # Must not raise.


# ── Legacy QSettings migration ──────────────────────────────────────


class _StubQSettings:
    """Minimal QSettings-shaped stub for migrate_from_qsettings."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = dict(values or {})

    def value(self, key: str, default: Any = "") -> Any:
        return self._values.get(key, default)

    def remove(self, key: str) -> None:
        self._values.pop(key, None)


class TestMigrateFromQSettings:
    def test_migrates_each_legacy_value_into_keychain(
        self, stub_keyring: _StubKeyring
    ) -> None:
        qs = _StubQSettings({
            "tokens/wikidata_token": "User@Bot:legacy",
            "tokens/gemini_api_key": "AIzaLegacy",
        })
        outcome = migrate_from_qsettings(
            qs,  # type: ignore[arg-type]
            {
                "wikidata_token": "tokens/wikidata_token",
                "gemini_api_key": "tokens/gemini_api_key",
            },
        )
        assert outcome["wikidata_token"] == "migrated"
        assert outcome["gemini_api_key"] == "migrated"
        # Verify the values landed in the keychain.
        assert stub_keyring.store[(SERVICE_NAME, "wikidata_token")] == "User@Bot:legacy"
        assert stub_keyring.store[(SERVICE_NAME, "gemini_api_key")] == "AIzaLegacy"
        # And were cleared from the legacy store.
        assert qs.value("tokens/wikidata_token") == ""
        assert qs.value("tokens/gemini_api_key") == ""

    def test_already_set_in_keychain_does_not_clobber(
        self, stub_keyring: _StubKeyring
    ) -> None:
        stub_keyring.store[(SERVICE_NAME, "gemini_api_key")] = "AIzaCurrent"
        qs = _StubQSettings({"tokens/gemini_api_key": "AIzaStale"})
        outcome = migrate_from_qsettings(
            qs,  # type: ignore[arg-type]
            {"gemini_api_key": "tokens/gemini_api_key"},
        )
        assert outcome["gemini_api_key"] == "already-set"
        # Keychain value untouched.
        assert stub_keyring.store[(SERVICE_NAME, "gemini_api_key")] == "AIzaCurrent"
        # Legacy slot cleared anyway.
        assert qs.value("tokens/gemini_api_key") == ""

    def test_nothing_returned_when_legacy_empty(
        self, stub_keyring: _StubKeyring
    ) -> None:
        qs = _StubQSettings()
        outcome = migrate_from_qsettings(
            qs,  # type: ignore[arg-type]
            {"gemini_api_key": "tokens/gemini_api_key"},
        )
        assert outcome["gemini_api_key"] == "nothing"

    def test_idempotent_on_second_run(
        self, stub_keyring: _StubKeyring
    ) -> None:
        qs = _StubQSettings({"tokens/wikidata_token": "User@Bot:legacy"})
        migrate_from_qsettings(
            qs,  # type: ignore[arg-type]
            {"wikidata_token": "tokens/wikidata_token"},
        )
        # Second run — legacy slot is empty, keychain already has the value.
        outcome = migrate_from_qsettings(
            qs,  # type: ignore[arg-type]
            {"wikidata_token": "tokens/wikidata_token"},
        )
        assert outcome["wikidata_token"] == "nothing"


class TestKeyringFailureSwallowed:
    def test_get_swallows_keyring_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad = MagicMock()
        bad.get_password.side_effect = RuntimeError("backend exploded")
        monkeypatch.setattr(credential_store, "_import_keyring", lambda: bad)
        store = CredentialStore()
        assert store.get(GEMINI_API_KEY) == ""

    def test_set_wraps_keyring_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad = MagicMock()
        bad.set_password.side_effect = RuntimeError("backend exploded")
        monkeypatch.setattr(credential_store, "_import_keyring", lambda: bad)
        store = CredentialStore()
        with pytest.raises(CredentialStoreError):
            store.set(GEMINI_API_KEY, "AIzaX")
