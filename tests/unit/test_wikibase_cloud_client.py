"""Tests for the read-only Wikibase Cloud connection helper."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
import requests
from converter.wikibase.cloud_client import WikibaseCloudClient, WikibaseEndpointConfig


class _FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        http_error: requests.RequestException | None = None,
        json_error: ValueError | None = None,
    ) -> None:
        self._payload = payload
        self._http_error = http_error
        self._json_error = json_error

    def raise_for_status(self) -> None:
        if self._http_error is not None:
            raise self._http_error

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def test_endpoint_config_normalizes_api_url() -> None:
    assert WikibaseEndpointConfig("https://example.wikibase.cloud").api_url == (
        "https://example.wikibase.cloud/w/api.php"
    )
    assert WikibaseEndpointConfig("https://example.wikibase.cloud/").api_url == (
        "https://example.wikibase.cloud/w/api.php"
    )
    assert WikibaseEndpointConfig("https://example.wikibase.cloud/w/api.php").api_url == (
        "https://example.wikibase.cloud/w/api.php"
    )


def test_mhm_hmo_config_points_to_wikibase_cloud() -> None:
    config = WikibaseCloudClient.config_for_mhm_hmo_cloud()

    assert config.base_url == "https://mhm-hmo.wikibase.cloud"
    assert config.api_url == "https://mhm-hmo.wikibase.cloud/w/api.php"
    assert config.display_name == "MHM HMO Wikibase"


def test_connection_success_parses_siteinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Mapping[str, str], float]] = []
    payload: dict[str, object] = {
        "query": {
            "general": {
                "sitename": "MHM HMO",
                "generator": "MediaWiki 1.41.0",
            }
        }
    }

    def fake_get(
        self: requests.Session,
        url: str,
        *,
        params: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        calls.append((url, params, timeout))
        return _FakeResponse(payload)

    monkeypatch.setattr(requests.Session, "get", fake_get)

    result = WikibaseCloudClient(
        WikibaseEndpointConfig("https://example.wikibase.cloud/"),
        timeout=3.5,
    ).test_connection()

    assert result.ok is True
    assert result.site_name == "MHM HMO"
    assert result.generator == "MediaWiki 1.41.0"
    assert result.api_url == "https://example.wikibase.cloud/w/api.php"
    assert result.message == "Connection successful"
    assert calls == [
        (
            "https://example.wikibase.cloud/w/api.php",
            {
                "action": "query",
                "meta": "siteinfo",
                "siprop": "general",
                "format": "json",
            },
            3.5,
        )
    ]


def test_connection_handles_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(
        self: requests.Session,
        url: str,
        *,
        params: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        return _FakeResponse({"error": {"code": "badvalue", "info": "Invalid value"}})

    monkeypatch.setattr(requests.Session, "get", fake_get)

    result = WikibaseCloudClient(WikibaseEndpointConfig("https://example.org")).test_connection()

    assert result.ok is False
    assert result.message == "API error badvalue: Invalid value"


def test_connection_handles_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(
        self: requests.Session,
        url: str,
        *,
        params: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        raise requests.Timeout("timed out")

    monkeypatch.setattr(requests.Session, "get", fake_get)

    result = WikibaseCloudClient(
        WikibaseEndpointConfig("https://example.org", display_name="Example")
    ).test_connection()

    assert result.ok is False
    assert result.site_name == "Example"
    assert result.message == "Network error: timed out"


def test_connection_handles_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(
        self: requests.Session,
        url: str,
        *,
        params: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        return _FakeResponse({}, json_error=ValueError("not json"))

    monkeypatch.setattr(requests.Session, "get", fake_get)

    result = WikibaseCloudClient(WikibaseEndpointConfig("https://example.org")).test_connection()

    assert result.ok is False
    assert result.message == "Invalid JSON response: not json"


def test_connection_handles_unexpected_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(
        self: requests.Session,
        url: str,
        *,
        params: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        return _FakeResponse({"query": {}})

    monkeypatch.setattr(requests.Session, "get", fake_get)

    result = WikibaseCloudClient(WikibaseEndpointConfig("https://example.org")).test_connection()

    assert result.ok is False
    assert result.message == "Unexpected API response: missing query.general"
