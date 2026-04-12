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
    MAZAL_DB_PATH = "authority/mazal_db_path"
    MAZAL_XML_DIR = "authority/mazal_xml_dir"
    KIMA_DB_PATH = "authority/kima_db_path"
    KIMA_TSV_DIR = "authority/kima_tsv_dir"

    # Repo-relative defaults (resolved at class definition time so they survive
    # being imported from any working directory).
    _REPO_ROOT = Path(__file__).parents[3]
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

    # wikidata_token
    @property
    def wikidata_token(self) -> str:
        """Wikidata API bearer token."""
        return str(self.get(self.WIKIDATA_TOKEN, ""))

    @wikidata_token.setter
    def wikidata_token(self, value: str) -> None:
        self.set(self.WIKIDATA_TOKEN, value)

    # log_level
    @property
    def log_level(self) -> str:
        """Application log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)."""
        return str(self.get(self.LOG_LEVEL, "INFO"))

    @log_level.setter
    def log_level(self, value: str) -> None:
        self.set(self.LOG_LEVEL, value)

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
