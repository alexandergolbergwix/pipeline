"""MHM Pipeline desktop application entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from mhm_pipeline.platform_.paths import app_log_dir, ensure_app_dirs
from mhm_pipeline.settings.settings_manager import SettingsManager

# Resolve icon relative to this file so it works from any working directory
_ICON_PATH = Path(__file__).parent.parent.parent / "assets" / "icon.png"


def _configure_logging(log_level: str) -> None:
    """Set up file and console logging."""
    log_dir = app_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "mhm_pipeline.log", encoding="utf-8"),
        ],
    )


def main() -> None:
    """Launch the MHM Pipeline desktop application."""
    ensure_app_dirs()
    app = QApplication(sys.argv)
    app.setApplicationName("MHMPipeline")
    app.setOrganizationName("Bar-Ilan University")
    if _ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_ICON_PATH)))

    settings = SettingsManager()
    _configure_logging(settings.log_level)

    if not settings.first_run_done:
        from mhm_pipeline.gui.wizard.setup_wizard import SetupWizard

        wizard = SetupWizard(settings)
        if wizard.exec() != SetupWizard.DialogCode.Accepted:
            sys.exit(0)

    from mhm_pipeline.controller.pipeline_controller import PipelineController
    from mhm_pipeline.gui.main_window import MainWindow

    controller = PipelineController(settings)
    window = MainWindow(settings, controller)
    window.show()
    sys.exit(app.exec())
