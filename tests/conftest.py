"""Global test fixtures — block all real HTTP to prevent accidental data changes."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Force offscreen Qt rendering in CI (no display server on Windows/Linux runners)
if os.environ.get("CI"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _BlockedHTTP(Exception):
    """Raised when a test tries to make a real HTTP request."""


def _blocked_send(*args: object, **kwargs: object) -> None:
    raise _BlockedHTTP(
        "Tests must not make real HTTP requests. "
        "Mock the network call or use the 'allow_http' fixture."
    )


@pytest.fixture(autouse=True)
def _block_http(request: pytest.FixtureRequest) -> object:  # type: ignore[misc]
    """Automatically block all outgoing HTTP in every test.

    Tests that genuinely need HTTP (rare) can use:
        @pytest.mark.allow_http
    """
    if "allow_http" in request.keywords:
        yield
        return

    with patch("urllib3.connectionpool.HTTPConnectionPool.urlopen", side_effect=_blocked_send):
        yield
