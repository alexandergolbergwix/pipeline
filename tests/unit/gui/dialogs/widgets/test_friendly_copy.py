"""Unit tests for ``mhm_pipeline.gui.dialogs.widgets.friendly_copy``.

Pure-Python module — no Qt required. Covers the friendly-label maps,
the ``[STEP]`` log-line rewrites, and the per-checker headline composer.
"""

from __future__ import annotations

from mhm_pipeline.gui.dialogs.widgets.friendly_copy import (
    compose_headline,
    humanise_evaluator,
    humanise_log_line,
)


class TestHumaniseEvaluator:
    def test_person_ner_to_person_ai(self) -> None:
        assert humanise_evaluator("person_ner") == "Person AI"

    def test_provenance_ner_to_owner_ai(self) -> None:
        assert humanise_evaluator("provenance_ner") == "Owner AI"

    def test_contents_ner_to_contents_ai(self) -> None:
        assert humanise_evaluator("contents_ner") == "Contents AI"

    def test_genre_classifier_to_genre_ai(self) -> None:
        assert humanise_evaluator("genre_classifier") == "Genre AI"

    def test_unknown_id_pass_through_title_case(self) -> None:
        # An evaluator we haven't friendlied yet should not surface as
        # raw snake_case; the function should fall back to title-cased
        # words.
        result = humanise_evaluator("custom_checker_xyz")
        assert "_" not in result
        assert result != "custom_checker_xyz"


class TestHumaniseLogLine:
    def test_step_judging_line_uses_friendly_evaluator_and_n_of_m(self) -> None:
        out = humanise_log_line("[STEP] Judging person_ner 47/143")
        assert "Person AI" in out
        assert "47" in out
        assert "143" in out
        # Either "47 of 143" or "47/143" — both are acceptable, but
        # the canonical rewrite produces "N of M".
        assert "47 of 143" in out or "47/143" in out


class TestComposeHeadline:
    def test_two_evaluators_produce_percentage_and_friendly_names(self) -> None:
        rows = [
            {
                "evaluator_id": "person_ner",
                "candidates_total": "100",
                "full": "90",
                "fail": "5",
            },
            {
                "evaluator_id": "provenance_ner",
                "candidates_total": "50",
                "full": "40",
                "fail": "2",
            },
        ]
        out = compose_headline(rows)
        assert "%" in out
        # At least one friendly checker name shows up.
        assert "Person AI" in out or "Owner AI" in out

    def test_zero_total_evaluator_skipped(self) -> None:
        rows = [
            {
                "evaluator_id": "person_ner",
                "candidates_total": "100",
                "full": "90",
                "fail": "0",
            },
            # Skipped — total is 0.
            {
                "evaluator_id": "provenance_ner",
                "candidates_total": "0",
                "full": "0",
                "fail": "0",
            },
        ]
        out = compose_headline(rows)
        assert "Person AI" in out
        assert "Owner AI" not in out

    def test_caps_at_three_evaluators(self) -> None:
        rows = [
            {
                "evaluator_id": f"checker_{i}",
                "candidates_total": str(100 - i),  # so they sort by size
                "full": str(80 - i),
                "fail": "1",
            }
            for i in range(5)
        ]
        # Spread two known names so we can assert they appear / are skipped.
        rows[0]["evaluator_id"] = "person_ner"
        rows[1]["evaluator_id"] = "provenance_ner"
        rows[2]["evaluator_id"] = "contents_ner"
        rows[3]["evaluator_id"] = "genre_classifier"
        rows[4]["evaluator_id"] = "place_ner"

        out = compose_headline(rows)
        # Three of the friendly names appear; the fourth and fifth
        # (smallest totals after sorting) are NOT mentioned in the lead.
        mentioned = sum(
            1 for friendly in (
                "Person AI", "Owner AI", "Contents AI",
                "Genre AI", "Place AI",
            ) if friendly in out
        )
        assert mentioned == 3
