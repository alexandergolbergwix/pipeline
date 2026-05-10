"""MHM Pipeline desktop application entry point."""

from __future__ import annotations

import os
import sys

# PyInstaller windowed builds (`console=False`) have `sys.stdout` and
# `sys.stderr` set to `None`. tqdm / transformers / huggingface_hub call
# `.isatty()` on them, which crashes Stage 2 with
# "'NoneType' object has no attribute 'isatty'". Replace both with
# `os.devnull` writers so every standard file method (write, flush,
# isatty, fileno) is safe. Must happen before importing PyQt or anything
# that may pull in transformers / torch.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

import datetime  # noqa: E402
import logging  # noqa: E402
from pathlib import Path  # noqa: E402

from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.platform_.paths import app_log_dir, ensure_app_dirs
from mhm_pipeline.settings.settings_manager import SettingsManager

# Resolve icon relative to this file so it works from any working directory
_ICON_PATH = Path(__file__).parent.parent.parent / "assets" / "icon.png"


def _configure_logging(log_level: str) -> None:
    """Set up file and console logging with daily rotation and 30-day TTL."""
    from logging.handlers import TimedRotatingFileHandler  # noqa: PLC0415

    log_dir = app_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "mhm_pipeline.log"

    # Daily rotation, keep 30 days of logs
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            file_handler,
        ],
    )


def _set_macos_process_name() -> None:
    """Set the app process name in the macOS menu bar when available."""
    if sys.platform != "darwin":
        return

    try:
        import ctypes  # noqa: PLC0415

        libc = ctypes.cdll.LoadLibrary("libc.dylib")
        libc.setprogname(b"MHM Pipeline")
    except Exception:
        pass


def _set_bundled_model_env_if_frozen() -> None:
    """When frozen, point the inference wrappers at the bundled local model
    directories (so `transformers.from_pretrained(...)` reads files off disk
    and never goes to the network). Runs unconditionally on every launch —
    env vars are not persisted between processes, and the macOS launcher
    does the equivalent in shell.
    """
    if not getattr(sys, "frozen", False):
        return
    from mhm_pipeline.platform_.paths import bundled_resource_root  # noqa: PLC0415

    root = bundled_resource_root()
    bundle_paths = {
        "MHM_BUNDLED_DICTABERT": root / "models" / "dictabert",
        "MHM_BUNDLED_NER_MODEL": root / "models" / "hebrew-manuscript-joint-ner-v2",
        "MHM_BUNDLED_PROVENANCE_MODEL": root / "ner" / "provenance_ner_model.pt",
        "MHM_BUNDLED_CONTENTS_MODEL": root / "ner" / "contents_ner_model.pt",
    }
    for var, path in bundle_paths.items():
        if path.exists():
            os.environ.setdefault(var, str(path))

    # Belt-and-braces: even if a wrapper still uses the HF model id,
    # force transformers to look in the bundle's models/ dir (with HF
    # cache layout) and never go online.
    if (root / "models").exists():
        os.environ.setdefault("HF_HOME", str(root / "models"))
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _auto_complete_first_run_if_bundled(settings: SettingsManager) -> None:
    """Skip the first-run wizard if the app is frozen by PyInstaller and all
    bundled models are present at the expected paths."""
    if not getattr(sys, "frozen", False):
        return
    if settings.first_run_done:
        return
    from mhm_pipeline.platform_.paths import bundled_resource_root  # noqa: PLC0415

    root = bundled_resource_root()
    required = [
        root / "converter" / "authority" / "mazal_index.db",
        root / "data" / "kima" / "kima_index.db",
        root / "ner" / "provenance_ner_model.pt",
        root / "ner" / "contents_ner_model.pt",
        root / "models" / "hebrew-manuscript-joint-ner-v2",
        root / "models" / "dictabert",
    ]
    if all(p.exists() for p in required):
        settings.first_run_done = True


def _append_crash_log(traceback_text: str) -> None:
    """Write fallback crash information to the platform log directory."""
    crash_path = app_log_dir() / "crash.log"
    crash_path.parent.mkdir(parents=True, exist_ok=True)
    with crash_path.open("a", encoding="utf-8") as crash_file:
        crash_file.write(f"\n--- {datetime.datetime.now()} ---\n{traceback_text}")


def main() -> None:
    """Launch the MHM Pipeline desktop application."""
    ensure_app_dirs()

    # Required for QWebEngineView (Cytoscape.js graph viewer) — must be set
    # BEFORE QApplication is created, otherwise WebEngine silently fails.
    from PyQt6.QtCore import Qt  # noqa: PLC0415

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    # Set process name for macOS menu bar (shows "MHM Pipeline" instead of "Python")
    _set_macos_process_name()

    app = QApplication(sys.argv)
    app.setApplicationName("MHM Pipeline")
    app.setOrganizationName("Bar-Ilan University")
    if _ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_ICON_PATH)))

    settings = SettingsManager()
    _set_bundled_model_env_if_frozen()
    _auto_complete_first_run_if_bundled(settings)
    _configure_logging(settings.log_level)

    if not settings.first_run_done:
        from mhm_pipeline.gui.wizard.setup_wizard import SetupWizard

        wizard = SetupWizard(settings)
        if wizard.exec() != SetupWizard.DialogCode.Accepted:
            sys.exit(0)

    from mhm_pipeline.controller.pipeline_controller import PipelineController
    from mhm_pipeline.gui import theme
    from mhm_pipeline.gui.main_window import MainWindow

    theme.apply_stylesheet(app)

    controller = PipelineController(settings)
    window = MainWindow(settings, controller)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception as _exc:
        import traceback  # noqa: PLC0415

        _tb = traceback.format_exc()
        try:
            import logging as _logging  # noqa: PLC0415

            _logging.getLogger(__name__).critical("Unhandled exception: %s\n%s", _exc, _tb)
        except Exception:
            pass
        # Also write to a plain crash file so it's visible even without logging
        try:
            _append_crash_log(_tb)
        except Exception:
            pass
        raise
