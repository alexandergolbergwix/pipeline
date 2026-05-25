"""Encrypted credential storage backed by the OS keychain.

Rule 50 (2026-05-25). Three API tokens (Gemini, Wikidata, Wikibase
Cloud bot password) are sensitive; storing them in plain INI files
under the user's home directory leaks them to anything that can read
that directory, and means the user has to re-paste the key every time
they want to verify or upload.

This module routes those three values through the OS keychain via the
:mod:`keyring` library:

* macOS — Keychain.
* Windows — Credential Manager.
* Linux — Secret Service (libsecret).

The :class:`CredentialStore` API has four operations: ``get`` /
``set`` / ``delete`` / ``has``. A first-launch migration sweeps any
plain-text values that existed in the legacy QSettings store (the
pre-Rule-50 location) over to the keychain and clears the QSettings
slot, so users upgrading from a previous build don't lose their keys
or leave them on disk.

**No read-back UX:** the GUI's Credentials dialog never *displays*
the stored value — it shows an empty input with a "stored — type to
replace" placeholder when ``has(key)`` returns True. The user can
override (type a new value) or clear (call ``delete``) but cannot
see what's currently stored. This module enforces nothing about that;
it's a UI contract.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyQt6.QtCore import QSettings

logger = logging.getLogger(__name__)

# Service name registered with the OS keychain. One service, three users
# (one per credential). The user-facing label that some keychains show
# (e.g. macOS Keychain Access) is derived from this.
SERVICE_NAME = "MHMPipeline"

# Account names (the "user" slot in keyring's two-tuple key).
GEMINI_API_KEY = "gemini_api_key"
WIKIDATA_TOKEN = "wikidata_token"
WIKIBASE_CLOUD_BOT_PASSWORD = "wikibase_cloud_bot_password"

# Every credential this module is willing to manage. Adding a new
# credential requires adding it here so the API is closed-set —
# callers can't accidentally route arbitrary strings through the
# keychain.
_VALID_KEYS: frozenset[str] = frozenset({
    GEMINI_API_KEY,
    WIKIDATA_TOKEN,
    WIKIBASE_CLOUD_BOT_PASSWORD,
})


class CredentialStoreError(RuntimeError):
    """Raised when the OS keychain backend is unavailable or rejected
    the operation. The GUI surfaces this with a clear message + a link
    to the Credentials help topic."""


class CredentialStore:
    """Thin wrapper around :mod:`keyring` with closed-set key validation.

    Constructed by :class:`SettingsManager`; downstream code never
    instantiates it directly — it goes through the typed property
    accessors. The store is stateless apart from the cached keyring
    import, so multiple instances are safe.
    """

    def __init__(self) -> None:
        self._keyring = _import_keyring()

    def has(self, key: str) -> bool:
        """Return True iff ``key`` has a stored value."""
        return bool(self.get(key))

    def get(self, key: str) -> str:
        """Return the stored value, or the empty string if none is set."""
        self._validate(key)
        if self._keyring is None:
            return ""
        try:
            value = self._keyring.get_password(SERVICE_NAME, key)
        except Exception as exc:
            logger.warning("Keyring read failed for %s: %s", key, exc)
            return ""
        return value or ""

    def set(self, key: str, value: str) -> None:
        """Store ``value`` against ``key``. Empty string is rejected
        explicitly — callers wanting to clear must call :meth:`delete`
        so the intent is unambiguous in audit trails."""
        self._validate(key)
        if not value:
            raise CredentialStoreError(
                f"Refusing to store empty value for {key!r}. "
                "Use CredentialStore.delete() to clear instead."
            )
        if self._keyring is None:
            raise CredentialStoreError(
                "The OS keychain backend (keyring) is not available. "
                "Cannot store credentials securely."
            )
        try:
            self._keyring.set_password(SERVICE_NAME, key, value)
        except Exception as exc:
            raise CredentialStoreError(
                f"Could not store {key!r} in the OS keychain: {exc}"
            ) from exc

    def delete(self, key: str) -> None:
        """Remove the stored value. No-op when nothing is stored."""
        self._validate(key)
        if self._keyring is None:
            return
        try:
            self._keyring.delete_password(SERVICE_NAME, key)
        except Exception as exc:
            # PasswordDeleteError / NoKeyringError → log + swallow.
            logger.debug("Keyring delete for %s: %s", key, exc)

    @staticmethod
    def _validate(key: str) -> None:
        if key not in _VALID_KEYS:
            raise CredentialStoreError(
                f"{key!r} is not a registered credential. "
                f"Allowed: {sorted(_VALID_KEYS)}"
            )


def migrate_from_qsettings(
    qsettings: QSettings,
    legacy_key_map: dict[str, str],
) -> dict[str, str]:
    """Move any pre-Rule-50 plain-text values from QSettings to the keychain.

    Parameters
    ----------
    qsettings:
        The :class:`QSettings` instance the legacy values were stored
        in (the same backend :class:`SettingsManager` uses for
        non-secrets).
    legacy_key_map:
        Maps the keyring credential id → the legacy QSettings path
        (e.g. ``{"wikidata_token": "tokens/wikidata_token"}``). Only
        listed keys are touched.

    Returns
    -------
    dict
        Map of ``credential_id → migration-outcome`` where each value
        is one of ``"migrated"`` (had a legacy value, copied + cleared),
        ``"already-set"`` (keychain already has a value; legacy
        cleared anyway), or ``"nothing"`` (no legacy value).

    Migration is idempotent — running it twice in a row is a no-op on
    the second pass because the legacy QSettings slots are cleared on
    success.
    """
    store = CredentialStore()
    outcome: dict[str, str] = {}
    for cred_id, legacy_path in legacy_key_map.items():
        legacy_value = str(qsettings.value(legacy_path, "") or "")
        if not legacy_value:
            outcome[cred_id] = "nothing"
            continue
        if store.has(cred_id):
            # Don't clobber a fresh keychain value with a stale legacy one.
            qsettings.remove(legacy_path)
            outcome[cred_id] = "already-set"
            continue
        try:
            store.set(cred_id, legacy_value)
        except CredentialStoreError as exc:
            logger.warning(
                "Could not migrate %s to keyring: %s. Legacy value retained.",
                cred_id, exc,
            )
            outcome[cred_id] = "failed"
            continue
        qsettings.remove(legacy_path)
        outcome[cred_id] = "migrated"
    return outcome


def _import_keyring() -> object | None:
    """Import :mod:`keyring` lazily so the rest of the app keeps
    working in environments where the dependency is missing (CI
    fixtures, stripped containers). Logs a single warning the first
    time the import fails."""
    try:
        import keyring  # noqa: PLC0415

        return keyring
    except ImportError as exc:
        logger.warning(
            "keyring is not installed (%s). Credential storage will be "
            "in-memory only for this session — keys will need to be re-typed "
            "on next launch.", exc,
        )
        return None


__all__ = [
    "CredentialStore",
    "CredentialStoreError",
    "GEMINI_API_KEY",
    "SERVICE_NAME",
    "WIKIBASE_CLOUD_BOT_PASSWORD",
    "WIKIDATA_TOKEN",
    "migrate_from_qsettings",
]
