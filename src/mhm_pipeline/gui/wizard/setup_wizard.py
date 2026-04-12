"""First-run setup wizard for model downloading and configuration."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QProgressBar,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from mhm_pipeline.platform_.gpu import get_device
from mhm_pipeline.settings.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

_FULL_MODELS: list[str] = [
    "hebrew-nlp/HalleluBERT-wwm",
    "hebrew-nlp/NeoDictaBERT",
]
_CPU_MODELS: list[str] = [
    "hebrew-nlp/HalleluBERT-wwm",
]


# ── Download worker ──────────────────────────────────────────────────


class _DownloadWorker(QThread):
    """Background worker that downloads HuggingFace model snapshots."""

    progress = pyqtSignal(int, int, str)  # current, total, model_name
    finished_ok = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        models: list[str],
        cache_dir: Path,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._models = models
        self._cache_dir = cache_dir

    def run(self) -> None:
        """Execute model downloads sequentially."""
        try:
            from huggingface_hub import snapshot_download  # type: ignore[import-untyped]

            total = len(self._models)
            for idx, model in enumerate(self._models):
                self.progress.emit(idx, total, model)
                snapshot_download(
                    repo_id=model,
                    cache_dir=str(self._cache_dir),
                )
            self.progress.emit(total, total, "")
            self.finished_ok.emit()
        except Exception as exc:
            logger.exception("Model download failed")
            self.error_occurred.emit(str(exc))


# ── Wizard pages ─────────────────────────────────────────────────────


class _WelcomePage(QWizardPage):
    """Page 1: greeting, detected GPU, and disk space."""

    def __init__(self, parent: QWizardPage | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Welcome to MHM Pipeline")
        self.setSubTitle("This wizard will help you download the required NLP models.")

        layout = QVBoxLayout(self)

        device = get_device()
        layout.addWidget(QLabel(f"Detected compute device: {device}"))

        free_gb = _free_disk_gb()
        layout.addWidget(QLabel(f"Free disk space: {free_gb:.1f} GB"))
        layout.addWidget(
            QLabel(
                "You need at least 6 GB of free space for the full model set, "
                "or 2.5 GB for CPU-only models."
            )
        )


class _ModelSelectPage(QWizardPage):
    """Page 2: choose Full or CPU-only model profile."""

    def __init__(self, parent: QWizardPage | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Model Profile")
        self.setSubTitle("Select the model profile to download.")

        layout = QVBoxLayout(self)
        self._radio_full = QRadioButton("Full models (5.6 GB) — GPU recommended")
        self._radio_full.setChecked(True)
        self._radio_cpu = QRadioButton("CPU-only models (2.1 GB)")
        layout.addWidget(self._radio_full)
        layout.addWidget(self._radio_cpu)

        self.registerField("full_profile", self._radio_full)

    @property
    def is_full(self) -> bool:
        """Return True if the user selected the full profile."""
        return self._radio_full.isChecked()


class _DownloadPage(QWizardPage):
    """Page 3: download models with progress feedback."""

    def __init__(
        self,
        settings: SettingsManager,
        parent: QWizardPage | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._worker: _DownloadWorker | None = None
        self._download_complete = False

        self.setTitle("Downloading Models")
        self.setSubTitle("Please wait while models are downloaded.")

        layout = QVBoxLayout(self)
        self._status_label = QLabel("Preparing…")
        self._overall_bar = QProgressBar()
        self._overall_bar.setRange(0, 1)
        layout.addWidget(self._status_label)
        layout.addWidget(self._overall_bar)

    def initializePage(self) -> None:  # noqa: N802
        """Start downloading when the page becomes visible."""
        self._download_complete = False
        self.completeChanged.emit()

        select_page = self.wizard().page(1)
        if isinstance(select_page, _ModelSelectPage) and select_page.is_full:
            models = _FULL_MODELS
        else:
            models = _CPU_MODELS

        total = len(models)
        self._overall_bar.setRange(0, total)
        self._overall_bar.setValue(0)

        self._worker = _DownloadWorker(models, self._settings.hf_home)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def isComplete(self) -> bool:  # noqa: N802
        """Only allow advancing when the download is complete."""
        return self._download_complete

    def _on_progress(self, current: int, total: int, model: str) -> None:
        self._overall_bar.setValue(current)
        if model:
            self._status_label.setText(f"Downloading {model} ({current + 1}/{total})…")
        else:
            self._status_label.setText("All downloads complete.")

    def _on_finished(self) -> None:
        self._download_complete = True
        self.completeChanged.emit()

    def _on_error(self, message: str) -> None:
        self._status_label.setText(f"Error: {message}")


class _CompletePage(QWizardPage):
    """Page 4: summary and finalisation."""

    def __init__(
        self,
        settings: SettingsManager,
        parent: QWizardPage | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setTitle("Setup Complete")
        self.setSubTitle("Models have been downloaded and the pipeline is ready to use.")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("You can change model settings later in Preferences."))

    def initializePage(self) -> None:  # noqa: N802
        """Mark first-run as complete when this page is shown."""
        self._settings.first_run_done = True


# ── Main wizard ──────────────────────────────────────────────────────


class SetupWizard(QWizard):
    """First-run wizard that downloads required NLP models."""

    def __init__(
        self,
        settings: SettingsManager,
        parent: QWizard | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings

        self.setWindowTitle("MHM Pipeline — Setup")
        self.setMinimumSize(520, 380)

        self.addPage(_WelcomePage())
        self.addPage(_ModelSelectPage())
        self.addPage(_DownloadPage(settings))
        self.addPage(_CompletePage(settings))


# ── Helpers ──────────────────────────────────────────────────────────


def _free_disk_gb() -> float:
    """Return free disk space in GB for the home directory."""
    usage = shutil.disk_usage(Path.home())
    return usage.free / (1024**3)
