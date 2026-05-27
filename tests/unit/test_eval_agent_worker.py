"""Unit tests for :class:`EvalAgentWorker` (Rule 50).

Subprocess is mocked end-to-end so we never touch the real Gemini API
or the bundled eval-agent. We verify:

* The worker refuses to run without a Gemini key (emits ``error`` with
  the canonical message that routes the user to Credentials).
* The worker refuses to run when the input dir is missing required
  files.
* It builds the right subprocess argv, ``cwd``, and ``env`` (Gemini
  key only in the env block, never on the command line).
* It parses ``[STEP] …`` and ``[PROGRESS] N`` stdout lines into
  ``substep`` / ``progress`` signal emissions.
* It emits ``finished(<run_dir>)`` on exit code 0.
* It emits ``error`` on non-zero exit code.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mhm_pipeline.controller.workers import EvalAgentWorker


@pytest.fixture
def fake_pipeline_out(tmp_path: Path) -> Path:
    """Create a temp dir holding the two files EvalAgentWorker requires."""
    out = tmp_path / "pipeline-out"
    out.mkdir()
    (out / "marc_extracted.json").write_text("[]")
    (out / "ner_results.json").write_text("[]")
    return out


@pytest.fixture
def fake_user_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make ``ensure_user_state_dir`` return a writable tmp dir."""
    user_state = tmp_path / "user-state"
    (user_state / "state" / "runs").mkdir(parents=True)
    (user_state / "state" / "cache").mkdir(parents=True)
    from mhm_pipeline import eval_agent_runner

    monkeypatch.setattr(eval_agent_runner, "ensure_user_state_dir", lambda _r=None: user_state)
    monkeypatch.setattr(
        eval_agent_runner, "locate_bundled_eval_agent",
        lambda: tmp_path / "bundled-eval-agent",
    )
    return user_state


class _FakeProc:
    """Minimal subprocess.Popen stand-in."""

    def __init__(self, stdout_lines: list[str], returncode: int = 0) -> None:
        self.stdout = io.StringIO("".join(line + "\n" for line in stdout_lines))
        self._rc = returncode

    def wait(self) -> int:
        return self._rc


class TestEvalAgentWorkerInputValidation:
    def test_missing_key_emits_error(
        self, fake_pipeline_out: Path
    ) -> None:
        worker = EvalAgentWorker(fake_pipeline_out, gemini_api_key="")
        captured: list[str] = []
        worker.error.connect(lambda msg: captured.append(msg))
        worker.run()
        assert captured, "expected an error emission"
        assert "Gemini API key is required" in captured[0]
        assert "Credentials" in captured[0]

    def test_missing_inputs_emits_error(
        self, tmp_path: Path
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        worker = EvalAgentWorker(empty, gemini_api_key="AIzaX")
        captured: list[str] = []
        worker.error.connect(lambda msg: captured.append(msg))
        worker.run()
        assert captured
        assert "marc_extracted.json" in captured[0]
        assert "ner_results.json" in captured[0]


class TestEvalAgentWorkerSubprocessShape:
    def test_subprocess_args_contain_pipeline_output(
        self, fake_pipeline_out: Path, fake_user_state: Path
    ) -> None:
        worker = EvalAgentWorker(fake_pipeline_out, gemini_api_key="AIzaTEST")

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc(stdout_lines=[])
            # Make _latest_run_dir return a fake to short-circuit the
            # success branch — but the test only cares about argv.
            from mhm_pipeline.controller import workers as workers_mod

            fake_run = fake_user_state / "state" / "runs" / "fake-run"
            fake_run.mkdir()
            with patch.object(workers_mod, "_latest_run_dir", return_value=fake_run):
                worker.run()

        assert popen.call_count == 1
        call_kwargs = popen.call_args.kwargs
        argv = popen.call_args.args[0]
        assert "eval_agent.cli" in argv
        assert "run" in argv
        assert "--pipeline-output" in argv
        assert str(fake_pipeline_out) in argv
        # API key MUST NOT appear in argv (it goes via the env block).
        assert "AIzaTEST" not in argv
        assert call_kwargs["env"]["GEMINI_API_KEY"] == "AIzaTEST"
        assert call_kwargs["cwd"] == str(fake_user_state)

    def test_default_use_cache_omits_no_cache_flag(
        self, fake_pipeline_out: Path, fake_user_state: Path
    ) -> None:
        """Default: cache is enabled. ``--no-cache`` must NOT appear."""
        worker = EvalAgentWorker(fake_pipeline_out, gemini_api_key="AIzaTEST")

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc(stdout_lines=[])
            from mhm_pipeline.controller import workers as workers_mod

            fake_run = fake_user_state / "state" / "runs" / "fake-run"
            fake_run.mkdir()
            with patch.object(workers_mod, "_latest_run_dir", return_value=fake_run):
                worker.run()

        argv = popen.call_args.args[0]
        assert "--no-cache" not in argv

    def test_use_cache_false_appends_no_cache_flag(
        self, fake_pipeline_out: Path, fake_user_state: Path
    ) -> None:
        """``use_cache=False`` → ``--no-cache`` lands on argv."""
        worker = EvalAgentWorker(
            fake_pipeline_out, gemini_api_key="AIzaTEST", use_cache=False
        )

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc(stdout_lines=[])
            from mhm_pipeline.controller import workers as workers_mod

            fake_run = fake_user_state / "state" / "runs" / "fake-run"
            fake_run.mkdir()
            with patch.object(workers_mod, "_latest_run_dir", return_value=fake_run):
                worker.run()

        argv = popen.call_args.args[0]
        assert "--no-cache" in argv


    def test_no_model_flags_by_default(
        self, fake_pipeline_out: Path, fake_user_state: Path
    ) -> None:
        """No model overrides → neither flag appears on argv."""
        worker = EvalAgentWorker(fake_pipeline_out, gemini_api_key="AIzaTEST")

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc(stdout_lines=[])
            from mhm_pipeline.controller import workers as workers_mod

            fake_run = fake_user_state / "state" / "runs" / "fake-run"
            fake_run.mkdir()
            with patch.object(workers_mod, "_latest_run_dir", return_value=fake_run):
                worker.run()

        argv = popen.call_args.args[0]
        assert "--tier-model" not in argv
        assert "--escalate-model" not in argv

    def test_model_flags_land_on_argv(
        self, fake_pipeline_out: Path, fake_user_state: Path
    ) -> None:
        """tier_model + escalate_model → ``--tier-model X --escalate-model Y``."""
        worker = EvalAgentWorker(
            fake_pipeline_out,
            gemini_api_key="AIzaTEST",
            tier_model="gemini-3-flash",
            escalate_model="gemini-3-pro",
        )

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc(stdout_lines=[])
            from mhm_pipeline.controller import workers as workers_mod

            fake_run = fake_user_state / "state" / "runs" / "fake-run"
            fake_run.mkdir()
            with patch.object(workers_mod, "_latest_run_dir", return_value=fake_run):
                worker.run()

        argv = popen.call_args.args[0]
        assert "--tier-model" in argv
        assert argv[argv.index("--tier-model") + 1] == "gemini-3-flash"
        assert "--escalate-model" in argv
        assert argv[argv.index("--escalate-model") + 1] == "gemini-3-pro"


class TestEvalAgentWorkerStdoutParsing:
    def test_step_lines_become_substep_emissions(
        self, fake_pipeline_out: Path, fake_user_state: Path
    ) -> None:
        worker = EvalAgentWorker(fake_pipeline_out, gemini_api_key="AIzaTEST")
        substeps: list[str] = []
        worker.substep.connect(lambda s: substeps.append(s))

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc(
                stdout_lines=[
                    "[STEP] Loading rubrics",
                    "[STEP] Judging 143 candidates",
                ],
            )
            from mhm_pipeline.controller import workers as workers_mod

            fake_run = fake_user_state / "state" / "runs" / "r1"
            fake_run.mkdir()
            with patch.object(workers_mod, "_latest_run_dir", return_value=fake_run):
                worker.run()

        assert "Loading rubrics" in substeps
        assert "Judging 143 candidates" in substeps

    def test_progress_lines_emit_progress(
        self, fake_pipeline_out: Path, fake_user_state: Path
    ) -> None:
        worker = EvalAgentWorker(fake_pipeline_out, gemini_api_key="AIzaTEST")
        pcts: list[int] = []
        worker.progress.connect(lambda p: pcts.append(p))

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc(
                stdout_lines=["[PROGRESS] 25", "[PROGRESS] 75"],
            )
            from mhm_pipeline.controller import workers as workers_mod

            fake_run = fake_user_state / "state" / "runs" / "r1"
            fake_run.mkdir()
            with patch.object(workers_mod, "_latest_run_dir", return_value=fake_run):
                worker.run()

        assert 25 in pcts
        assert 75 in pcts


class TestEvalAgentWorkerLifecycle:
    def test_emits_finished_with_run_dir_on_success(
        self, fake_pipeline_out: Path, fake_user_state: Path
    ) -> None:
        worker = EvalAgentWorker(fake_pipeline_out, gemini_api_key="AIzaTEST")
        captured: list[Path] = []
        worker.finished.connect(lambda p: captured.append(p))

        run_dir = fake_user_state / "state" / "runs" / "winning-run"
        run_dir.mkdir()
        (run_dir / "report.md").write_text("# report\n")

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc(stdout_lines=[], returncode=0)
            from mhm_pipeline.controller import workers as workers_mod

            with patch.object(workers_mod, "_latest_run_dir", return_value=run_dir):
                worker.run()

        assert captured == [run_dir]

    def test_emits_error_on_nonzero_exit_code(
        self, fake_pipeline_out: Path, fake_user_state: Path
    ) -> None:
        worker = EvalAgentWorker(fake_pipeline_out, gemini_api_key="AIzaTEST")
        errors: list[str] = []
        worker.error.connect(lambda msg: errors.append(msg))

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc(stdout_lines=[], returncode=17)
            worker.run()

        assert errors
        assert "exit code 17" in errors[0]
