"""Orchestrates pipeline stage execution."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from mhm_pipeline.controller.workers import (
    AuthorityWorker,
    EvalAgentWorker,
    MarcParseWorker,
    NerWorker,
    RdfBuildWorker,
    ShaclValidateWorker,
    StageWorker,
    WikidataUploadWorker,
)
from mhm_pipeline.platform_.gpu import get_device
from mhm_pipeline.settings.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

_STAGE_NAMES: dict[int, str] = {
    # Rule 53 (2026-05-25) — user-facing log lines use the real stage
    # names instead of "Stage N". Names match the sidebar labels in
    # main_window._STAGE_LABELS so log + sidebar stay in lock-step.
    0: "MARC Parsing",
    1: "AI-based Enrichment",
    2: "Authority Matching",
    3: "RDF Graph",
    4: "SHACL Validation",
    5: "Wikidata Studio",
}

# Rule 50 — the eval-agent verification step piggybacks on the
# StageWorker lifecycle but does NOT consume a numbered stage slot;
# it's an optional post-Stage-2 audit. ``-1`` is the sentinel routed
# through the existing stage_* signals so the GUI's progress + log
# wiring transparently picks it up.
EVAL_AGENT_STAGE_INDEX = -1
# Rule 53 — friendly name for the sentinel so log lines read
# "AI agent verification …" instead of "Stage 0 …".
_STAGE_NAMES[EVAL_AGENT_STAGE_INDEX] = "AI agent verification"


def stage_display_name(stage_index: int) -> str:
    """Return the user-facing name for ``stage_index``.

    Falls back to a generic ``"Operation"`` label (never ``"Stage N"``)
    when an unregistered index slips through — Rule 53 forbids the
    numeric-stage idiom anywhere in user-visible strings.
    """
    return _STAGE_NAMES.get(stage_index, "Operation")


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
    stage_substep = pyqtSignal(int, str)  # (stage_index, substep label) — DynamicProgressBar
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
        worker.substep.connect(partial(self._on_worker_substep, stage_index))

        # Forward entity_status from WikidataUploadWorker
        if hasattr(worker, "entity_status"):
            worker.entity_status.connect(self.entity_status)

        name = _STAGE_NAMES.get(stage_index, f"Stage {stage_index}")
        logger.info("Starting stage %d (%s)", stage_index, name)
        self.stage_started.emit(stage_index)
        worker.start()

    def _on_worker_progress(self, stage_index: int, pct: int) -> None:
        self.stage_progress.emit(stage_index, pct)

    def _on_worker_substep(self, stage_index: int, label: str) -> None:
        self.stage_substep.emit(stage_index, label)

    def mark_stage_complete(self, stage_index: int, output_path: Path) -> None:
        """Mark an interactive (no-worker) stage as complete and emit finished."""
        self._stage_outputs[stage_index] = output_path
        self.stage_finished.emit(stage_index, output_path)

    def build_eval_agent_worker(
        self,
        pipeline_output_dir: Path,
        gemini_api_key: str,
        models: set[str] | None = None,
        use_cache: bool = True,
        tier_model: str | None = None,
        escalate_model: str | None = None,
        eval_target: str = "stage2",
    ) -> EvalAgentWorker:
        """Construct (but do NOT start) the eval-agent worker.

        Split from :meth:`start_eval_agent` (Rule 52) so the GUI can
        build the worker, hand it to :class:`AiVerificationDialog` to
        wire signals + open the dialog, and only THEN call
        :meth:`_start_worker` to actually fire the QThread. This avoids
        the race where the worker starts emitting signals before the
        dialog has subscribed to them.

        ``use_cache=False`` passes ``--no-cache`` to the eval-agent so
        every prediction hits Gemini fresh, ignoring any verdict
        cached from a prior run.
        """
        return EvalAgentWorker(
            pipeline_output_dir=pipeline_output_dir,
            gemini_api_key=gemini_api_key,
            models=models,
            use_cache=use_cache,
            tier_model=tier_model,
            escalate_model=escalate_model,
            eval_target=eval_target,
        )

    def _start_worker(self, worker: EvalAgentWorker, stage_index: int) -> None:
        """Wire the controller's stage_* signals and start the QThread.

        Internal helper used by :meth:`start_eval_agent` and by
        callers that built the worker via :meth:`build_eval_agent_worker`
        and need to defer the start until after a dialog is wired up.
        """
        self._current_worker = worker
        worker.finished.connect(partial(self._on_worker_finished, stage_index))
        worker.error.connect(partial(self._on_worker_error, stage_index))
        worker.progress.connect(partial(self._on_worker_progress, stage_index))
        worker.substep.connect(partial(self._on_worker_substep, stage_index))
        self.stage_started.emit(stage_index)
        worker.start()

    def start_eval_agent(
        self,
        pipeline_output_dir: Path,
        gemini_api_key: str,
        models: set[str] | None = None,
        use_cache: bool = True,
        tier_model: str | None = None,
        escalate_model: str | None = None,
        eval_target: str = "stage2",
    ) -> EvalAgentWorker:
        """Launch the bundled eval-agent over the Stage 2 output dir (Rule 50).

        Reuses the existing :class:`StageWorker` lifecycle wiring so
        progress + substep + log lines + finished/error route through
        ``stage_progress`` / ``stage_substep`` / ``stage_finished`` /
        ``stage_error`` with ``stage_index = EVAL_AGENT_STAGE_INDEX``.
        The main window's panel listens on that sentinel value and
        opens the eval-agent report dialog on ``stage_finished``.

        Returns the live :class:`EvalAgentWorker` so the caller can pass
        it to :class:`AiVerificationDialog` (Rule 52). Callers that want
        to subscribe to worker signals BEFORE the QThread starts should
        use :meth:`build_eval_agent_worker` + :meth:`_start_worker`
        instead.

        Parameters
        ----------
        pipeline_output_dir:
            Directory containing ``marc_extracted.json`` +
            ``ner_results.json`` (the Stage 2 output dir).
        gemini_api_key:
            Pulled from :attr:`SettingsManager.gemini_api_key` by the
            caller. Empty string lets the worker emit a clear error
            that routes the user to Settings → Credentials.
        models:
            Optional include-set of evaluator ids to run. ``None``
            runs every registered evaluator.
        """
        worker = self.build_eval_agent_worker(
            pipeline_output_dir=pipeline_output_dir,
            gemini_api_key=gemini_api_key,
            models=models,
            use_cache=use_cache,
            tier_model=tier_model,
            escalate_model=escalate_model,
            eval_target=eval_target,
        )
        logger.info(
            "Starting eval-agent verification on %s (use_cache=%s)",
            pipeline_output_dir,
            use_cache,
        )
        self._start_worker(worker, EVAL_AGENT_STAGE_INDEX)
        return worker

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
                start=int(str(kwargs.get("start", 0))),
                end=int(str(kwargs.get("end", 0))),
                device=get_device(self._settings.gpu_device),
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
                device=get_device(self._settings.gpu_device),
                batch_size=int(str(kwargs.get("batch_size", self._settings.batch_size))),
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
            # Stage 3 (was Stage 4) — RDF build from authority-enriched data.
            # RdfBuildWorker prefers authority_enriched_reviewed.json when it
            # exists beside authority_enriched.json, and rejects Wikidata Studio
            # review-state JSON.
            if "output_dir" in kwargs:
                output_dir = Path(str(kwargs["output_dir"]))
            return RdfBuildWorker(
                input_path=self._resolve_input(2, kwargs),
                output_dir=output_dir,
                rdf_format=str(kwargs.get("rdf_format", "Turtle")),
            )

        if stage_index == 4:
            # Stage 4 (was Stage 5) — SHACL Validate
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
            # Stage 5 (was Stage 6) — Wikidata projection/upload. Prefer the
            # Stage 3 HMO RDF output, but accept explicit Studio inputs and
            # pre-approved items.
            token = str(kwargs.get("token", ""))
            output_dir = Path(str(kwargs.get("output_dir", self._settings.output_dir)))
            if kwargs.get("input_path") is not None:
                input_path = Path(str(kwargs["input_path"]))
            elif self._stage_outputs.get(3) is not None:
                input_path = self._stage_outputs[3]
            else:
                input_path = self._resolve_input(2, kwargs)
            approved_raw = kwargs.get("approved_items")
            approved_items = approved_raw if isinstance(approved_raw, list) else None
            return WikidataUploadWorker(
                input_path=input_path,
                output_dir=output_dir,
                token=token,
                dry_run=bool(kwargs.get("dry_run", True)),
                batch_mode=bool(kwargs.get("batch_mode", False)),
                approved_items=approved_items,
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
