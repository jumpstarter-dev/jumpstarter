"""
pytest fixtures for DUT HiL tests with mitmproxy.

These fixtures integrate the mitmproxy Jumpstarter driver into
your test workflow. The proxy client is available as ``client.proxy``
when using Jumpstarter's pytest plugin.

Usage:
    jmp start --exporter my-bench -- pytest tests/ -v
"""

from __future__ import annotations

import pytest
from jumpstarter_driver_mitmproxy.client import MitmproxyClient

# -- Proxy session fixtures --------------------------------------------------


@pytest.fixture(scope="session")
def proxy_session(client):
    """Start proxy for the entire test session.

    Uses mock mode with the web UI enabled so engineers can
    inspect traffic in the browser during test development.
    """
    proxy: MitmproxyClient = client.proxy
    proxy.start(mode="mock", web_ui=True)

    # Print the web UI URL for interactive debugging
    info = proxy.status()
    if info.get("web_ui_address"):
        print(f"\n>>> mitmweb UI: {info['web_ui_address']}")

    yield proxy
    proxy.stop()


@pytest.fixture
def proxy(proxy_session):
    """Per-test proxy fixture that clears mocks between tests.

    Inherits the session-scoped proxy but ensures each test
    starts with a clean mock slate.
    """
    proxy_session.clear_mocks()
    yield proxy_session
    proxy_session.clear_mocks()


# -- Scenario fixtures -------------------------------------------------------


@pytest.fixture
def mock_device_status(proxy):
    """Mock the device status endpoint with standard test data."""
    with proxy.mock_endpoint(
        "GET", "/api/v1/status",
        body={
            "id": "device-001",
            "status": "active",
            "uptime_s": 86400,
            "battery_pct": 85,
            "firmware_version": "2.5.1",
            "last_updated": "2026-02-13T10:00:00Z",
        },
    ):
        yield


@pytest.fixture
def mock_update_available(proxy):
    """Mock the update endpoint to report an available update."""
    with proxy.mock_endpoint(
        "GET", "/api/v1/updates/check",
        body={
            "update_available": True,
            "current_version": "2.5.1",
            "latest_version": "2.6.0",
            "download_url": "https://updates.example.com/v2.6.0.bin",
            "release_notes": "Bug fixes and performance improvements",
            "size_bytes": 524288000,
        },
    ):
        yield


@pytest.fixture
def mock_up_to_date(proxy):
    """Mock the update endpoint to report system is up to date."""
    with proxy.mock_endpoint(
        "GET", "/api/v1/updates/check",
        body={
            "update_available": False,
            "current_version": "2.5.1",
            "latest_version": "2.5.1",
        },
    ):
        yield


@pytest.fixture
def mock_backend_down(proxy):
    """Simulate all backend services returning 503."""
    with proxy.mock_endpoint(
        "GET", "/api/v1/*",
        status=503,
        body={"error": "Service Unavailable"},
    ):
        yield


@pytest.fixture
def mock_slow_backend(proxy):
    """Simulate a gateway timeout (504) from the backend."""
    with proxy.mock_endpoint(
        "GET", "/api/v1/*",
        status=504,
        body={"error": "Gateway Timeout"},
    ):
        yield


@pytest.fixture
def mock_auth_expired(proxy):
    """Simulate expired authentication token."""
    with proxy.mock_endpoint(
        "GET", "/api/v1/*",
        status=401,
        body={"error": "Token expired", "code": "AUTH_EXPIRED"},
    ):
        yield
