"""End-to-end integration tests for the eval-agent state-dir contract.

These exercise the REAL bundled eval-agent CLI (via the
``tests/fixtures/eval_agent_test_runner.py`` wrapper which stubs out
the Gemini judge) and assert the env-var + flag contract the MHM
Pipeline depends on.

Background — live bug from 2026-05-25
-------------------------------------

The eval-agent ignored ``EVAL_AGENT_STATE_DIR``, so the MHM Pipeline
subprocess wrote its run dir to the wrong location and the GUI
silently erred. Fix landed in eval-agent commit ``81de1a3``: env var
is now honored, ``--state-dir`` flag is now accepted.

The previous integration test
(:class:`TestEvalAgentWorkerAgainstFakeCli` in
``test_credentials_and_eval_agent.py``) used a HAND-WRITTEN fake CLI
that by coincidence wrote to ``Path.cwd() / "state" / "runs"`` — which
matches the worker's expectation, NOT the real CLI's pre-fix
behaviour. These tests close that gap by driving the REAL CLI.

Marker
------

All tests in this module are marked ``slow_models`` so CI's default
``-m "not slow_models"`` selector skips them — they shell out to a
real Python subprocess that imports the full eval-agent stack
(~5 s startup). Run them explicitly with ``pytest -m slow_models``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ── Module-level skip if eval-agent sibling isn't present ────────────

_EVAL_AGENT_ROOT = Path("/Users/alexandergo/Documents/Doctorat/eval-agent")
_WRAPPER = Path(__file__).resolve().parents[1] / "fixtures" / "eval_agent_test_runner.py"

if not _EVAL_AGENT_ROOT.is_dir():
    pytest.skip(
        f"eval-agent sibling repo not present at {_EVAL_AGENT_ROOT}; "
        "state-dir contract tests need the real CLI.",
        allow_module_level=True,
    )
if not _WRAPPER.is_file():
    pytest.skip(
        f"eval-agent test runner wrapper missing at {_WRAPPER}",
        allow_module_level=True,
    )


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def bundled_eval_agent_root() -> Path:
    """The dev-tree sibling path to the eval-agent project."""
    return _EVAL_AGENT_ROOT


@pytest.fixture
def fake_marc_and_ner(tmp_path: Path) -> Path:
    """Write minimal but real-shape ``marc_extracted.json`` +
    ``ner_results.json`` so the eval-agent has at least one candidate
    above threshold."""
    pipeline_out = tmp_path / "pipeline-out"
    pipeline_out.mkdir()
    marc_records = [
        {
            "_control_number": "99001",
            "title": "Test Manuscript",
            "authors": ["Moshe"],
            "contributors": [],
            "provenance": [],
            "notes": [],
            "colophon_text": "",
            "data_from_colophon": "",
        }
    ]
    ner_records = [
        {
            "_control_number": "99001",
            "text": "Moshe",
            "entities": [
                {
                    "source": "person_ner",
                    "person": "Moshe",
                    "role": "AUTHOR",
                    "confidence": 0.99,
                    "model_confidence": 0.99,
                    "start": 0,
                    "end": 5,
                }
            ],
        }
    ]
    (pipeline_out / "marc_extracted.json").write_text(
        json.dumps(marc_records), encoding="utf-8"
    )
    (pipeline_out / "ner_results.json").write_text(
        json.dumps(ner_records), encoding="utf-8"
    )
    return pipeline_out


def _make_state_dir(parent: Path, name: str) -> Path:
    state = parent / name
    (state / "cache").mkdir(parents=True)
    (state / "runs").mkdir(parents=True)
    return state


def _run_wrapper(
    *,
    pipeline_out: Path,
    state_dir_env: Path | None,
    state_dir_flag: Path | None,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Spawn the real eval-agent CLI via the stub-judge wrapper."""
    env = dict(os.environ)
    env["GEMINI_API_KEY"] = "stub-key"
    env["PYTHONPATH"] = str(_EVAL_AGENT_ROOT)
    if state_dir_env is not None:
        env["EVAL_AGENT_STATE_DIR"] = str(state_dir_env)
    else:
        env.pop("EVAL_AGENT_STATE_DIR", None)

    cmd: list[str] = [
        sys.executable,
        str(_WRAPPER),
        "run",
        "--pipeline-output", str(pipeline_out),
        "--no-self-verify",
    ]
    if state_dir_flag is not None:
        cmd.extend(["--state-dir", str(state_dir_flag)])

    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.slow_models
class TestRealEvalAgentStateDirContract:
    """Drive the real eval-agent CLI; assert env + flag wiring."""

    def test_real_cli_honors_eval_agent_state_dir_env_var(
        self,
        tmp_path: Path,
        bundled_eval_agent_root: Path,
        fake_marc_and_ner: Path,
    ) -> None:
        state = _make_state_dir(tmp_path, "state-env")
        bundled_runs = bundled_eval_agent_root / "state" / "runs"
        runs_before: set[str] = (
            {p.name for p in bundled_runs.iterdir() if p.is_dir()}
            if bundled_runs.is_dir() else set()
        )

        # Run with ONLY the env var (no --state-dir flag) so the env
        # path is what makes the run land somewhere usable.
        result = _run_wrapper(
            pipeline_out=fake_marc_and_ner,
            state_dir_env=state,
            state_dir_flag=None,
            cwd=tmp_path,
        )

        assert result.returncode == 0, (
            f"wrapper failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # New run subdir under the env-pointed dir.
        run_dirs = [p for p in (state / "runs").iterdir() if p.is_dir()]
        assert len(run_dirs) >= 1, (
            f"env-pointed runs dir got no new subfolder; stdout:\n{result.stdout}"
        )
        # The bundled in-tree runs dir gained nothing.
        runs_after = (
            {p.name for p in bundled_runs.iterdir() if p.is_dir()}
            if bundled_runs.is_dir() else set()
        )
        assert runs_after == runs_before, (
            "real CLI silently wrote to the bundled state/runs/ — the "
            "exact 2026-05-25 regression."
        )

    def test_real_cli_honors_state_dir_flag(
        self,
        tmp_path: Path,
        fake_marc_and_ner: Path,
    ) -> None:
        env_state = _make_state_dir(tmp_path, "state-env")
        flag_state = _make_state_dir(tmp_path, "state-flagged")

        result = _run_wrapper(
            pipeline_out=fake_marc_and_ner,
            state_dir_env=env_state,
            state_dir_flag=flag_state,
            cwd=tmp_path,
        )

        assert result.returncode == 0, (
            f"wrapper failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        flag_runs = [p for p in (flag_state / "runs").iterdir() if p.is_dir()]
        env_runs = [p for p in (env_state / "runs").iterdir() if p.is_dir()]
        assert len(flag_runs) >= 1, (
            "--state-dir flag did not produce a run dir at the flagged path"
        )
        assert len(env_runs) == 0, (
            "env var path got a run dir even though --state-dir was passed "
            "— flag must override env"
        )

    def test_real_cli_writes_canonical_artefacts(
        self,
        tmp_path: Path,
        fake_marc_and_ner: Path,
    ) -> None:
        state = _make_state_dir(tmp_path, "state")
        result = _run_wrapper(
            pipeline_out=fake_marc_and_ner,
            state_dir_env=state,
            state_dir_flag=None,
            cwd=tmp_path,
        )

        assert result.returncode == 0, (
            f"wrapper failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        run_dirs = [p for p in (state / "runs").iterdir() if p.is_dir()]
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        for artefact in ("manifest.json", "summary.csv", "results.jsonl", "report.md"):
            assert (run_dir / artefact).exists(), (
                f"{artefact} missing under {run_dir}; "
                f"stdout:\n{result.stdout}"
            )


@pytest.mark.slow_models
class TestEvalAgentWorkerPassesStateDirToSubprocess:
    """Worker-level contract: ``--state-dir`` + env var are both set
    before ``subprocess.Popen`` is called."""

    def test_eval_agent_worker_passes_state_dir_to_subprocess(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import io

        from mhm_pipeline import eval_agent_runner
        from mhm_pipeline.controller import workers as workers_mod
        from mhm_pipeline.controller.workers import EvalAgentWorker

        # Stub the runner so we don't depend on the bundled eval-agent
        # being installable in the test env.
        bundled = tmp_path / "bundled-eval-agent"
        bundled.mkdir()
        user_state = tmp_path / "user-state"
        (user_state / "state" / "runs").mkdir(parents=True)
        (user_state / "state" / "cache").mkdir(parents=True)
        monkeypatch.setattr(
            eval_agent_runner, "ensure_user_state_dir", lambda _r=None: user_state
        )
        monkeypatch.setattr(
            eval_agent_runner, "locate_bundled_eval_agent", lambda: bundled
        )
        monkeypatch.setattr(
            eval_agent_runner, "resolve_python_executable", lambda: sys.executable
        )

        pipeline_out = tmp_path / "pipeline-out"
        pipeline_out.mkdir()
        (pipeline_out / "marc_extracted.json").write_text("[]")
        (pipeline_out / "ner_results.json").write_text("[]")

        # Minimal Popen stand-in (matches the unit-test _FakeProc shape).
        class _FakeProc:
            def __init__(self) -> None:
                self.stdout = io.StringIO("")

            def wait(self) -> int:
                return 0

        worker = EvalAgentWorker(
            pipeline_output_dir=pipeline_out,
            gemini_api_key="AIzaUnitTest",
        )

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc()
            fake_run = user_state / "state" / "runs" / "stub-run"
            fake_run.mkdir()
            with patch.object(
                workers_mod, "_latest_run_dir", return_value=fake_run
            ):
                worker.run()

        assert popen.call_count == 1
        cmd = worker._last_cmd
        assert cmd is not None
        assert "--state-dir" in cmd, (
            "EvalAgentWorker omitted --state-dir from argv — would have "
            "silently regressed the 2026-05-25 state-dir bug."
        )
        # The flag-value MUST point at <user_state>/state.
        sd_idx = cmd.index("--state-dir")
        expected_state = str(user_state / "state")
        assert cmd[sd_idx + 1] == expected_state
        # cwd is the writable per-user state dir.
        assert worker._last_cwd == user_state
        # env block carries the env var too (defense in depth).
        env_passed = popen.call_args.kwargs["env"]
        assert env_passed.get("EVAL_AGENT_STATE_DIR") == expected_state


@pytest.mark.slow_models
class TestSymptomMirrorEvalAgentWorkerAgainstRealCli:
    """**Symptom test for the 2026-05-25 live failure.**

    The user-visible error string was

        ``AI agent finished but no run directory was produced under
        /Users/alexandergo/Library/Application Support/MHMPipeline/
        eval-agent/state/runs.``

    It fired because the eval-agent ignored ``EVAL_AGENT_STATE_DIR``
    and wrote the run dir to the wrong place; the worker looked at
    the env-pointed path, found nothing, and emitted
    :pyattr:`EvalAgentWorker.error`.

    This test drives the **real** bundled eval-agent CLI (with a
    stub-judge wrapper so no Gemini call) through the **real**
    :class:`EvalAgentWorker` end-to-end. It asserts:

    1. The worker emits ``finished(run_dir)`` — NOT ``error(...)``.
    2. The captured run_dir exists.
    3. The error signal NEVER fires (no ``"no run directory was
       produced"`` string ever surfaces).

    If the state-dir fix regresses, this test fails at point 1 with
    the canonical user-facing error string in ``captured_error``.
    """

    def test_worker_against_real_cli_emits_finished_not_error(
        self,
        tmp_path: Path,
        fake_marc_and_ner: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from mhm_pipeline import eval_agent_runner
        from mhm_pipeline.controller.workers import EvalAgentWorker

        # Point the worker at the real eval-agent root; the subprocess
        # is rewritten below to launch our wrapper (which monkey-patches
        # the Gemini judge before forwarding to the real CLI).
        monkeypatch.setattr(
            eval_agent_runner,
            "locate_bundled_eval_agent",
            lambda: _EVAL_AGENT_ROOT,
        )

        # Per-user state dir lives under tmp_path — same shape as the
        # production worker's user_state_dir.
        user_state = tmp_path / "user-state"
        (user_state / "state" / "runs").mkdir(parents=True)
        (user_state / "state" / "cache").mkdir(parents=True)
        monkeypatch.setattr(
            eval_agent_runner,
            "ensure_user_state_dir",
            lambda _r=None: user_state,
        )
        monkeypatch.setattr(
            eval_agent_runner,
            "resolve_python_executable",
            lambda: sys.executable,
        )

        # Splice the wrapper script in front of the eval-agent CLI by
        # patching the worker's subprocess argv: instead of `python -m
        # eval_agent.cli run`, we run the wrapper which monkey-patches
        # `_build_judge` to a stub and then forwards to the real CLI.
        # Easiest path: re-export the wrapper as a sitecustomize-style
        # PYTHONPATH entry. The worker already sets PYTHONPATH to the
        # bundled root; just prepend wrapper_root.
        original_run = EvalAgentWorker.run

        def _patched_run(self: EvalAgentWorker) -> None:
            import os as _os

            # The worker's run() builds its own argv. We need it to
            # invoke the wrapper script, not `python -m eval_agent.cli`.
            # Simplest swap: monkey-patch subprocess.Popen at this scope
            # to rewrite the argv just before launching.
            import subprocess as _subprocess
            from contextlib import contextmanager

            real_popen = _subprocess.Popen

            @contextmanager
            def _scoped_popen() -> Any:
                def _rewriting_popen(cmd: list[str], **kwargs: Any) -> Any:
                    if "eval_agent.cli" in cmd:
                        # Replace `python -m eval_agent.cli ...` with
                        # `python <wrapper> ...` (drops the `-m
                        # eval_agent.cli` pair).
                        idx_m = cmd.index("-m")
                        new_cmd = (
                            cmd[:idx_m]
                            + [str(_WRAPPER)]
                            + cmd[idx_m + 2 :]
                        )
                        # The wrapper imports `eval_agent` from
                        # PYTHONPATH; make sure it's there.
                        env = kwargs.get("env", _os.environ.copy())
                        env["PYTHONPATH"] = (
                            f"{_EVAL_AGENT_ROOT}{_os.pathsep}"
                            f"{env.get('PYTHONPATH', '')}"
                        )
                        kwargs["env"] = env
                        return real_popen(new_cmd, **kwargs)
                    return real_popen(cmd, **kwargs)

                _subprocess.Popen = _rewriting_popen  # type: ignore[assignment]
                try:
                    yield
                finally:
                    _subprocess.Popen = real_popen  # type: ignore[assignment]

            with _scoped_popen():
                original_run(self)

        monkeypatch.setattr(EvalAgentWorker, "run", _patched_run)

        worker = EvalAgentWorker(
            pipeline_output_dir=fake_marc_and_ner,
            gemini_api_key="stub-key",
        )

        captured_finished: list[Path] = []
        captured_error: list[str] = []
        worker.finished.connect(lambda p: captured_finished.append(p))
        worker.error.connect(lambda msg: captured_error.append(msg))

        worker.run()

        assert not captured_error, (
            "EvalAgentWorker emitted an error against the real CLI — "
            "the 2026-05-25 regression is back. "
            f"Error string: {captured_error[0] if captured_error else ''}"
        )
        assert captured_finished, (
            "EvalAgentWorker did NOT emit finished. Either subprocess "
            "exited non-zero or the run-dir lookup found nothing — the "
            "exact failure surface of the 2026-05-25 live bug."
        )
        run_dir = captured_finished[0]
        assert run_dir.exists(), f"finished emitted but run dir missing: {run_dir}"
        # The canonical artefact a working run produces.
        assert (run_dir / "manifest.json").exists(), (
            f"run dir at {run_dir} has no manifest.json — partial write?"
        )

        # The user-visible error string MUST NOT have surfaced anywhere.
        for err in captured_error:
            assert "no run directory was produced" not in err, (
                "The canonical 2026-05-25 error string fired despite "
                "subprocess exit 0 — env var / state-dir flag was not "
                "honored end-to-end."
            )


@pytest.mark.slow_models
class TestEvalAgentWorkerStatsLineBridge:
    """The ``[STATS]`` stdout line emitted by eval-agent's ``ui.emit_stats``
    must round-trip into the worker's ``stats_update`` signal."""

    def test_eval_agent_worker_emits_stats_update_when_stdout_has_stats_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import io

        from mhm_pipeline import eval_agent_runner
        from mhm_pipeline.controller import workers as workers_mod
        from mhm_pipeline.controller.workers import EvalAgentWorker

        bundled = tmp_path / "bundled-eval-agent"
        bundled.mkdir()
        user_state = tmp_path / "user-state"
        (user_state / "state" / "runs").mkdir(parents=True)
        (user_state / "state" / "cache").mkdir(parents=True)
        monkeypatch.setattr(
            eval_agent_runner, "ensure_user_state_dir", lambda _r=None: user_state
        )
        monkeypatch.setattr(
            eval_agent_runner, "locate_bundled_eval_agent", lambda: bundled
        )
        monkeypatch.setattr(
            eval_agent_runner, "resolve_python_executable", lambda: sys.executable
        )

        pipeline_out = tmp_path / "pipeline-out"
        pipeline_out.mkdir()
        (pipeline_out / "marc_extracted.json").write_text("[]")
        (pipeline_out / "ner_results.json").write_text("[]")

        stdout_text = (
            "[STEP] Loading rubrics\n"
            "[PROGRESS] 10\n"
            "[STATS] total=10 hits=2 judged=5 in_tok=100 out_tok=20\n"
            "[PROGRESS] 100\n"
        )

        class _FakeProc:
            def __init__(self) -> None:
                self.stdout = io.StringIO(stdout_text)

            def wait(self) -> int:
                return 0

        worker = EvalAgentWorker(
            pipeline_output_dir=pipeline_out,
            gemini_api_key="AIzaUnitTest",
        )
        captured_stats: list[dict[str, Any]] = []
        worker.stats_update.connect(lambda d: captured_stats.append(d))

        with patch("subprocess.Popen") as popen:
            popen.return_value = _FakeProc()
            fake_run = user_state / "state" / "runs" / "stub-run"
            fake_run.mkdir()
            with patch.object(
                workers_mod, "_latest_run_dir", return_value=fake_run
            ):
                worker.run()

        assert captured_stats, (
            "[STATS] stdout line did not surface as stats_update emission"
        )
        assert captured_stats[-1] == {
            "total": 10,
            "cache_hits": 2,
            "judged": 5,
            "input_tokens": 100,
            "output_tokens": 20,
        }
