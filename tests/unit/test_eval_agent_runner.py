"""Unit tests for :mod:`mhm_pipeline.eval_agent_runner` (Rule 50)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mhm_pipeline import eval_agent_runner


def _make_fake_eval_agent(root: Path) -> Path:
    """Create a minimal eval-agent tree at ``root`` so locate_bundled_eval_agent
    accepts it (it just checks for ``eval_agent/cli.py``)."""
    pkg = root / "eval_agent"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text("def main(_): pass\n")
    config = root / "config"
    config.mkdir(exist_ok=True)
    (config / "default.yaml").write_text("threshold: 0.5\n")
    rubrics = config / "rubrics"
    rubrics.mkdir(exist_ok=True)
    (rubrics / "person_ner.md").write_text("# rubric\n")
    return root


class TestLocateBundledEvalAgent:
    def test_returns_explicit_path_when_it_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eval_agent_root = _make_fake_eval_agent(tmp_path / "eval-agent")

        def _stub_root() -> Path:
            return tmp_path  # parent of "eval-agent/"

        monkeypatch.setattr(eval_agent_runner, "bundled_resource_root", _stub_root)
        assert eval_agent_runner.locate_bundled_eval_agent() == eval_agent_root

    def test_raises_when_no_layout_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Point the bundled resource root at an empty subdir and also
        # patch the module-local __file__ resolver so the sibling-
        # project fallback (which walks parents of __file__) lands
        # somewhere with no eval-agent either.
        empty_root = tmp_path / "nowhere"
        empty_root.mkdir()
        monkeypatch.setattr(
            eval_agent_runner, "bundled_resource_root", lambda: empty_root
        )
        # Force the dev fallback path (Path(__file__).resolve().parents[2])
        # to a directory with no sibling eval-agent.
        sentinel = tmp_path / "fake-src" / "mhm_pipeline"
        sentinel.mkdir(parents=True)
        marker = sentinel / "platform_" / "paths.py"
        marker.parent.mkdir(parents=True)
        marker.write_text("")
        monkeypatch.setattr(
            eval_agent_runner, "__file__", str(sentinel / "eval_agent_runner.py")
        )
        with pytest.raises(FileNotFoundError):
            eval_agent_runner.locate_bundled_eval_agent()


class TestEnsureUserStateDir:
    def test_creates_per_user_dir_on_first_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundled = _make_fake_eval_agent(tmp_path / "bundled")
        user_data = tmp_path / "user-data"
        monkeypatch.setattr(eval_agent_runner, "app_data_dir", lambda: user_data)

        out = eval_agent_runner.ensure_user_state_dir(bundled)
        assert out == user_data / "eval-agent"
        assert out.exists()
        # config got copied
        assert (out / "config" / "default.yaml").read_text() == "threshold: 0.5\n"
        # state subdirs ready for the subprocess
        assert (out / "state" / "runs").exists()
        assert (out / "state" / "cache").exists()

    def test_idempotent_on_second_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundled = _make_fake_eval_agent(tmp_path / "bundled")
        user_data = tmp_path / "user-data"
        monkeypatch.setattr(eval_agent_runner, "app_data_dir", lambda: user_data)

        first = eval_agent_runner.ensure_user_state_dir(bundled)
        # Mark the user-side config so we can confirm we don't blow it away.
        (first / "config" / "user_marker.txt").write_text("alive")

        # Touch state/runs to verify it's preserved.
        (first / "state" / "runs" / "old_run_keep_me").mkdir()

        second = eval_agent_runner.ensure_user_state_dir(bundled)
        assert second == first
        assert (first / "state" / "runs" / "old_run_keep_me").exists()

    def test_re_copies_config_when_bundled_mtime_is_newer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        bundled = _make_fake_eval_agent(tmp_path / "bundled")
        user_data = tmp_path / "user-data"
        monkeypatch.setattr(eval_agent_runner, "app_data_dir", lambda: user_data)

        first = eval_agent_runner.ensure_user_state_dir(bundled)
        runs_dir = first / "state" / "runs"
        (runs_dir / "old_run_keep_me").mkdir()

        time.sleep(0.05)  # ensure mtime tick
        # Rewrite the bundled config so it gets a newer mtime than the
        # user copy.
        shutil.rmtree(bundled / "config")
        bundled_cfg = bundled / "config"
        bundled_cfg.mkdir()
        (bundled_cfg / "default.yaml").write_text("threshold: 0.99\n")

        second = eval_agent_runner.ensure_user_state_dir(bundled)
        assert second == first
        # Config refreshed.
        assert (first / "config" / "default.yaml").read_text() == "threshold: 0.99\n"
        # state/runs preserved — re-copy only touches config/.
        assert (runs_dir / "old_run_keep_me").exists()

    def test_does_not_write_to_bundled_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundled = _make_fake_eval_agent(tmp_path / "bundled")
        user_data = tmp_path / "user-data"
        monkeypatch.setattr(eval_agent_runner, "app_data_dir", lambda: user_data)

        before_files = sorted(p.name for p in bundled.rglob("*") if p.is_file())
        eval_agent_runner.ensure_user_state_dir(bundled)
        after_files = sorted(p.name for p in bundled.rglob("*") if p.is_file())
        assert before_files == after_files


class TestResolvePythonExecutable:
    def test_returns_sys_executable(self) -> None:
        import sys

        assert eval_agent_runner.resolve_python_executable() == sys.executable
