"""Tests for ``converter.authority.wikidata_matcher`` (Agent A).

All HTTP is stubbed via ``unittest.mock.patch.object`` — no live SPARQL
calls. The shared throttle clock is reset between tests and
``time.sleep`` is monkey-patched to a no-op so the suite runs in well
under a second.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests


# ── Helpers ───────────────────────────────────────────────────────────


def _bindings_payload(qids: list[str]) -> dict[str, Any]:
    return {
        "head": {"vars": ["p"]},
        "results": {
            "bindings": [
                {"p": {"type": "uri", "value": f"http://www.wikidata.org/entity/{q}"}}
                for q in qids
            ]
        },
    }


def _label_payload(labels: list[str]) -> dict[str, Any]:
    return {
        "head": {"vars": ["label"]},
        "results": {
            "bindings": [
                {"label": {"type": "literal", "xml:lang": "he", "value": s}}
                for s in labels
            ]
        },
    }


def _ask_payload(value: bool) -> dict[str, Any]:
    return {"head": {}, "boolean": value}


def _ok(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def _err(status: int) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.json.return_value = {}
    return resp


@pytest.fixture(autouse=True)
def _isolate_module_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets its own cache dir, no real sleeping, no rate budget,
    fresh ``Session`` so ``patch.object(Session, 'get')`` rebinds cleanly."""
    monkeypatch.setenv("MHM_AUTHORITY_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", raising=False)

    import converter.authority.wikidata_crosscheck as wcc
    import converter.authority.wikidata_matcher as wm

    monkeypatch.setattr(wcc.time, "sleep", lambda _s: None)
    # Matcher imports time lazily; patch its module-level reference too.
    monkeypatch.setattr("time.sleep", lambda _s: None)
    wcc._reset_throttle_for_tests()
    wcc._session = None
    wm._reset_session_for_tests()


# ── Tests ─────────────────────────────────────────────────────────────


def test_identifier_triangulation_single_hit() -> None:
    """Test 1: VIAF ID with exactly one P214 match → returns that QID."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    payload = _bindings_payload(["Q12345"])
    with patch.object(requests.Session, "get", return_value=_ok(payload)) as mock_get:
        matcher = WikidataMatcher()
        result = matcher.find_qid_by_viaf("170207558")

    assert result == "Q12345"
    assert mock_get.call_count == 1
    # The query must hit ``wdt:P214`` literally — not the label search.
    call_kwargs = mock_get.call_args
    sent_query = call_kwargs.kwargs.get("params", {}).get("query", "")
    assert "wdt:P214" in sent_query
    assert "170207558" in sent_query


def test_identifier_triangulation_multiple_rows_abstain() -> None:
    """Test 2: VIAF ID with two P214 matches → return None (abstain)."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    payload = _bindings_payload(["Q1", "Q2"])
    with patch.object(requests.Session, "get", return_value=_ok(payload)):
        matcher = WikidataMatcher()
        result = matcher.find_qid_by_viaf("99999999")

    assert result is None


def test_hebrew_label_match_with_type_pass() -> None:
    """Test 3: Hebrew label search returns a Q5 (human) candidate."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    label_payload = _bindings_payload(["Q42"])
    type_ok = _ask_payload(True)
    label_check = _label_payload(["אברהם בן מאיר"])
    side_effect = [_ok(label_payload), _ok(type_ok), _ok(label_check)]

    with patch.object(requests.Session, "get", side_effect=side_effect) as mock_get:
        matcher = WikidataMatcher()
        result = matcher.match_person("אברהם בן מאיר")

    assert result == "Q42"
    # Three SPARQL hops: label search, ASK type check, label-fetch verification.
    assert mock_get.call_count == 3
    assert matcher.last_match_was_latin_only() is False


def test_hebrew_label_match_wrong_type_filtered() -> None:
    """Test 4: candidate is an organisation (P31=Q43229) → ASK returns
    False → ``match_person`` falls through Mode 2, then Mode 3 also misses,
    so the call returns None."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    # Mode 2: returns one candidate that fails the type ASK.
    mode2_label = _bindings_payload(["Q999"])
    mode2_type_fail = _ask_payload(False)
    # Mode 3 (Latin fallback): empty bindings — nothing to verify.
    mode3_empty = _bindings_payload([])

    side_effect = [_ok(mode2_label), _ok(mode2_type_fail), _ok(mode3_empty)]
    with patch.object(requests.Session, "get", side_effect=side_effect) as mock_get:
        matcher = WikidataMatcher()
        result = matcher.match_person("ספרייה לאומית")

    assert result is None
    assert mock_get.call_count == 3


def test_latin_fallback_only_when_hebrew_empty() -> None:
    """Test 5: Mode 2 (Hebrew) runs first; Mode 3 (Latin) only runs on miss."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    # Mode 2: empty.
    mode2_empty = _bindings_payload([])
    # Mode 3: one candidate, type passes, label matches.
    mode3_label = _bindings_payload(["Q7"])
    type_ok = _ask_payload(True)
    label_check = _label_payload(["Maimonides"])
    side_effect = [_ok(mode2_empty), _ok(mode3_label), _ok(type_ok), _ok(label_check)]

    with patch.object(requests.Session, "get", side_effect=side_effect) as mock_get:
        matcher = WikidataMatcher()
        result = matcher.match_person("Maimonides")

    assert result == "Q7"
    assert matcher.last_match_was_latin_only() is True
    # Verify call ordering: 1st = Hebrew (he), 2nd = Latin (en).
    sent_queries = [c.kwargs.get("params", {}).get("query", "") for c in mock_get.call_args_list]
    assert '"@he' in sent_queries[0]
    assert '"@en' in sent_queries[1]


def test_cache_hit_avoids_http() -> None:
    """Test 6: a successful match populates the cache; the second call
    issues zero HTTP requests."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    label_payload = _bindings_payload(["Q42"])
    type_ok = _ask_payload(True)
    label_check = _label_payload(["שמשון, יצחק בן ברוך"])
    side_effect = [_ok(label_payload), _ok(type_ok), _ok(label_check)]

    with patch.object(requests.Session, "get", side_effect=side_effect) as mock_get_1:
        matcher = WikidataMatcher()
        first = matcher.match_person("שמשון, יצחק בן ברוך")
    assert first == "Q42"
    assert mock_get_1.call_count == 3

    # New matcher instance — must still hit the on-disk cache.
    with patch.object(requests.Session, "get") as mock_get_2:
        matcher_2 = WikidataMatcher()
        second = matcher_2.match_person("שמשון, יצחק בן ברוך")

    assert second == "Q42"
    assert mock_get_2.call_count == 0


def test_429_recovery_after_backoff() -> None:
    """Test 7: identifier query returns 429, 429, then 200 → matcher succeeds."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    payload = _bindings_payload(["Q777"])
    side_effect = [_err(429), _err(429), _ok(payload)]

    with patch.object(requests.Session, "get", side_effect=side_effect) as mock_get:
        matcher = WikidataMatcher()
        result = matcher.find_qid_by_viaf("123")

    assert result == "Q777"
    assert mock_get.call_count == 3


def test_label_search_prefers_lowest_qid_when_multiple_candidates() -> None:
    """Test 9: when SPARQL returns several candidates, the matcher picks the
    smallest-numbered QID (canonical) over a recent pipeline-created
    duplicate. Regression test for Rashi → Q189564 not Q139094451."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    # SPARQL returns the duplicate FIRST, canonical second — without
    # sort-by-QID the matcher would pick the first that verifies.
    candidates = _bindings_payload(["Q139094451", "Q189564"])
    type_ok = _ask_payload(True)
    label_check = _label_payload(["שלמה בן יצחק"])
    # After sorting: Q189564 is tried first → its 2 verification calls fire,
    # both pass → Q139094451 never reached.
    side_effect = [_ok(candidates), _ok(type_ok), _ok(label_check)]

    with patch.object(requests.Session, "get", side_effect=side_effect):
        matcher = WikidataMatcher()
        result = matcher.match_person("שלמה בן יצחק")

    assert result == "Q189564"


def test_label_search_skips_failing_lower_qid_falls_through_to_next() -> None:
    """Test 10: when the lowest QID fails verification, matcher proceeds to
    the next-lowest. Confirms sort + verify still iterates."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    candidates = _bindings_payload(["Q500", "Q100", "Q300"])
    # Sorted ascending: Q100 (fails type), Q300 (passes), Q500 (skipped).
    side_effect = [
        _ok(candidates),
        _ok(_ask_payload(False)),  # Q100 type ASK fails
        _ok(_ask_payload(True)),   # Q300 type ASK passes
        _ok(_label_payload(["matching label"])),  # Q300 label verifies
    ]

    with patch.object(requests.Session, "get", side_effect=side_effect):
        matcher = WikidataMatcher()
        result = matcher.match_person("matching label")

    assert result == "Q300"


def test_find_viaf_by_qid_single_value() -> None:
    """Test 11: backfill — wd:Q42 wdt:P214 returns one VIAF ID → that ID."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    payload = {
        "head": {"vars": ["viaf"]},
        "results": {
            "bindings": [
                {"viaf": {"type": "literal", "value": "12345678"}},
            ]
        },
    }

    with patch.object(requests.Session, "get", return_value=_ok(payload)) as mock_get:
        matcher = WikidataMatcher()
        result = matcher.find_viaf_by_qid("Q42")

    assert result == "12345678"
    assert mock_get.call_count == 1
    sent_query = mock_get.call_args.kwargs.get("params", {}).get("query", "")
    assert "wdt:P214" in sent_query
    assert "wd:Q42" in sent_query


def test_find_viaf_by_qid_multiple_abstain() -> None:
    """Test 12: backfill abstains when a QID has 2+ P214 values."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    payload = {
        "head": {"vars": ["viaf"]},
        "results": {
            "bindings": [
                {"viaf": {"type": "literal", "value": "111"}},
                {"viaf": {"type": "literal", "value": "222"}},
            ]
        },
    }
    with patch.object(requests.Session, "get", return_value=_ok(payload)):
        matcher = WikidataMatcher()
        result = matcher.find_viaf_by_qid("Q42")

    assert result is None


def test_find_viaf_by_qid_caches() -> None:
    """Test 13: backfill caches under qid_to_viaf:<qid> and avoids re-fetching."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    payload = {
        "head": {"vars": ["viaf"]},
        "results": {"bindings": [{"viaf": {"type": "literal", "value": "55"}}]},
    }

    with patch.object(requests.Session, "get", return_value=_ok(payload)) as mock_get_1:
        first = WikidataMatcher().find_viaf_by_qid("Q7")
    assert first == "55"
    assert mock_get_1.call_count == 1

    with patch.object(requests.Session, "get") as mock_get_2:
        second = WikidataMatcher().find_viaf_by_qid("Q7")
    assert second == "55"
    assert mock_get_2.call_count == 0


def test_disable_kill_switch_via_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 8: ``MHM_DISABLE_WIKIDATA_CROSSCHECK=1`` makes every match_*
    method return None without issuing any HTTP request."""
    from converter.authority.wikidata_matcher import WikidataMatcher

    monkeypatch.setenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", "1")

    with patch.object(requests.Session, "get") as mock_get:
        matcher = WikidataMatcher()
        assert matcher.match_person("אברהם") is None
        assert matcher.match_place("ירושלים") is None
        assert matcher.match_corporate("ספרייה לאומית") is None
        assert matcher.match_work("ספר הזוהר") is None
        assert matcher.find_qid_by_viaf("12345678") is None
        assert matcher.find_qid_by_mazal("987654") is None

    assert mock_get.call_count == 0
