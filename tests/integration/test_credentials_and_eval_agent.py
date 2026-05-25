"""Integration tests for the Rule 50 credentials + eval-agent surface.

These exercise the wiring across modules end-to-end:

* :class:`SettingsManager` + :class:`CredentialStore` — every secret-
  shaped property round-trips through the OS keychain (or the
  in-memory stub when running headless).
* QSettings → keychain migration: pre-Rule-50 plaintext values are
  moved on first SettingsManager touch and the legacy slot is cleared.
* :class:`EvalAgentWorker` + bundled eval-agent CLI: the worker spawns
  a real subprocess against a fake but conforming eval-agent CLI
  (written inline as a test fixture) and emits ``finished`` with a
  populated run dir.

These intentionally **do not** call the real Gemini API. Gemini is
out of scope for CI; the fake CLI just prints ``[STEP]`` lines and
writes the standard four artefacts.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
import time
from pathlib import Path

import pytest

from mhm_pipeline.settings import credential_store
from mhm_pipeline.settings.credential_store import (
    GEMINI_API_KEY,
    SERVICE_NAME,
    WIKIBASE_CLOUD_BOT_PASSWORD,
    WIKIDATA_TOKEN,
)

# ── In-memory keyring backend so CI containers without a real
#    keychain still pass these tests ──────────────────────────────────


class _StubKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str) -> str | None:
        return self.store.get((service, user))

    def set_password(self, service: str, user: str, value: str) -> None:
        self.store[(service, user)] = value

    def delete_password(self, service: str, user: str) -> None:
        self.store.pop((service, user), None)


@pytest.fixture
def stub_keyring(monkeypatch: pytest.MonkeyPatch) -> _StubKeyring:
    stub = _StubKeyring()
    monkeypatch.setattr(credential_store, "_import_keyring", lambda: stub)
    return stub


# ── SettingsManager <-> CredentialStore integration ─────────────────


class TestSettingsManagerCredentialIntegration:
    def test_gemini_round_trip_through_settings_manager(
        self, stub_keyring: _StubKeyring
    ) -> None:
        from mhm_pipeline.settings.settings_manager import SettingsManager

        s = SettingsManager()
        assert s.gemini_api_key == ""
        s.gemini_api_key = "AIzaIntegrationTest"
        assert s.gemini_api_key == "AIzaIntegrationTest"
        assert stub_keyring.store[(SERVICE_NAME, GEMINI_API_KEY)] == "AIzaIntegrationTest"

    def test_setting_empty_string_clears_stored_value(
        self, stub_keyring: _StubKeyring
    ) -> None:
        from mhm_pipeline.settings.settings_manager import SettingsManager

        s = SettingsManager()
        s.wikidata_token = "User@Bot:secret"
        s.wikidata_token = ""
        assert (SERVICE_NAME, WIKIDATA_TOKEN) not in stub_keyring.store

    def test_three_credentials_independent(
        self, stub_keyring: _StubKeyring
    ) -> None:
        from mhm_pipeline.settings.settings_manager import SettingsManager

        s = SettingsManager()
        s.gemini_api_key = "AIzaA"
        s.wikidata_token = "User@Bot:B"
        s.wikibase_cloud_bot_password = "hexC"
        assert s.gemini_api_key == "AIzaA"
        assert s.wikidata_token == "User@Bot:B"
        assert s.wikibase_cloud_bot_password == "hexC"

        # Clearing one leaves the others alone.
        s.wikidata_token = ""
        assert s.gemini_api_key == "AIzaA"
        assert s.wikibase_cloud_bot_password == "hexC"


class TestQSettingsToKeychainMigration:
    def test_pre_rule_50_value_migrated_on_first_access(
        self, stub_keyring: _StubKeyring, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force QSettings to a temp INI file we can pre-populate.
        from PyQt6.QtCore import QSettings

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))

        from mhm_pipeline.settings.settings_manager import SettingsManager

        s = SettingsManager()
        # Plant a legacy plaintext value in QSettings ahead of the
        # first ``_ensure_credentials`` call.
        s._qs.setValue("tokens/gemini_api_key", "AIzaLegacyValue")
        s._qs.sync()

        # First read triggers the migration sweep.
        assert s.gemini_api_key == "AIzaLegacyValue"
        # Legacy slot must be cleared on disk.
        assert str(s._qs.value("tokens/gemini_api_key", "") or "") == ""


# ── EvalAgentWorker against a fake eval-agent CLI ───────────────────


def _write_fake_eval_agent_pkg(root: Path) -> Path:
    """Materialise a stripped-down eval-agent at ``root`` that:

    * Exposes ``python -m eval_agent.cli run --pipeline-output …``.
    * Reads ``GEMINI_API_KEY`` from the env (fails if missing).
    * Writes the canonical four artefacts to ``state/runs/<ts>/`` under
      cwd, emits two ``[STEP]`` and one ``[PROGRESS]`` line.
    """
    pkg = root / "eval_agent"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    cli_code = textwrap.dedent(
        """
        import argparse
        import json
        import os
        import sys
        import time
        from pathlib import Path

        def main(argv=None):
            parser = argparse.ArgumentParser()
            sub = parser.add_subparsers(dest="cmd")
            run = sub.add_parser("run")
            run.add_argument("--pipeline-output", required=True)
            run.add_argument("--models", required=False)
            # Match the real eval-agent CLI surface (added 2026-05-25):
            # accept --state-dir so the EvalAgentWorker's argv doesn't
            # error out with argparse exit-code 2.
            run.add_argument("--state-dir", required=False, default=None)
            args = parser.parse_args(argv)

            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                print("[ERROR] GEMINI_API_KEY missing", file=sys.stderr)
                return 2

            print("[STEP] Loading rubrics", flush=True)
            print("[PROGRESS] 25", flush=True)
            print("[STEP] Judging candidates", flush=True)

            ts = time.strftime("%Y%m%dT%H%M%S")
            # Honor EVAL_AGENT_STATE_DIR (per the 2026-05-25 fix);
            # fall back to the legacy cwd/state/ path so existing
            # callers keep working.
            state_env = os.environ.get("EVAL_AGENT_STATE_DIR")
            state_root = Path(state_env) if state_env else (Path.cwd() / "state")
            run_dir = state_root / "runs" / ts
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "report.md").write_text("# eval-agent fake report\\n", encoding="utf-8")
            (run_dir / "summary.csv").write_text(
                "evaluator,candidates,pass,fail\\n"
                "person_ner,3,2,1\\n", encoding="utf-8",
            )
            (run_dir / "results.jsonl").write_text("{}\\n", encoding="utf-8")
            (run_dir / "manifest.json").write_text(
                json.dumps({"started_at": ts, "fake": True}), encoding="utf-8",
            )
            print("[PROGRESS] 100", flush=True)
            return 0

        if __name__ == "__main__":
            sys.exit(main())
        """
    )
    (pkg / "cli.py").write_text(cli_code)
    (pkg / "__main__.py").write_text(
        "from eval_agent.cli import main\nimport sys\nsys.exit(main())\n"
    )
    return root


def _write_fake_pipeline_outputs(out: Path) -> Path:
    """Drop the two files EvalAgentWorker requires before running."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "marc_extracted.json").write_text("[]")
    (out / "ner_results.json").write_text("[]")
    return out


@pytest.fixture
def fake_eval_agent_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """Wire ``ensure_user_state_dir`` + ``locate_bundled_eval_agent`` at
    a temp fake eval-agent installation that produces real run dirs."""
    bundled = _write_fake_eval_agent_pkg(tmp_path / "bundled-eval-agent")
    user_state = tmp_path / "user-state"
    user_state.mkdir()
    (user_state / "state" / "runs").mkdir(parents=True)
    (user_state / "state" / "cache").mkdir(parents=True)

    from mhm_pipeline import eval_agent_runner

    monkeypatch.setattr(
        eval_agent_runner, "ensure_user_state_dir", lambda _r=None: user_state
    )
    monkeypatch.setattr(
        eval_agent_runner, "locate_bundled_eval_agent", lambda: bundled
    )
    monkeypatch.setattr(
        eval_agent_runner, "resolve_python_executable", lambda: sys.executable
    )
    pipeline_out = _write_fake_pipeline_outputs(tmp_path / "pipeline-out")
    return {
        "bundled": bundled,
        "user_state": user_state,
        "pipeline_out": pipeline_out,
    }


class TestEvalAgentWorkerAgainstFakeCli:
    def test_subprocess_round_trip_emits_finished_with_run_dir(
        self,
        fake_eval_agent_environment: dict[str, Path],
        qtbot: object,
    ) -> None:
        """Spawn the fake eval-agent CLI, let it write artefacts, and
        confirm the worker emits ``finished`` with the per-run dir."""
        from PyQt6.QtCore import QEventLoop, QTimer

        from mhm_pipeline.controller.workers import EvalAgentWorker

        env = fake_eval_agent_environment
        worker = EvalAgentWorker(
            pipeline_output_dir=env["pipeline_out"],
            gemini_api_key="AIzaFakeKey",
        )
        captured: dict[str, object] = {}
        substeps: list[str] = []

        def _on_finished(p: Path) -> None:
            captured["run_dir"] = p

        def _on_error(msg: str) -> None:
            captured["error"] = msg

        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        worker.substep.connect(lambda s: substeps.append(s))

        # Run the worker directly (no QThread) so the test is
        # synchronous and easy to assert against.
        worker.run()

        assert "error" not in captured, f"unexpected error: {captured.get('error')}"
        assert "run_dir" in captured, "worker did not emit finished"
        run_dir = captured["run_dir"]
        assert isinstance(run_dir, Path)
        assert (run_dir / "report.md").exists()
        assert (run_dir / "summary.csv").exists()
        # Substep emissions captured at least the two [STEP] lines.
        assert "Loading rubrics" in substeps
        assert "Judging candidates" in substeps
