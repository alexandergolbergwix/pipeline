"""Typed wrapper around QSettings for MHM Pipeline configuration."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings

from mhm_pipeline.platform_.paths import app_data_dir


class SettingsManager:
    """Typed, cross-platform settings manager backed by QSettings (INI format).

    All setting keys are exposed as class-level constants.  Typed convenience
    properties provide safe access with sensible defaults.
    """

    # ── Key constants ──────────────────────────────────────────────────
    MODEL_DIR = "paths/model_dir"
    HF_HOME = "paths/hf_home"
    GPU_DEVICE = "compute/gpu_device"
    BATCH_SIZE = "compute/batch_size"
    OUTPUT_DIR = "paths/output_dir"
    WIKIDATA_TOKEN = "tokens/wikidata_token"
    LOG_LEVEL = "logging/log_level"
    FIRST_RUN_DONE = "app/first_run_done"
    THEME = "display/theme"
    MAZAL_DB_PATH = "authority/mazal_db_path"
    MAZAL_XML_DIR = "authority/mazal_xml_dir"
    KIMA_DB_PATH = "authority/kima_db_path"
    KIMA_TSV_DIR = "authority/kima_tsv_dir"
    # ── Rule 45 (Phase 3): Wikibase Cloud bot credentials for IIIF upload ──
    WIKIBASE_CLOUD_URL = "wikibase/cloud_url"
    WIKIBASE_CLOUD_BOT_USERNAME = "wikibase/cloud_bot_username"
    WIKIBASE_CLOUD_BOT_NAME = "wikibase/cloud_bot_name"
    WIKIBASE_CLOUD_BOT_PASSWORD = "wikibase/cloud_bot_password"
    # ── Rule 50: Gemini API key for the bundled eval-agent verification ────
    GEMINI_API_KEY = "tokens/gemini_api_key"
    # ── Rule 52: eval-agent model selection (non-secret; QSettings) ────────
    EVAL_AGENT_TIER_MODEL = "eval_agent/tier_model"
    EVAL_AGENT_ESCALATE_MODEL = "eval_agent/escalate_model"

    # Repo-relative defaults (resolved at class definition time so they survive
    # being imported from any working directory). When frozen by PyInstaller
    # the data files live inside sys._MEIPASS, not relative to this source
    # file — bundled_resource_root() handles both layouts.
    from mhm_pipeline.platform_.paths import bundled_resource_root as _bundled_root_fn
    _REPO_ROOT = _bundled_root_fn()
    del _bundled_root_fn
    _DEFAULT_MAZAL_DB = _REPO_ROOT / "converter" / "authority" / "mazal_index.db"
    _DEFAULT_MAZAL_XML = _REPO_ROOT / "data" / "NLI_AUTHORITY_XML"
    _DEFAULT_KIMA_DB = _REPO_ROOT / "data" / "kima" / "kima_index.db"
    _DEFAULT_KIMA_TSV = _REPO_ROOT / "data" / "kima"

    def __init__(self) -> None:
        self._qs = QSettings(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            "Bar-Ilan University",
            "MHMPipeline",
        )
        # Rule 50: secret-shaped values (Gemini, Wikidata, Wikibase
        # Cloud bot password) are routed through the OS keychain via
        # :mod:`credential_store`. Constructed lazily on first access
        # so that test fixtures running without a real keychain don't
        # pay the import cost.
        self._credentials: object | None = None
        self._credentials_migrated = False

    # ── Generic accessors ──────────────────────────────────────────────

    def get(self, key: str, default: str | int | bool | Path) -> str | int | bool | Path:
        """Return the stored value for *key*, falling back to *default*.

        The returned value is coerced to the same type as *default*.
        """
        raw = self._qs.value(key, default)
        if isinstance(default, bool):
            if isinstance(raw, bool):
                return raw
            return str(raw).lower() in ("true", "1", "yes")
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, Path):
            return Path(str(raw))
        return str(raw)

    def set(self, key: str, value: str | int | bool | Path) -> None:
        """Persist *value* under *key*."""
        if isinstance(value, Path):
            self._qs.setValue(key, str(value))
        else:
            self._qs.setValue(key, value)

    # ── Typed convenience properties ───────────────────────────────────

    # model_dir
    @property
    def model_dir(self) -> Path:
        """Directory where downloaded models are stored."""
        return Path(str(self.get(self.MODEL_DIR, app_data_dir() / "models")))

    @model_dir.setter
    def model_dir(self, value: Path) -> None:
        self.set(self.MODEL_DIR, value)

    # hf_home
    @property
    def hf_home(self) -> Path:
        """HuggingFace cache directory."""
        return Path(str(self.get(self.HF_HOME, app_data_dir() / "hf_cache")))

    @hf_home.setter
    def hf_home(self, value: Path) -> None:
        self.set(self.HF_HOME, value)

    # gpu_device
    @property
    def gpu_device(self) -> str:
        """Compute device preference ('auto', 'mps', 'cuda', or 'cpu')."""
        return str(self.get(self.GPU_DEVICE, "auto"))

    @gpu_device.setter
    def gpu_device(self, value: str) -> None:
        self.set(self.GPU_DEVICE, value)

    # eval_agent_tier_model — tier-1 model for AI verification (non-secret)
    @property
    def eval_agent_tier_model(self) -> str:
        """Tier-1 (cheap pass) model id for eval-agent verification."""
        return str(self.get(self.EVAL_AGENT_TIER_MODEL, "gemini-3.5-flash"))

    @eval_agent_tier_model.setter
    def eval_agent_tier_model(self, value: str) -> None:
        self.set(self.EVAL_AGENT_TIER_MODEL, value)

    # eval_agent_escalate_model — model the agent loop escalates to
    @property
    def eval_agent_escalate_model(self) -> str:
        """Escalation model id for eval-agent verification (hard cases)."""
        return str(self.get(self.EVAL_AGENT_ESCALATE_MODEL, "gemini-3.1-pro-preview"))

    @eval_agent_escalate_model.setter
    def eval_agent_escalate_model(self, value: str) -> None:
        self.set(self.EVAL_AGENT_ESCALATE_MODEL, value)

    # batch_size
    @property
    def batch_size(self) -> int:
        """Inference batch size."""
        return int(self.get(self.BATCH_SIZE, 32))  # type: ignore[arg-type]

    @batch_size.setter
    def batch_size(self, value: int) -> None:
        self.set(self.BATCH_SIZE, value)

    # output_dir
    @property
    def output_dir(self) -> Path:
        """Default directory for pipeline output files."""
        return Path(str(self.get(self.OUTPUT_DIR, Path.home() / "MHM_Output")))

    @output_dir.setter
    def output_dir(self, value: Path) -> None:
        self.set(self.OUTPUT_DIR, value)

    # ── Rule 50 — encrypted credential storage via the OS keychain ────
    # Three secret-shaped values are routed through ``credential_store``
    # (which wraps :mod:`keyring`) instead of the QSettings INI file.
    # The pre-Rule-50 INI values are migrated on first access.

    def _ensure_credentials(self) -> object:
        """Return the lazy-imported :class:`CredentialStore`, running
        the one-shot legacy migration on the first call."""
        if self._credentials is None:
            from mhm_pipeline.settings.credential_store import (  # noqa: PLC0415
                CredentialStore,
                migrate_from_qsettings,
            )

            self._credentials = CredentialStore()
            if not self._credentials_migrated:
                migrate_from_qsettings(
                    self._qs,
                    {
                        "wikidata_token": self.WIKIDATA_TOKEN,
                        "gemini_api_key": self.GEMINI_API_KEY,
                        "wikibase_cloud_bot_password": self.WIKIBASE_CLOUD_BOT_PASSWORD,
                    },
                )
                self._credentials_migrated = True
        return self._credentials

    @property
    def credentials_backend(self) -> object:
        """Public handle on the :class:`CredentialStore` for the
        Credentials dialog (so the dialog can call ``has(key)`` /
        ``delete(key)`` directly without re-implementing the API)."""
        return self._ensure_credentials()

    # wikidata_token (Rule 50: OS keychain)
    @property
    def wikidata_token(self) -> str:
        """Wikidata API bearer token. Stored in OS keychain (Rule 50)."""
        store = self._ensure_credentials()
        return store.get("wikidata_token")

    @wikidata_token.setter
    def wikidata_token(self, value: str) -> None:
        store = self._ensure_credentials()
        if value:
            store.set("wikidata_token", value)
        else:
            store.delete("wikidata_token")

    # gemini_api_key (Rule 50: OS keychain)
    @property
    def gemini_api_key(self) -> str:
        """Google Gemini API key used by the bundled eval-agent.

        Stored in the OS keychain (Keychain on macOS, Credential
        Manager on Windows, libsecret on Linux) via
        :mod:`credential_store`. Returns the empty string when no key
        is configured.
        """
        store = self._ensure_credentials()
        return store.get("gemini_api_key")

    @gemini_api_key.setter
    def gemini_api_key(self, value: str) -> None:
        store = self._ensure_credentials()
        if value:
            store.set("gemini_api_key", value)
        else:
            store.delete("gemini_api_key")

    # log_level
    @property
    def log_level(self) -> str:
        """Application log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)."""
        return str(self.get(self.LOG_LEVEL, "INFO"))

    @log_level.setter
    def log_level(self, value: str) -> None:
        self.set(self.LOG_LEVEL, value)

    # theme
    @property
    def theme(self) -> str:
        """Appearance: 'system' (auto-detect), 'dark', or 'light'."""
        return str(self.get(self.THEME, "system"))

    @theme.setter
    def theme(self, value: str) -> None:
        if value not in ("system", "dark", "light"):
            value = "system"
        self.set(self.THEME, value)

    # first_run_done
    @property
    def first_run_done(self) -> bool:
        """Whether the first-run wizard has completed."""
        return bool(self.get(self.FIRST_RUN_DONE, False))

    @first_run_done.setter
    def first_run_done(self, value: bool) -> None:
        self.set(self.FIRST_RUN_DONE, value)

    # mazal_db_path
    @property
    def mazal_db_path(self) -> Path:
        """Path to the Mazal (NLI) SQLite authority index."""
        return Path(str(self.get(self.MAZAL_DB_PATH, self._DEFAULT_MAZAL_DB)))

    @mazal_db_path.setter
    def mazal_db_path(self, value: Path) -> None:
        self.set(self.MAZAL_DB_PATH, value)

    # mazal_xml_dir
    @property
    def mazal_xml_dir(self) -> Path:
        """Directory containing NLI authority XML files (NLIAUT*.xml)."""
        return Path(str(self.get(self.MAZAL_XML_DIR, self._DEFAULT_MAZAL_XML)))

    @mazal_xml_dir.setter
    def mazal_xml_dir(self, value: Path) -> None:
        self.set(self.MAZAL_XML_DIR, value)

    # kima_db_path
    @property
    def kima_db_path(self) -> Path:
        """Path to the KIMA SQLite authority index."""
        return Path(str(self.get(self.KIMA_DB_PATH, self._DEFAULT_KIMA_DB)))

    @kima_db_path.setter
    def kima_db_path(self, value: Path) -> None:
        self.set(self.KIMA_DB_PATH, value)

    # kima_tsv_dir
    @property
    def kima_tsv_dir(self) -> Path:
        """Directory containing the KIMA TSV source files."""
        return Path(str(self.get(self.KIMA_TSV_DIR, self._DEFAULT_KIMA_TSV)))

    @kima_tsv_dir.setter
    def kima_tsv_dir(self, value: Path) -> None:
        self.set(self.KIMA_TSV_DIR, value)

    # ── Rule 45 (Phase 3): Wikibase Cloud bot credentials ──────────────

    @property
    def wikibase_cloud_url(self) -> str:
        """Base URL of the project Wikibase Cloud (default: mhm-hmo.wikibase.cloud)."""
        return str(self.get(self.WIKIBASE_CLOUD_URL, "https://mhm-hmo.wikibase.cloud"))

    @wikibase_cloud_url.setter
    def wikibase_cloud_url(self, value: str) -> None:
        self.set(self.WIKIBASE_CLOUD_URL, value)

    @property
    def wikibase_cloud_bot_username(self) -> str:
        """Bot account username on the Wikibase Cloud instance."""
        return str(self.get(self.WIKIBASE_CLOUD_BOT_USERNAME, ""))

    @wikibase_cloud_bot_username.setter
    def wikibase_cloud_bot_username(self, value: str) -> None:
        self.set(self.WIKIBASE_CLOUD_BOT_USERNAME, value)

    @property
    def wikibase_cloud_bot_name(self) -> str:
        """Bot password name (the part after ``@`` in Special:BotPasswords)."""
        return str(self.get(self.WIKIBASE_CLOUD_BOT_NAME, ""))

    @wikibase_cloud_bot_name.setter
    def wikibase_cloud_bot_name(self, value: str) -> None:
        self.set(self.WIKIBASE_CLOUD_BOT_NAME, value)

    @property
    def wikibase_cloud_bot_password(self) -> str:
        """The actual bot password. Stored in the OS keychain (Rule 50).

        Pre-Rule-50 builds stored this in the QSettings INI file
        ("OS keychain via QSettings" was a misleading comment — INI
        format never used the keychain on Linux/Windows). The
        :meth:`_ensure_credentials` first-launch migration moves any
        such legacy value over to the real keychain.
        """
        store = self._ensure_credentials()
        return store.get("wikibase_cloud_bot_password")

    @wikibase_cloud_bot_password.setter
    def wikibase_cloud_bot_password(self, value: str) -> None:
        store = self._ensure_credentials()
        if value:
            store.set("wikibase_cloud_bot_password", value)
        else:
            store.delete("wikibase_cloud_bot_password")

    @property
    def wikibase_cloud_credentials(self) -> object | None:
        """Resolved :class:`WikibaseBotCredentials` or ``None`` if not configured.

        Lazy import on the wikibase module so importing ``settings_manager``
        in tests does not pull the writer dependency tree.
        """
        username = self.wikibase_cloud_bot_username.strip()
        bot_name = self.wikibase_cloud_bot_name.strip()
        password = self.wikibase_cloud_bot_password.strip()
        if not (username and bot_name and password):
            return None
        from converter.wikibase.cloud_client import WikibaseBotCredentials  # noqa: PLC0415
        return WikibaseBotCredentials(
            username=username,
            bot_name=bot_name,
            password=password,
        )
