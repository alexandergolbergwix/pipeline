"""Tests for ``converter.wikidata.wikidata_reverse_lookup``.

The repo's ``tests/conftest.py`` already blocks all real HTTP at the
``urllib3`` layer, so any test that asserts "no network was issued" is
automatically also asserting the function obeys offline-mode contracts —
a leaked ``requests.get`` would raise ``_BlockedHTTP`` and the test would
fail with a clear error rather than silently hit the live WDQS endpoint.

Tests that DO want to validate the SPARQL request shape mock
``requests.get`` explicitly with ``unittest.mock.patch``.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from converter.wikidata import wikidata_reverse_lookup as wrl

# ── Helpers ───────────────────────────────────────────────────────────


def _make_cache_file(
    tmp_path: Path,
    entries: dict[str, dict[str, Any]],
) -> Path:
    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps({"version": 1, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ok_sparql_response(label: str | None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    if label is None:
        resp.json.return_value = {"results": {"bindings": []}}
    else:
        resp.json.return_value = {
            "results": {
                "bindings": [
                    {
                        "label_en": {
                            "type": "literal",
                            "xml:lang": "en",
                            "value": label,
                        }
                    }
                ]
            }
        }
    return resp


# ── Tests ─────────────────────────────────────────────────────────────


def test_returns_cached_positive_without_network(tmp_path: Path) -> None:
    """A live positive entry is returned without hitting the network."""
    cache = _make_cache_file(
        tmp_path,
        {
            "משה בן מימון": {
                "english_label": "Maimonides",
                "fetched_at": _now_iso(),
                "ttl_seconds": 2_592_000,
            }
        },
    )
    with patch("converter.wikidata.wikidata_reverse_lookup.requests.get") as mock_get:
        result = wrl.lookup_english_label("משה בן מימון", cache_path=cache)
    assert result == "Maimonides"
    assert mock_get.call_count == 0


def test_returns_cached_negative_without_network(tmp_path: Path) -> None:
    """A live negative entry returns None without hitting the network."""
    cache = _make_cache_file(
        tmp_path,
        {
            "סופינו, עמנואל": {
                "english_label": None,
                "fetched_at": _now_iso(),
                "ttl_seconds": 86_400,
            }
        },
    )
    with patch("converter.wikidata.wikidata_reverse_lookup.requests.get") as mock_get:
        result = wrl.lookup_english_label("סופינו, עמנואל", cache_path=cache)
    assert result is None
    assert mock_get.call_count == 0


def test_returns_none_for_non_hebrew_input(tmp_path: Path) -> None:
    """Latin-only input short-circuits before any cache or network read."""
    cache = tmp_path / "cache.json"
    with patch("converter.wikidata.wikidata_reverse_lookup.requests.get") as mock_get:
        result = wrl.lookup_english_label("Maimonides", cache_path=cache)
    assert result is None
    assert mock_get.call_count == 0
    assert not cache.exists()


def test_returns_none_for_empty_input(tmp_path: Path) -> None:
    """Empty / whitespace input returns None and never touches anything."""
    cache = tmp_path / "cache.json"
    with patch("converter.wikidata.wikidata_reverse_lookup.requests.get") as mock_get:
        assert wrl.lookup_english_label("", cache_path=cache) is None
        assert wrl.lookup_english_label("   ", cache_path=cache) is None
        assert wrl.lookup_english_label(None, cache_path=cache) is None  # type: ignore[arg-type]
    assert mock_get.call_count == 0
    assert not cache.exists()


def test_network_disabled_returns_none_when_cache_miss(tmp_path: Path) -> None:
    """allow_network=False short-circuits to None on cache miss."""
    cache = tmp_path / "cache.json"
    with patch("converter.wikidata.wikidata_reverse_lookup.requests.get") as mock_get:
        result = wrl.lookup_english_label(
            "ישראל בן אליעזר",
            cache_path=cache,
            allow_network=False,
        )
    assert result is None
    assert mock_get.call_count == 0


def test_sparql_query_shape_when_network_called(tmp_path: Path) -> None:
    """The SPARQL request must contain the Hebrew literal and hit WDQS."""
    cache = tmp_path / "cache.json"
    with patch(
        "converter.wikidata.wikidata_reverse_lookup.requests.get",
        return_value=_ok_sparql_response("Maimonides"),
    ) as mock_get:
        result = wrl.lookup_english_label(
            "משה בן מימון",
            cache_path=cache,
            allow_network=True,
        )
    assert result == "Maimonides"
    assert mock_get.call_count == 1
    call = mock_get.call_args
    assert call.args[0] == "https://query.wikidata.org/sparql"
    params = call.kwargs.get("params", {})
    sent_query = params.get("query", "")
    assert "משה בן מימון" in sent_query
    assert "rdfs:label" in sent_query
    assert "skos:altLabel" in sent_query
    assert 'LANG(?label_en) = "en"' in sent_query
    assert "LIMIT 1" in sent_query
    headers = call.kwargs.get("headers", {})
    assert "MHMPipeline" in headers.get("User-Agent", "")
    assert headers.get("Accept") == "application/sparql-results+json"
    # The success path must persist a positive cache entry.
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert data["entries"]["משה בן מימון"]["english_label"] == "Maimonides"
    assert data["entries"]["משה בן מימון"]["ttl_seconds"] == 30 * 24 * 3600


def test_network_failure_returns_none_and_caches_negative(tmp_path: Path) -> None:
    """A network error returns None and writes a negative entry for 24h."""
    cache = tmp_path / "cache.json"
    with patch(
        "converter.wikidata.wikidata_reverse_lookup.requests.get",
        side_effect=RuntimeError("connection reset"),
    ) as mock_get:
        result = wrl.lookup_english_label(
            "שם לא מוכר",
            cache_path=cache,
            allow_network=True,
        )
    assert result is None
    assert mock_get.call_count == 1
    data = json.loads(cache.read_text(encoding="utf-8"))
    entry = data["entries"]["שם לא מוכר"]
    assert entry["english_label"] is None
    assert entry["ttl_seconds"] == 24 * 3600


def test_cache_ttl_expiry_triggers_refetch(tmp_path: Path) -> None:
    """An expired entry is ignored and the network is called again."""
    old_ts = (
        _dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(days=40)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache = _make_cache_file(
        tmp_path,
        {
            "משה בן מימון": {
                "english_label": "Stale Value",
                "fetched_at": old_ts,
                "ttl_seconds": 2_592_000,  # 30 days — already expired
            }
        },
    )
    with patch(
        "converter.wikidata.wikidata_reverse_lookup.requests.get",
        return_value=_ok_sparql_response("Maimonides"),
    ) as mock_get:
        result = wrl.lookup_english_label(
            "משה בן מימון",
            cache_path=cache,
            allow_network=True,
        )
    assert result == "Maimonides"
    assert mock_get.call_count == 1
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert data["entries"]["משה בן מימון"]["english_label"] == "Maimonides"


def test_env_var_mhm_no_network_blocks_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MHM_NO_NETWORK=true forces offline mode on cache miss."""
    monkeypatch.setenv("MHM_NO_NETWORK", "true")
    cache = tmp_path / "cache.json"
    with patch("converter.wikidata.wikidata_reverse_lookup.requests.get") as mock_get:
        result = wrl.lookup_english_label("משה בן מימון", cache_path=cache)
    assert result is None
    assert mock_get.call_count == 0


def test_no_result_caches_negative(tmp_path: Path) -> None:
    """A successful SPARQL response with zero bindings caches as negative."""
    cache = tmp_path / "cache.json"
    with patch(
        "converter.wikidata.wikidata_reverse_lookup.requests.get",
        return_value=_ok_sparql_response(None),
    ):
        result = wrl.lookup_english_label(
            "שם בלי תוצאה",
            cache_path=cache,
            allow_network=True,
        )
    assert result is None
    data = json.loads(cache.read_text(encoding="utf-8"))
    entry = data["entries"]["שם בלי תוצאה"]
    assert entry["english_label"] is None
    assert entry["ttl_seconds"] == 24 * 3600
