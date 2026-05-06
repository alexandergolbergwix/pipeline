"""Integration-test fixtures for the MHM Pipeline.

The autouse :func:`_drain_qthreads` fixture below addresses a recurring
pytest-qt leak: tests call ``qtbot.waitSignal(worker.finished)`` and
return as soon as the signal fires, but the underlying ``QThread`` is
still transitioning from *running* to *finished* state. The next test
starts another worker on the same ``QApplication`` and the two QThreads
deadlock on Qt's signal-delivery locks.

CLAUDE.md Rule 16 already mandates ``worker.wait()`` before dropping a
``QThread`` reference in production (``PipelineController`` does this).
The tests never followed the same rule. This fixture enforces it
automatically by enumerating every live ``QThread`` at test teardown
and joining each one with a 5-second budget. After the budget expires
the thread is told to ``quit()`` and waited again — so a runaway test
never blocks the suite for more than ~10 s.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _drain_qthreads() -> Iterator[None]:
    """Wait for every ``QThread`` created during the test to terminate."""
    yield
    try:
        from PyQt6.QtCore import QThread
    except ImportError:
        return

    # Snapshot live QThreads other than the main thread — Qt doesn't
    # expose a public registry, so we walk Python's GC to find them.
    import gc

    threads: list[QThread] = [
        obj
        for obj in gc.get_objects()
        if isinstance(obj, QThread) and obj is not QThread.currentThread()
    ]
    for t in threads:
        try:
            if t.isRunning():
                # Polite first
                if not t.wait(5_000):
                    t.quit()
                    if not t.wait(5_000):
                        logger.warning(
                            "QThread %r did not terminate after quit() "
                            "+ wait(5s); skipping",
                            t,
                        )
        except RuntimeError:
            # Underlying C++ object already deleted — nothing to wait on.
            continue
