"""Cross-platform application directory helpers."""

from __future__ import annotations

import sys
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


def bundled_resource_root() -> Path:
    """Return the base directory containing bundled assets (models, DBs, ontologies).

    When frozen by PyInstaller (one-folder mode), returns sys._MEIPASS — the
    extraction directory. In development, returns the repo root (3 parents up
    from this file: src/mhm_pipeline/platform_/paths.py -> repo root).
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parents[3]


def find_model_weights(filename: str) -> Path | None:
    """Locate a model weight file (.pt, .bin, .safetensors) across all the
    deployment layouts the pipeline supports.

    Search order — each layout maps to a different distribution channel:

    1. PyInstaller-frozen bundle (Windows / Linux .exe) — files are staged
       under ``sys._MEIPASS / "ner" / <filename>`` by the spec's `_opt(...)`
       calls.
    2. Repo / pipeline layout (development, or .app where the source tree is
       at ``Contents/Resources/pipeline/``) — ``<root>/ner/<filename>``.
    3. macOS .app bundle's separate models dir — ``<.app>/Contents/Resources
       /models/<filename>``. The bundle's `build_app.sh` puts large model
       weights here (sibling of `pipeline/`) so the source tree stays small.

    Returns the first matching path, or None if the file isn't found anywhere.
    Used by both `controller/workers.py` (model loading) and the NER panel
    checkbox auto-detection so the UI never disagrees with the worker about
    whether a model is present.
    """
    candidates: list[Path] = [
        bundled_resource_root() / "ner" / filename,
    ]

    # Walk up to the macOS .app ancestor, if any, and add the two layouts.
    for parent in Path(__file__).resolve().parents:
        if parent.name.endswith(".app"):
            candidates.append(parent / "Contents" / "Resources" / "pipeline" / "ner" / filename)
            candidates.append(parent / "Contents" / "Resources" / "models" / filename)
            break

    for cand in candidates:
        try:
            if cand.exists():
                return cand
        except OSError:
            continue
    return None
