"""Tests for cross-platform app startup helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from mhm_pipeline import app


def test_append_crash_log_uses_platform_log_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Crash logs should go through the shared platform log directory helper."""
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(app, "app_log_dir", lambda: log_dir)

    app._append_crash_log("traceback line")

    crash_log = log_dir / "crash.log"
    assert crash_log.exists()
    assert "traceback line" in crash_log.read_text(encoding="utf-8")


def test_append_crash_log_appends_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Multiple crashes should accumulate in the same file."""
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(app, "app_log_dir", lambda: log_dir)

    app._append_crash_log("first traceback")
    app._append_crash_log("second traceback")

    crash_log = log_dir / "crash.log"
    content = crash_log.read_text(encoding="utf-8")
    assert "first traceback" in content
    assert "second traceback" in content
