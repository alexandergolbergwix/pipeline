"""Tests for ``converter.authority.wikidata_crosscheck`` (F2 / F3).

All HTTP is stubbed via ``unittest.mock.patch.object`` — no live SPARQL
calls. The module's session-wide rate limiter is bypassed by patching
``time.sleep`` to a no-op so the suite runs in well under a second.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests


# ── Helpers ───────────────────────────────────────────────────────────


def _sparql_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap a list of binding rows in WDQS's standard envelope."""
    return {
        "head": {"vars": ["item", "labels", "b", "d", "occs"]},
        "results": {"bindings": items},
    }


def _binding(
    qid: str,
    *,
    labels: str = "",
    birth: str = "",
    death: str = "",
    occs: str = "",
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {
        "item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"},
    }
    if labels:
        out["labels"] = {"type": "literal", "value": labels}
    if birth:
        out["b"] = {"type": "literal", "value": birth}
    if death:
        out["d"] = {"type": "literal", "value": death}
    if occs:
        out["occs"] = {"type": "literal", "value": occs}
    return out


def _ok_response(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def _err_response(status: int) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.json.return_value = {}
    return resp


@pytest.fixture(autouse=True)
def _isolate_module_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets its own cache dir, no real sleeping, no rate budget."""
    monkeypatch.setenv("MHM_AUTHORITY_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", raising=False)

    import converter.authority.wikidata_crosscheck as wcc

    monkeypatch.setattr(wcc.time, "sleep", lambda _s: None)
    wcc._reset_throttle_for_tests()
    # Force a fresh session per test so patch.object on Session.get
    # doesn't leak across tests.
    wcc._session = None


# ── Tests ─────────────────────────────────────────────────────────────


def test_lookup_single_item_returns_expected_shape() -> None:
    """Test 1: one item, one Hebrew label → correctly parsed result."""
    import converter.authority.wikidata_crosscheck as wcc

    payload = _sparql_payload(
        [
            _binding(
                "Q12345",
                labels="ויטל, חיים בן יוסף",
                birth="1542-01-01T00:00:00Z",
                death="1620-04-23T00:00:00Z",
                occs="http://www.wikidata.org/entity/Q42603",
            )
        ]
    )
    with patch.object(requests.Session, "get", return_value=_ok_response(payload)) as mock_get:
        result = wcc.lookup_viaf("170207558")

    assert mock_get.call_count == 1
    assert result.viaf_id == "170207558"
    assert result.qids == ("Q12345",)
    assert result.hebrew_labels == ("ויטל, חיים בן יוסף",)
    assert result.birth_years == (1542,)
    assert result.death_years == (1620,)
    assert result.occupations == ("http://www.wikidata.org/entity/Q42603",)
    assert result.error is None


def test_overmerged_when_multiple_items_disagree_on_birth() -> None:
    """Test 2: two items with two different birth years → over-merged."""
    import converter.authority.wikidata_crosscheck as wcc

    payload = _sparql_payload(
        [
            _binding("Q1", labels="לוי", birth="1100-01-01T00:00:00Z"),
            _binding("Q2", labels="לוי", birth="1700-01-01T00:00:00Z"),
        ]
    )
    with patch.object(requests.Session, "get", return_value=_ok_response(payload)):
        result = wcc.lookup_viaf("99999999")

    assert len(result.qids) == 2
    assert wcc.is_overmerged(result) is True


def test_not_overmerged_when_birth_years_agree() -> None:
    """Test 3: two items with the same birth year (alternate Wikidata records,
    e.g. main entity + redirect) → NOT over-merged."""
    import converter.authority.wikidata_crosscheck as wcc

    payload = _sparql_payload(
        [
            _binding("Q10", birth="1500-01-01T00:00:00Z"),
            _binding("Q11", birth="1500-01-01T00:00:00Z"),
        ]
    )
    with patch.object(requests.Session, "get", return_value=_ok_response(payload)):
        result = wcc.lookup_viaf("88888888")

    assert len(result.qids) == 2
    assert wcc.is_overmerged(result) is False


def test_hebrew_label_matches_with_diacritic_variants() -> None:
    """Test 4: Levenshtein ≤ 2 accepts a nikud-ed variant of the same name."""
    import converter.authority.wikidata_crosscheck as wcc

    bare = "אברהם בן מאיר"
    nikud = "אַבְרָהָם בֶּן מֵאִיר"  # same string with vowel points
    assert wcc.hebrew_label_matches(bare, [nikud], max_distance=2) is True


def test_hebrew_label_does_not_match_when_too_far() -> None:
    """Test 5: distance > max → reject. Two completely different names."""
    import converter.authority.wikidata_crosscheck as wcc

    assert (
        wcc.hebrew_label_matches("שלמה בן יצחק", ["יוסף בן מימון"], max_distance=2)
        is False
    )


def test_http_429_then_succeeds_on_retry() -> None:
    """Test 6: first two attempts return 429, third returns 200."""
    import converter.authority.wikidata_crosscheck as wcc

    payload = _sparql_payload([_binding("Q777")])
    responses = [_err_response(429), _err_response(429), _ok_response(payload)]

    with patch.object(requests.Session, "get", side_effect=responses) as mock_get:
        result = wcc.lookup_viaf("123")

    assert mock_get.call_count == 3
    assert result.error is None
    assert result.qids == ("Q777",)


def test_http_500_final_failure_returns_error_no_exception() -> None:
    """Test 7: persistent 500s → result with .error populated, no raise."""
    import converter.authority.wikidata_crosscheck as wcc

    with patch.object(requests.Session, "get", return_value=_err_response(500)) as mock_get:
        result = wcc.lookup_viaf("456")

    assert mock_get.call_count == wcc.MAX_RETRIES
    assert result.qids == ()
    assert result.error is not None
    assert "500" in result.error


def test_cache_reuse_within_ttl_then_refetch_after_expiry(tmp_path: Path) -> None:
    """Test 8: 29-day-old cache entry is reused; 31-day-old entry is refreshed."""
    import converter.authority.wikidata_crosscheck as wcc

    payload = _sparql_payload([_binding("Q999")])
    # First lookup at t=1000 — populates cache.
    with patch.object(wcc, "_now", side_effect=[1000.0, 1000.0]):
        with patch.object(
            requests.Session, "get", return_value=_ok_response(payload)
        ) as mock_get_1:
            wcc.lookup_viaf("v-1")
    assert mock_get_1.call_count == 1

    # Second lookup at t = 1000 + 29 days — within TTL, no HTTP.
    twenty_nine_days = 29 * 86400
    with patch.object(wcc, "_now", return_value=1000.0 + twenty_nine_days):
        with patch.object(requests.Session, "get") as mock_get_2:
            r = wcc.lookup_viaf("v-1")
    assert mock_get_2.call_count == 0
    assert r.qids == ("Q999",)

    # Third lookup at t = 1000 + 31 days — expired, must refetch.
    thirty_one_days = 31 * 86400
    payload2 = _sparql_payload([_binding("Q1000")])
    with patch.object(wcc, "_now", side_effect=[1000.0 + thirty_one_days, 1000.0 + thirty_one_days]):
        with patch.object(
            requests.Session, "get", return_value=_ok_response(payload2)
        ) as mock_get_3:
            r2 = wcc.lookup_viaf("v-1")
    assert mock_get_3.call_count == 1
    assert r2.qids == ("Q1000",)


def test_corrupt_cache_recovers_gracefully(tmp_path: Path) -> None:
    """Test 9: a malformed cache file is treated as a miss + rewritten."""
    import converter.authority.wikidata_crosscheck as wcc

    cache_file = wcc._cache_path()
    cache_file.write_text("{not valid json", encoding="utf-8")

    payload = _sparql_payload([_binding("Q5")])
    with patch.object(requests.Session, "get", return_value=_ok_response(payload)) as mock_get:
        result = wcc.lookup_viaf("777")

    assert mock_get.call_count == 1
    assert result.qids == ("Q5",)
    # Cache was rewritten cleanly.
    rewritten = json.loads(cache_file.read_text(encoding="utf-8"))
    assert "777" in rewritten


def test_overmerge_table_pair_collision_detection() -> None:
    """Test 10: two MARC names with two distinct Mazal IDs sharing one VIAF
    is the F3 signature."""
    import converter.authority.wikidata_crosscheck as wcc

    table = wcc.OverMergeTable()
    table.record_mazal_pair("שמשון, יצחק בן ברוך", "NLI:1", "79251093")
    table.record_mazal_pair("ברוך בן יצחק בן שמשון", "NLI:2", "79251093")
    # A third VIAF that is *not* an over-merge: only one Mazal ID.
    table.record_mazal_pair("איש פלוני", "NLI:3", "11111")
    table.record_mazal_pair("איש פלוני", "NLI:3", "11111")

    collisions = table.detect_pair_collision()
    assert collisions == {"79251093"}


def test_disable_env_var_short_circuits_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 11: setting the disable env var makes ``is_enabled`` return False."""
    import converter.authority.wikidata_crosscheck as wcc

    monkeypatch.setenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", "1")
    assert wcc.is_enabled() is False

    monkeypatch.delenv("MHM_DISABLE_WIKIDATA_CROSSCHECK")
    assert wcc.is_enabled() is True
