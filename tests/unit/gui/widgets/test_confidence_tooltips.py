"""Unit tests for the confidence-column hover tooltips.

Two surfaces:

* :func:`mhm_pipeline.gui.widgets.authority_editor._build_authority_confidence_tooltip`
  — the Stage-3 verdict breakdown (sources matched, guards fired,
  rejection reason).
* :func:`mhm_pipeline.gui.widgets.extraction_editor._build_ner_keyword_conf_tooltip`
  and ``_build_ner_model_conf_tooltip`` — explain the keyword-classifier
  bucket and BIO softmax respectively.

The tests poke the HTML builders directly so no Qt event loop is
required.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: E402
    _build_authority_confidence_tooltip,
    _confidence_band_label,
    flatten_authority_records,
)
from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: E402
    _build_ner_keyword_conf_tooltip,
    _build_ner_model_conf_tooltip,
    _ner_confidence_band,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _qapp_offscreen() -> None:
    """Qt theme calls inside the helpers need a QApplication.

    The HTML builders import ``mhm_pipeline.gui.theme`` lazily, which in
    turn reads the QApplication palette. Without a running app the
    helpers will crash on first invocation.
    """
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield
    del app


# ── Authority tooltip ─────────────────────────────────────────────────────


class TestAuthorityConfidenceTooltip:
    """Authority tooltip — Stage 3 verdict breakdown."""

    def test_high_confidence_renders_sources_block(self) -> None:
        row = {
            "_auth_kind": "mazal",
            "confidence": 0.95,
            "_confidence_bucket": "high",
            "_sources": ["mazal", "viaf"],
            "_source_count": 2,
            "_preferred_name_lat": "Maimonides",
            "matched_id": "987001234567890",
            "wikidata_qid": "Q189554",
            "_guard_flags": ["has_wikidata", "wikidata_confirms"],
            "_birth_year": 1138,
            "_death_year": 1204,
            "gnd_id": "118576488",
            "lc_id": "n 78096039",
            "isni": "0000000123750072",
            "bnf_id": "",
            "_rejection_reason": "",
        }
        html = _build_authority_confidence_tooltip(row)
        assert "HIGH" in html
        assert "Sources agreed:" in html
        assert "Maimonides" in html
        assert "Q189554" in html
        assert "118576488" in html  # GND
        assert "1138" in html and "1204" in html
        assert "Wikidata confirms" in html

    def test_low_confidence_renders_rejection_reason(self) -> None:
        row = {
            "_auth_kind": "viaf",
            "confidence": 0.3,
            "_confidence_bucket": "low",
            "_sources": [],
            "_source_count": 0,
            "_preferred_name_lat": "",
            "matched_id": "",
            "wikidata_qid": "",
            "_guard_flags": ["date_conflict"],
            "_birth_year": 1700,
            "_death_year": 1770,
            "_rejection_reason": (
                "date-conflict: person born 1700, MS dated 1500 "
                "(cannot be MS author)"
            ),
        }
        html = _build_authority_confidence_tooltip(row)
        assert "LOW" in html
        assert "Rejection reason" in html
        assert "1700" in html and "1500" in html
        assert "Date conflict" in html

    def test_kima_short_circuits_to_index_lookup_message(self) -> None:
        row = {
            "_auth_kind": "kima",
            "confidence": 1.0,
            "_sources": ["kima"],
            "_source_count": 1,
        }
        html = _build_authority_confidence_tooltip(row)
        assert "KIMA" in html
        assert "direct-index lookup" in html
        # Should NOT render the source-by-source breakdown for KIMA.
        assert "Sources agreed" not in html

    def test_guard_labels_humanised(self) -> None:
        row = {
            "_auth_kind": "viaf",
            "confidence": 0.3,
            "_confidence_bucket": "low",
            "_guard_flags": [
                "short_name_homonym",
                "cluster_collapse",
                "over_merge_detected",
            ],
            "matched_id": "",
            "wikidata_qid": "",
            "_sources": [],
            "_source_count": 0,
        }
        html = _build_authority_confidence_tooltip(row)
        assert "Short-name homonym" in html
        assert "Cluster collapse" in html
        assert "Over-merge" in html

    def test_html_escapes_rejection_reason_to_prevent_injection(self) -> None:
        row = {
            "_auth_kind": "mazal",
            "confidence": 0.3,
            "_confidence_bucket": "low",
            "_rejection_reason": "<script>alert('xss')</script>",
            "_guard_flags": [],
            "_sources": [],
            "_source_count": 0,
            "matched_id": "",
            "wikidata_qid": "",
        }
        html = _build_authority_confidence_tooltip(row)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_bucket_string_overrides_float_band(self) -> None:
        # Float band would map 0.6 → MEDIUM, but the Stage 3 bucket says
        # "low" → tooltip must honour the explicit bucket.
        row = {
            "_auth_kind": "viaf",
            "confidence": 0.6,
            "_confidence_bucket": "low",
            "_guard_flags": [],
            "_sources": [],
            "_source_count": 0,
            "matched_id": "",
            "wikidata_qid": "",
        }
        html = _build_authority_confidence_tooltip(row)
        assert "LOW" in html
        assert "MEDIUM" not in html


class TestConfidenceBandLabel:
    """Float → tri-level band table."""

    @pytest.mark.parametrize("score,expected", [
        (0.95, "HIGH"),
        (0.85, "HIGH"),
        (0.6, "MEDIUM"),
        (0.45, "MEDIUM"),
        (0.3, "LOW"),
        (0.0, "LOW"),
    ])
    def test_band_thresholds(self, score: float, expected: str) -> None:
        label, _ = _confidence_band_label(score)
        assert label == expected

    def test_bucket_hint_dominates(self) -> None:
        # Float says HIGH but bucket says LOW → bucket wins.
        label, _ = _confidence_band_label(0.95, bucket_hint="low")
        assert label == "LOW"


# ── NER tooltip ───────────────────────────────────────────────────────────


class TestNerKeywordConfidenceTooltip:
    """Keyword-classifier (0.60 / 0.85) tooltip — Conf. column."""

    def test_high_bucket_explains_role_keyword_match(self) -> None:
        ent = {
            "confidence": 0.85,
            "model_confidence": 0.91,
            "role": "AUTHOR",
            "source": "person_ner",
            "type": "PERSON",
        }
        html = _build_ner_keyword_conf_tooltip(ent)
        assert "HIGH" in html
        assert "0.85" in html
        assert "Role-keyword matched" in html
        assert "AUTHOR" in html
        assert "Person NER" in html

    def test_low_bucket_explains_no_keyword_match(self) -> None:
        ent = {
            "confidence": 0.60,
            "role": "",
            "source": "provenance_ner",
            "type": "OWNER",
        }
        html = _build_ner_keyword_conf_tooltip(ent)
        assert "MEDIUM" in html  # 0.60 lands in MEDIUM band
        assert "fallback bucket" in html
        assert "Provenance NER" in html

    def test_unscored_renders_unscored_label(self) -> None:
        ent = {"confidence": 0.0, "source": "person_ner"}
        html = _build_ner_keyword_conf_tooltip(ent)
        assert "UNSCORED" in html

    def test_html_escapes_role(self) -> None:
        ent = {
            "confidence": 0.85,
            "role": "<x>",
            "source": "person_ner",
        }
        html = _build_ner_keyword_conf_tooltip(ent)
        assert "<x>" not in html
        assert "&lt;x&gt;" in html


class TestNerModelConfidenceTooltip:
    """BIO softmax tooltip — Model Conf. column."""

    def test_high_softmax_renders_high_band(self) -> None:
        ent = {
            "confidence": 0.85,
            "model_confidence": 0.94,
            "type": "WORK",
            "source": "contents_ner",
        }
        html = _build_ner_model_conf_tooltip(ent)
        assert "HIGH" in html
        assert "0.94" in html
        assert "WORK" in html
        assert "Contents NER" in html
        assert "BIO softmax" in html

    def test_low_softmax_renders_low_band(self) -> None:
        ent = {
            "model_confidence": 0.42,
            "type": "OWNER",
            "source": "provenance_ner",
        }
        html = _build_ner_model_conf_tooltip(ent)
        assert "LOW" in html
        assert "0.42" in html

    def test_unscored_when_model_confidence_missing(self) -> None:
        ent = {"type": "PERSON", "source": "person_ner"}
        html = _build_ner_model_conf_tooltip(ent)
        assert "UNSCORED" in html

    def test_includes_band_threshold_legend(self) -> None:
        ent = {"model_confidence": 0.7, "source": "person_ner"}
        html = _build_ner_model_conf_tooltip(ent)
        assert "0.85" in html  # legend mentions the high threshold
        assert "0.60" in html


class TestNerConfidenceBand:
    @pytest.mark.parametrize("score,expected", [
        (0.95, "HIGH"),
        (0.85, "HIGH"),
        (0.70, "MEDIUM"),
        (0.60, "MEDIUM"),
        (0.30, "LOW"),
        (0.0, "UNSCORED"),
    ])
    def test_band_thresholds(self, score: float, expected: str) -> None:
        label, _ = _ner_confidence_band(score)
        assert label == expected


# ── Authority flatten — breakdown signals survive ────────────────────────


class TestFlattenAuthorityRowsCarryBreakdownSignals:
    """``flatten_authority_records`` must copy the tooltip signals from
    ``authority_enriched.json`` into the row dict so the tooltip builder
    can read them without re-running Stage 3."""

    def test_marc_match_carries_guard_flags(self) -> None:
        records = [{
            "_control_number": "990001",
            "marc_authority_matches": [{
                "name": "משה בן מימון",
                "role": "author",
                "field": "100",
                "confidence": "high",
                "mazal_id": "987001234567890",
                "viaf_uri": "https://viaf.org/viaf/123",
                "preferred_name_lat": "Maimonides",
                "guard_flags": ["wikidata_confirms"],
                "source_count": 2,
                "sources": ["mazal", "viaf"],
                "birth_year": 1138,
                "death_year": 1204,
            }],
        }]
        rows = flatten_authority_records(records)
        assert len(rows) == 1
        r = rows[0]
        assert r["_guard_flags"] == ["wikidata_confirms"]
        assert r["_source_count"] == 2
        assert "mazal" in r["_sources"]
        assert r["_preferred_name_lat"] == "Maimonides"
        assert r["_birth_year"] == 1138
        assert r["_death_year"] == 1204
        assert r["_confidence_bucket"] == "high"

    def test_marc_match_carries_rejection_reason(self) -> None:
        records = [{
            "_control_number": "990002",
            "marc_authority_matches": [{
                "name": "John Doe",
                "role": "author",
                "field": "100",
                "confidence": "low",
                "rejection_reason": "date-conflict: person born 1700, MS dated 1500",
                "guard_flags": ["date_conflict"],
            }],
        }]
        rows = flatten_authority_records(records)
        assert "date-conflict" in rows[0]["_rejection_reason"]
        assert "date_conflict" in rows[0]["_guard_flags"]

    def test_ner_entity_row_keeps_keyword_and_model_scores(self) -> None:
        records = [{
            "_control_number": "990003",
            "entities": [{
                "text": "Rashi",
                "type": "PERSON",
                "source": "person_ner",
                "confidence": 0.85,
                "model_confidence": 0.91,
            }],
        }]
        rows = flatten_authority_records(records)
        assert rows[0]["_ner_keyword_conf"] == pytest.approx(0.85)
        assert rows[0]["_ner_model_conf"] == pytest.approx(0.91)
        assert rows[0]["_ner_source"] == "person_ner"

    def test_kima_row_marks_source_as_kima(self) -> None:
        records = [{
            "_control_number": "990004",
            "kima_places": {
                "ירושלים": "https://www.wikidata.org/entity/Q1218",
            },
        }]
        rows = flatten_authority_records(records)
        kima_rows = [r for r in rows if r["_auth_kind"] == "kima"]
        assert len(kima_rows) == 1
        assert kima_rows[0]["_sources"] == ["kima"]
        assert kima_rows[0]["_source_count"] == 1
