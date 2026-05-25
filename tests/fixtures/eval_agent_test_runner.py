"""Test runner wrapper that invokes the bundled eval-agent CLI with a
stubbed Gemini judge.

The wrapper is invoked as a subprocess from the
``test_eval_agent_cli_state_dir.py`` integration tests. It does three
things:

1. Adds the bundled eval-agent sibling repo to ``sys.path`` so
   ``eval_agent.*`` resolves.
2. Monkey-patches ``eval_agent.orchestration.session._build_judge`` to
   return a deterministic stub judge that returns a fixed verdict for
   every prompt. This keeps the run hermetic — no real Gemini calls,
   no network.
3. Hands off to ``eval_agent.cli.main(sys.argv[1:])`` and exits with
   its return code.

The bug we're guarding against (live 2026-05-25): the eval-agent
ignored ``EVAL_AGENT_STATE_DIR`` and ``--state-dir``, so the MHM
Pipeline subprocess wrote its run dir to the wrong location and the
GUI silently erred. The tests that drive this wrapper assert the
real CLI honors both knobs.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    eval_agent_root = Path(__file__).resolve().parents[2].parent / "eval-agent"
    if eval_agent_root.is_dir() and str(eval_agent_root) not in sys.path:
        sys.path.insert(0, str(eval_agent_root))

    from eval_agent.client.judge_interface import JudgeResponse  # noqa: PLC0415
    from eval_agent.orchestration import session as session_mod  # noqa: PLC0415

    class _StubJudge:
        id = "stub-judge-v1"

        def judge(
            self,
            *,
            prompt: str,
            schema: dict,
            timeout: int = 120,
        ) -> JudgeResponse:
            return JudgeResponse(
                verdict={
                    "name_ok": "yes",
                    "type_ok": "yes",
                    "role_ok": "n/a",
                    "overall": "full",
                    "reasoning": "stub judge — deterministic pass",
                },
                raw_text="{}",
                error=None,
                judge_id=self.id,
                input_tokens=10,
                output_tokens=5,
            )

    def _stub_build_judge(_config: object) -> _StubJudge:
        return _StubJudge()

    session_mod._build_judge = _stub_build_judge

    from eval_agent.cli import main as cli_main  # noqa: PLC0415

    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
