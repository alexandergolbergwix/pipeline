"""Unit tests for ``mhm_pipeline.gui.dialogs.widgets.agent_system_diagram``.

Verifies node construction, substep → activation routing, cache-hit
particle emission, error flashing, the ``on_finished`` done-state
transition, ``reset()`` cleanup, and the ``_parse_substep_line`` regex
table.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui.dialogs.widgets.agent_system_diagram import (  # noqa: E402
    AgentSystemDiagram,
    _AgentNode,
    _parse_substep_line,
)


@pytest.fixture(autouse=True)
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app


def _state(diagram: AgentSystemDiagram, key: str) -> str:
    node = diagram._nodes.get(key)
    assert node is not None, f"node {key!r} missing"
    return node.state()


class TestAgentSystemDiagramConstruction:
    def test_constructs_with_10_nodes(self) -> None:
        diagram = AgentSystemDiagram()
        assert diagram.node_count() == 10


class TestSubstepRouting:
    def test_loading_rubrics_activates_rubric_node(self) -> None:
        diagram = AgentSystemDiagram()
        diagram.on_substep("Loading rubrics")
        assert _state(diagram, "rubric") == _AgentNode.STATE_ACTIVE

    def test_judging_person_ner_activates_evaluator_and_emits_particle(
        self,
    ) -> None:
        diagram = AgentSystemDiagram()
        diagram.on_substep("Judging person_ner 47/143")
        # Evaluator + downstream judge are active.
        assert _state(diagram, "person_ner") == _AgentNode.STATE_ACTIVE
        assert _state(diagram, "gemini") == _AgentNode.STATE_ACTIVE
        # At least one in-flight particle for the evaluator→gemini hop.
        assert len(diagram._particles) >= 1


class TestCacheHitParticle:
    def test_cache_hit_delta_launches_particle_from_cache(self) -> None:
        diagram = AgentSystemDiagram()
        # Prime an active evaluator so the loop-back has a target.
        diagram.on_substep("Judging person_ner 10/100")
        before = len(diagram._particles)

        diagram.on_stats({"cache_hits": 10, "judged": 20, "total": 100})
        # Delta is +10 → fires the cache loop-back particle.
        diagram.on_stats({"cache_hits": 12, "judged": 22, "total": 100})

        assert len(diagram._particles) > before
        # Cache node has now been "touched" — diagram tracks it for
        # the on_finished sweep.
        assert "cache" in diagram._touched_nodes


class TestErrorFlash:
    def test_on_error_flips_active_nodes_to_error_state(self) -> None:
        diagram = AgentSystemDiagram()
        diagram.on_substep("Judging person_ner 1/10")
        # person_ner + gemini are STATE_ACTIVE right now.
        assert _state(diagram, "person_ner") == _AgentNode.STATE_ACTIVE

        diagram.on_error("boom")

        # At least one node that was active is now ERROR.
        error_states = [
            key for key, node in diagram._nodes.items()
            if node.state() == _AgentNode.STATE_ERROR
        ]
        assert error_states, "expected at least one node in error state"


class TestFinishedSweep:
    def test_on_finished_moves_touched_nodes_to_done(self) -> None:
        diagram = AgentSystemDiagram()
        diagram.on_substep("Loading rubrics")
        diagram.on_substep("Judging person_ner 5/10")
        diagram.on_finished()

        # No node should remain in the active state.
        active = [
            key for key, n in diagram._nodes.items()
            if n.state() == _AgentNode.STATE_ACTIVE
        ]
        assert active == []
        # And the evaluator we touched transitions to done (not error).
        assert _state(diagram, "person_ner") == _AgentNode.STATE_DONE


class TestReset:
    def test_reset_clears_particles_and_returns_nodes_to_idle(self) -> None:
        diagram = AgentSystemDiagram()
        diagram.on_substep("Judging person_ner 1/10")
        diagram.on_stats({"cache_hits": 5, "judged": 5, "total": 50})
        diagram.on_stats({"cache_hits": 8, "judged": 10, "total": 50})
        # Sanity: we set things up so reset has work to do.
        assert diagram._particles or diagram._touched_nodes

        diagram.reset()
        assert len(diagram._particles) == 0
        for node in diagram._nodes.values():
            assert node.state() == _AgentNode.STATE_IDLE


class TestParseSubstepLine:
    def test_loading_rubrics_pattern(self) -> None:
        parsed = _parse_substep_line("Loading rubrics")
        assert parsed == {"action": "load_rubrics"}

    def test_raw_judging_evaluator_n_over_m(self) -> None:
        parsed = _parse_substep_line("Judging person_ner 47/143")
        assert parsed == {
            "action": "judging",
            "evaluator_id": "person_ner",
            "current": 47,
            "total": 143,
        }

    def test_friendly_checking_n_of_m(self) -> None:
        parsed = _parse_substep_line("Checking Person AI 47 of 143")
        assert parsed == {
            "action": "judging",
            "evaluator_id": "person_ner",
            "current": 47,
            "total": 143,
        }

    def test_writing_results_pattern(self) -> None:
        parsed = _parse_substep_line("Writing results")
        assert parsed == {"action": "writing"}

    def test_unknown_input_returns_none(self) -> None:
        assert _parse_substep_line("bonjour") is None
        assert _parse_substep_line("") is None
