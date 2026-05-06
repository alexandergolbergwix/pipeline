"""Unified progress bar showing substep label, percentage, and ETA.

This widget replaces ``PercentProgressWidget`` and most uses of
``StageProgressWidget`` across the pipeline panels. It exposes a small,
deterministic API and handles three concerns the older widgets did not:

1. **Substep label** — a human-readable line ("Matching VIAF: Maimonides…")
   that is independent from progress mechanics. Updating it never resets
   the ETA history.
2. **ETA** — sliding-window mean tick rate over the last 10 progress
   updates, formatted as ``~12 s`` or ``~3 min 14 s``.
3. **Indeterminate-mode debounce** — when a worker briefly toggles the
   total to 0 we wait 100 ms before switching to the busy bar to avoid
   flicker.
"""

from __future__ import annotations

import time
from collections import deque

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFontMetrics, QGuiApplication
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme

_HISTORY_SIZE: int = 10
_ETA_MAX_SECONDS: int = 99 * 60  # clamp ceiling — anything bigger is meaningless
_INDETERMINATE_DEBOUNCE_MS: int = 100
_NBSP: str = "\u00a0"
_ETA_PLACEHOLDER: str = "—"


class DynamicProgressBar(QWidget):
    """Two-row progress widget: ``[substep […]   ~ETA]`` over a thin bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._total: int = 0
        self._current: int = 0
        self._history: deque[tuple[float, int]] = deque(maxlen=_HISTORY_SIZE)
        self._success: bool = True
        self._finished: bool = False
        self._pending_indeterminate: QTimer | None = None

        self._build_layout()
        self._apply_theme()
        self._wire_palette_signals()

    # ── Layout ────────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SPACE_SM, theme.SPACE_XS, theme.SPACE_SM, theme.SPACE_XS)
        outer.setSpacing(theme.SPACE_XS)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_SM)
        row.setContentsMargins(0, 0, 0, 0)

        self._substep = QLabel("")
        self._substep.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._substep.setLayoutDirection(Qt.LayoutDirection.LayoutDirectionAuto)
        self._substep.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # QLabel does not have setTextElideMode, so we elide manually in
        # paintEvent. To keep this widget under 200 LOC we instead use the
        # built-in elision via setMinimumWidth + setWordWrap(False) and let
        # Qt's QStyle elide the text on render. For sturdiness we also set
        # a sensible minimum width so very long Hebrew names truncate
        # rather than push the ETA off-screen.
        self._substep.setWordWrap(False)
        self._substep.setMinimumWidth(80)
        row.addWidget(self._substep, stretch=1)

        self._eta = QLabel("")
        self._eta.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._eta.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._eta.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        row.addWidget(self._eta, stretch=0)

        outer.addLayout(row)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(10)
        outer.addWidget(self._bar)

        self.setMinimumHeight(46)
        self.setMaximumHeight(56)

    # ── Theme ─────────────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        # Substep: bold, primary text colour
        self._substep.setStyleSheet(
            f"QLabel {{"
            f" color: {theme.ui('text')};"
            f" font-size: {theme.FONT_SM}px;"
            f" font-weight: {theme.WEIGHT_SEMIBOLD};"
            f"}}"
        )
        # ETA: subtext colour, monospace, fixed width based on widest label
        mono = theme.FONT_STACK_MONO
        self._eta.setStyleSheet(
            f"QLabel {{"
            f" color: {theme.ui('subtext')};"
            f" font-size: {theme.FONT_XS}px;"
            f" font-family: {mono};"
            f"}}"
        )
        metrics = QFontMetrics(self._eta.font())
        self._eta.setFixedWidth(metrics.horizontalAdvance("~99 min 59 s") + 8)

        # Surface — try the helper if present; otherwise fall back to ui tokens.
        surface = ""
        helper = getattr(theme, "glass_surface_style", None)
        frost = getattr(theme, "GLASS_FROST_THIN", None)
        if callable(helper) and frost is not None:
            try:
                surface = helper(frost=frost, selector="QWidget#dynamicProgressBar")
            except Exception:
                surface = ""
        self.setObjectName("dynamicProgressBar")
        chunk_color = (
            theme.severity("success").text if self._success else theme.severity("violation").text
        )
        bar_qss = (
            f"QProgressBar {{"
            f" background: {theme.ui('panel_bg')};"
            f" border: 1px solid {theme.ui('border')};"
            f" border-radius: {theme.RADIUS_SM}px;"
            f"}}"
            f"QProgressBar::chunk {{"
            f" background: {chunk_color};"
            f" border-radius: {theme.RADIUS_SM}px;"
            f"}}"
        )
        self._bar.setStyleSheet(bar_qss)
        self.setStyleSheet(surface)

    def _wire_palette_signals(self) -> None:
        try:
            hints = QGuiApplication.styleHints()
        except Exception:
            return
        signal = getattr(hints, "colorSchemeChanged", None)
        if signal is None:
            return
        try:
            signal.connect(self._on_color_scheme_changed)
        except Exception:
            pass

    def _on_color_scheme_changed(self, _scheme: object) -> None:
        theme.invalidate_cache()
        self._apply_theme()

    # ── Public API ────────────────────────────────────────────────────

    def set_total(self, total: int) -> None:
        """Set the total tick count.

        ``total <= 0`` switches the bar to indeterminate mode, debounced by
        100 ms so a transient zero from a worker that hasn't computed the
        total yet doesn't cause a flicker.
        """
        if total <= 0:
            self._schedule_indeterminate()
            return
        self._cancel_indeterminate()
        self._total = total
        self._bar.setRange(0, max(1, total))
        self._eta.show()

    def set_substep(self, label: str) -> None:
        """Update the substep label without touching the ETA history."""
        metrics = QFontMetrics(self._substep.font())
        elided = metrics.elidedText(
            label, Qt.TextElideMode.ElideMiddle, max(60, self._substep.width() - 4)
        )
        self._substep.setText(elided)
        self._substep.setToolTip(label if elided != label else "")

    def set_progress(self, current: int, total: int | None = None) -> None:
        """Push a new progress sample and recompute the ETA."""
        if total is not None:
            self.set_total(total)
        if self._total <= 0:
            return
        clamped = max(0, min(int(current), int(self._total)))
        self._current = clamped
        self._history.append((time.monotonic(), clamped))
        self._bar.setValue(clamped)
        if clamped >= self._total:
            self._eta.setText("")
            return
        self._eta.setText(self._format_eta())

    def reset(self) -> None:
        """Restore the widget to its initial blank state."""
        self._cancel_indeterminate()
        self._history.clear()
        self._total = 0
        self._current = 0
        self._success = True
        self._finished = False
        self._substep.setText("")
        self._substep.setToolTip("")
        self._eta.setText("")
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._apply_theme()

    def finish(self, success_label: str = "Done", *, success: bool = True) -> None:
        """Snap to 100 % and show *success_label*; switch chunk colour on failure."""
        self._cancel_indeterminate()
        self._finished = True
        self._success = success
        if self._bar.maximum() <= 0:
            self._bar.setRange(0, 100)
        self._bar.setValue(self._bar.maximum())
        self._substep.setText(success_label)
        self._eta.setText("")
        self._apply_theme()

    # ── Internals ─────────────────────────────────────────────────────

    def _schedule_indeterminate(self) -> None:
        if self._total == 0 and self._bar.maximum() == 0:
            return  # already indeterminate
        if self._pending_indeterminate is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._activate_indeterminate)
        timer.start(_INDETERMINATE_DEBOUNCE_MS)
        self._pending_indeterminate = timer

    def _cancel_indeterminate(self) -> None:
        if self._pending_indeterminate is not None:
            self._pending_indeterminate.stop()
            self._pending_indeterminate.deleteLater()
            self._pending_indeterminate = None

    def _activate_indeterminate(self) -> None:
        self._pending_indeterminate = None
        self._total = 0
        self._bar.setRange(0, 0)
        self._eta.hide()

    def _format_eta(self) -> str:
        if len(self._history) < 2:
            return _ETA_PLACEHOLDER
        t0, c0 = self._history[0]
        t1, c1 = self._history[-1]
        elapsed = t1 - t0
        ticks = c1 - c0
        if elapsed <= 0 or ticks <= 0:
            return _ETA_PLACEHOLDER
        mean_tick_seconds = elapsed / ticks
        remaining_ticks = max(0, self._total - self._current)
        remaining = remaining_ticks * mean_tick_seconds
        remaining = max(0.0, min(float(_ETA_MAX_SECONDS), remaining))
        return self._format_seconds(remaining)

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        secs_total = int(round(seconds))
        if secs_total < 60:
            return f"~{secs_total}{_NBSP}s"
        minutes = secs_total // 60
        secs = secs_total % 60
        return f"~{minutes}{_NBSP}min{_NBSP}{secs}{_NBSP}s"


# ─────────────────────────────────────────────────────────────────────────────
# Wiring helper — keeps panels DRY
# ─────────────────────────────────────────────────────────────────────────────


def connect_progress_signals(
    bar: "DynamicProgressBar",
    worker: object,
    *,
    success_label: str = "Done",
    failure_label: str = "Failed",
) -> None:
    """Wire a worker's standard signals to a :class:`DynamicProgressBar`.

    Eliminates the four-line connect block that would otherwise repeat in
    every panel that owns a ``DynamicProgressBar`` and a ``StageWorker``.
    Callers shrink to::

        bar = DynamicProgressBar()
        connect_progress_signals(bar, worker)

    Connections established (silently skipped when the signal is absent —
    not every worker emits every signal, and no panel should crash on
    that):

    * ``worker.progress(int)`` →  ``bar.set_progress(value)``
    * ``worker.substep(str)`` →  ``bar.set_substep(label)``
    * ``worker.finished(...)`` →  ``bar.finish(success_label, success=True)``
    * ``worker.error(str)`` →  ``bar.finish(failure_label, success=False)``

    Use ``success_label`` / ``failure_label`` to override the default
    end-of-job text per panel (e.g. ``"Stage 3 complete"``).
    """

    def _connect(signal_name: str, slot: object) -> None:
        sig = getattr(worker, signal_name, None)
        if sig is None:
            return
        connect = getattr(sig, "connect", None)
        if not callable(connect):
            return
        connect(slot)

    _connect("progress", lambda value: bar.set_progress(int(value)))
    _connect("substep", bar.set_substep)
    _connect(
        "finished",
        lambda *_args: bar.finish(success_label, success=True),
    )
    _connect(
        "error",
        lambda *_args: bar.finish(failure_label, success=False),
    )
