"""Orchestrates pipeline stage execution."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from mhm_pipeline.controller.workers import (
    AuthorityWorker,
    MarcParseWorker,
    NerWorker,
    RdfBuildWorker,
    ShaclValidateWorker,
    StageWorker,
    WikidataUploadWorker,
)
from mhm_pipeline.settings.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

_STAGE_NAMES: dict[int, str] = {
    0: "MARC Parse",
    1: "NER",
    2: "Authority",
    3: "RDF Build",
    4: "SHACL Validate",
    5: "Wikidata Upload",
}


class PipelineController(QObject):
    """Creates, connects, and manages pipeline stage workers.

    Signals:
      - stage_started(int): emitted when a stage begins
      - stage_finished(int, Path): emitted when a stage completes successfully
      - stage_error(int, str): emitted when a stage fails
      - stage_progress(int, int): emitted when progress updates (stage, percentage)
      - pipeline_finished(): emitted when all stages are done
    """

    stage_started = pyqtSignal(int)
    stage_finished = pyqtSignal(int, Path)
    stage_error = pyqtSignal(int, str)
    stage_progress = pyqtSignal(int, int)
    entity_status = pyqtSignal(str, str, str, str)  # Wikidata per-entity status
    pipeline_finished = pyqtSignal()

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__()
        self._settings = settings
        self._current_worker: StageWorker | None = None
        self._stage_outputs: dict[int, Path] = {}

    # ── Public API ────────────────────────────────────────────────────

    def start_stage(self, stage_index: int, **kwargs: object) -> None:
        """Create the worker for *stage_index*, wire its signals, and start it."""
        worker = self._build_worker(stage_index, **kwargs)
        self._current_worker = worker

        worker.finished.connect(partial(self._on_worker_finished, stage_index))
        worker.error.connect(partial(self._on_worker_error, stage_index))
        worker.progress.connect(partial(self._on_worker_progress, stage_index))

        # Forward entity_status from WikidataUploadWorker
        if hasattr(worker, "entity_status"):
            worker.entity_status.connect(self.entity_status)

        name = _STAGE_NAMES.get(stage_index, f"Stage {stage_index}")
        logger.info("Starting stage %d (%s)", stage_index, name)
        self.stage_started.emit(stage_index)
        worker.start()

    def _on_worker_progress(self, stage_index: int, pct: int) -> None:
        self.stage_progress.emit(stage_index, pct)

    def cancel(self) -> None:
        """Request the current worker to stop and wait for it to finish."""
        if self._current_worker is None:
            return
        logger.info("Cancelling current worker")
        self._current_worker.quit()
        self._current_worker.wait()
        self._current_worker = None

    @property
    def stage_outputs(self) -> dict[int, Path]:
        """Map of completed stage indices to their output paths."""
        return dict(self._stage_outputs)

    # ── Internal signal handlers ──────────────────────────────────────

    def _on_worker_finished(self, stage_index: int, output_path: Path) -> None:
        self._stage_outputs[stage_index] = output_path
        # Wait for QThread to fully stop before dropping reference (prevents SIGABRT)
        if self._current_worker is not None:
            self._current_worker.wait()
        self._current_worker = None
        logger.info("Stage %d finished: %s", stage_index, output_path)
        self.stage_finished.emit(stage_index, output_path)

    def _on_worker_error(self, stage_index: int, msg: str) -> None:
        if self._current_worker is not None:
            self._current_worker.wait()
        self._current_worker = None
        logger.error("Stage %d error: %s", stage_index, msg)
        self.stage_error.emit(stage_index, msg)

    # ── Worker factory ────────────────────────────────────────────────

    def _build_worker(self, stage_index: int, **kwargs: object) -> StageWorker:
        output_dir = self._settings.output_dir

        if stage_index == 0:
            if "output_dir" in kwargs:
                output_dir = Path(str(kwargs["output_dir"]))
            return MarcParseWorker(
                input_path=Path(str(kwargs["input_path"])),
                output_dir=output_dir,
                start=int(kwargs.get("start", 0)),
                end=int(kwargs.get("end", 0)),
                device=self._settings.gpu_device,
            )

        if stage_index == 1:
            if "output_dir" in kwargs:
                output_dir = Path(str(kwargs["output_dir"]))
            return NerWorker(
                input_path=self._resolve_input(0, kwargs),
                output_dir=output_dir,
                model_path=str(
                    kwargs.get("model_path", "alexgoldberg/hebrew-manuscript-joint-ner-v2")
                ),
                device=self._settings.gpu_device,
                batch_size=int(kwargs.get("batch_size", self._settings.batch_size)),
                provenance_model_path=str(kwargs.get("provenance_model_path", "")),
                contents_model_path=str(kwargs.get("contents_model_path", "")),
            )

        if stage_index == 2:
            if "output_dir" in kwargs:
                output_dir = Path(str(kwargs["output_dir"]))
            # ner_path: explicit kwarg → stage 1 output → None
            ner_path_raw = kwargs.get("ner_path")
            if ner_path_raw is not None:
                ner_path: Path | None = Path(str(ner_path_raw))
            else:
                ner_path = self._stage_outputs.get(1)
            return AuthorityWorker(
                input_path=self._resolve_input(0, kwargs),
                output_dir=output_dir,
                ner_path=ner_path,
                enable_viaf=bool(kwargs.get("enable_viaf", True)),
                enable_kima=bool(kwargs.get("enable_kima", False)),
                kima_db_path=str(kwargs.get("kima_db_path", self._settings.kima_db_path)),
                mazal_db_path=str(kwargs.get("mazal_db_path", self._settings.mazal_db_path)),
            )

        if stage_index == 3:
            if "output_dir" in kwargs:
                output_dir = Path(str(kwargs["output_dir"]))
            return RdfBuildWorker(
                input_path=self._resolve_input(0, kwargs),
                output_dir=output_dir,
                rdf_format=str(kwargs.get("rdf_format", "Turtle")),
            )

        if stage_index == 4:
            if "output_dir" in kwargs:
                output_dir = Path(str(kwargs["output_dir"]))
            return ShaclValidateWorker(
                ttl_path=self._resolve_input(3, kwargs),
                shapes_path=Path(
                    str(
                        kwargs.get("shapes_path", Path("ontology/shacl-shapes.ttl")),
                    )
                ),
                output_dir=output_dir,
            )

        if stage_index == 5:
            token = str(kwargs.get("token", ""))
            output_dir = Path(str(kwargs.get("output_dir", self._settings.output_dir)))
            return WikidataUploadWorker(
                input_path=self._resolve_input(2, kwargs),
                output_dir=output_dir,
                token=token,
                dry_run=bool(kwargs.get("dry_run", True)),
                batch_mode=bool(kwargs.get("batch_mode", False)),
            )

        raise ValueError(f"Unknown stage index: {stage_index}")

    def _resolve_input(self, prior_stage: int, kwargs: dict[str, object]) -> Path:
        """Return an explicit *input_path* kwarg, or fall back to a prior stage output."""
        explicit = kwargs.get("input_path")
        if explicit is not None:
            return Path(str(explicit))
        stored = self._stage_outputs.get(prior_stage)
        if stored is not None:
            return stored
        raise ValueError(f"No input_path provided and stage {prior_stage} has not completed")
