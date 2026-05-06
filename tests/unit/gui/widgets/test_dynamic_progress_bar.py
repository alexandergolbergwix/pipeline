"""Unit tests for ``DynamicProgressBar``."""

from __future__ import annotations

import os

import pytest

# Force offscreen rendering — these tests run headless.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui.widgets.dynamic_progress_bar import DynamicProgressBar  # noqa: E402


@pytest.fixture()
def widget(qtbot: object) -> DynamicProgressBar:
    """Build a fresh widget on the offscreen platform."""
    if QApplication.instance() is None:
        QApplication([])
    bar = DynamicProgressBar()
    qtbot.addWidget(bar)  # type: ignore[attr-defined]
    bar.show()
    return bar


def test_initial_state_shows_zero_percent_and_blank_labels(widget: DynamicProgressBar) -> None:
    assert widget._bar.value() == 0
    assert widget._bar.maximum() == 100
    assert widget._substep.text() == ""
    assert widget._eta.text() == ""


def test_set_progress_updates_bar_value_and_percent_text(widget: DynamicProgressBar) -> None:
    widget.set_total(200)
    widget.set_progress(50)
    assert widget._bar.value() == 50
    assert widget._bar.maximum() == 200


def test_substep_label_change_does_not_reset_eta_history(
    widget: DynamicProgressBar, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = [1000.0]
    monkeypatch.setattr(
        "mhm_pipeline.gui.widgets.dynamic_progress_bar.time.monotonic",
        lambda: counter[0],
    )
    widget.set_total(100)
    widget.set_progress(10)
    counter[0] += 5.0
    widget.set_substep("Doing something different")
    counter[0] += 5.0
    widget.set_progress(20)
    # ETA must have been computed (not blank, not the insufficient-data dash)
    assert widget._eta.text() not in ("", "—")
    # history kept both samples
    assert len(widget._history) == 2


def test_eta_format_minutes_and_seconds_for_long_remaining(
    widget: DynamicProgressBar, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = [0.0]
    monkeypatch.setattr(
        "mhm_pipeline.gui.widgets.dynamic_progress_bar.time.monotonic",
        lambda: counter[0],
    )
    widget.set_total(1000)
    widget.set_progress(100)
    counter[0] += 19.4  # 19.4 s for 80 ticks → 0.2425 s/tick
    widget.set_progress(180)
    # Remaining 820 ticks × 0.2425 s = ~198.85 s → ~3 min 19 s
    text = widget._eta.text()
    assert "min" in text
    assert text.startswith("~")
    assert text.endswith("s")


def test_eta_format_seconds_only_under_60s(
    widget: DynamicProgressBar, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = [0.0]
    monkeypatch.setattr(
        "mhm_pipeline.gui.widgets.dynamic_progress_bar.time.monotonic",
        lambda: counter[0],
    )
    widget.set_total(100)
    widget.set_progress(50)
    counter[0] += 10.0  # 10 s for 30 ticks → 1/3 s/tick
    widget.set_progress(80)
    # Remaining 20 × 1/3 = 6.67 s
    text = widget._eta.text()
    assert "min" not in text
    assert text.startswith("~")
    assert text.endswith("s")


def test_zero_total_switches_to_indeterminate_range(
    widget: DynamicProgressBar, qtbot: object
) -> None:
    widget.set_total(0)
    # Wait for the 100ms debounce timer to fire
    qtbot.wait(180)  # type: ignore[attr-defined]
    assert widget._bar.maximum() == 0


def test_finish_sets_100_percent_and_success_label_and_color(
    widget: DynamicProgressBar,
) -> None:
    widget.set_total(100)
    widget.set_progress(50)
    widget.finish("All done", success=True)
    assert widget._bar.value() == widget._bar.maximum()
    assert widget._substep.text() == "All done"
    assert widget._eta.text() == ""
    qss_success = widget._bar.styleSheet()

    widget.reset()
    widget.set_total(100)
    widget.finish("Failed", success=False)
    assert widget._substep.text() == "Failed"
    qss_fail = widget._bar.styleSheet()
    # Success and failure must paint different chunk colours
    assert qss_success != qss_fail


def test_reset_clears_eta_history_and_restores_determinate_range(
    widget: DynamicProgressBar,
) -> None:
    widget.set_total(100)
    widget.set_progress(60)
    widget.set_substep("middle")
    widget.reset()
    assert widget._bar.maximum() == 100
    assert widget._bar.value() == 0
    assert widget._substep.text() == ""
    assert widget._eta.text() == ""
    assert len(widget._history) == 0
