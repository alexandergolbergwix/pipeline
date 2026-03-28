"""Cross-platform application directory helpers."""

from __future__ import annotations

from pathlib import Path

import platformdirs

_APP_NAME = "MHMPipeline"
_APP_AUTHOR = "Bar-Ilan University"


def app_data_dir() -> Path:
    """Return platform app data dir (models, DB cache, etc.)."""
    return Path(platformdirs.user_data_dir(_APP_NAME, _APP_AUTHOR))


def app_config_dir() -> Path:
    """Return platform config dir (settings files)."""
    return Path(platformdirs.user_config_dir(_APP_NAME, _APP_AUTHOR))


def app_log_dir() -> Path:
    """Return platform log dir."""
    return Path(platformdirs.user_log_dir(_APP_NAME, _APP_AUTHOR))


def app_cache_dir() -> Path:
    """Return platform cache dir (authority API response cache)."""
    return Path(platformdirs.user_cache_dir(_APP_NAME, _APP_AUTHOR))


def ensure_app_dirs() -> None:
    """Create all application directories if they do not exist."""
    for d in (app_data_dir(), app_config_dir(), app_log_dir(), app_cache_dir()):
        d.mkdir(parents=True, exist_ok=True)
