"""Automated demo tests for the mitmproxy Jumpstarter driver.

Each test class demonstrates a different capability of the proxy driver.
Run with::

    cd python/packages/jumpstarter-driver-mitmproxy/demo
    jmp shell --exporter exporter.yaml -- pytest . -v

The tests require the backend server fixture (started automatically)
and a running proxy (started automatically via the ``proxy`` fixture).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
BACKEND_URL = "http://127.0.0.1:9000"


# ── Passthrough (no mocks) ────────────────────────────────────


class TestPassthrough:
    """No mocks configured — requests flow through the proxy to the real backend."""

    def test_status_from_real_backend(
        self, backend_server, proxy_client, http_session,
    ):
        resp = http_session.get(f"{BACKEND_URL}/api/v1/status", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "real-backend"
        assert data["device_id"] == "DUT-REAL-001"

    def test_updates_from_real_backend(
        self, backend_server, proxy_client, http_session,
    ):
        resp = http_session.get(
            f"{BACKEND_URL}/api/v1/updates/check", timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "real-backend"
        assert data["update_available"] is False

    def test_telemetry_from_real_backend(
        self, backend_server, proxy_client, http_session,
    ):
        resp = http_session.post(
            f"{BACKEND_URL}/api/v1/telemetry",
            json={"cpu_temp": 42.5},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "real-backend"
        assert data["accepted"] is True


# ── Mock overrides ────────────────────────────────────────────


class TestMockOverride:
    """Setting mocks replaces real backend responses."""

    def test_mock_replaces_real_response(
        self, backend_server, proxy_client, http_session,
    ):
        proxy_client.set_mock(
            "GET", "/api/v1/status",
            body={
                "device_id": "DUT-MOCK-999",
                "status": "online",
                "firmware_version": "2.5.1",
                "source": "mock",
            },
        )
        time.sleep(1)  # hot-reload timing

        resp = http_session.get(
            "http://example.com/api/v1/status", timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "mock"
        assert data["device_id"] == "DUT-MOCK-999"

    def test_mock_error_status(
        self, backend_server, proxy_client, http_session,
    ):
        proxy_client.set_mock(
            "GET", "/api/v1/status",
            status=503,
            body={
                "error": "Service Unavailable",
                "source": "mock",
            },
        )
        time.sleep(1)

        resp = http_session.get(
            "http://example.com/api/v1/status", timeout=10,
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "Service Unavailable"

    def test_mock_endpoint_context_manager(
        self, backend_server, proxy_client, http_session,
    ):
        """mock_endpoint() auto-cleans up after the with block."""
        with proxy_client.mock_endpoint(
            "GET", "/api/v1/status",
            body={
                "device_id": "TEMP-MOCK",
                "source": "mock",
            },
        ):
            time.sleep(1)
            resp = http_session.get(
                "http://example.com/api/v1/status", timeout=10,
            )
            assert resp.json()["source"] == "mock"
            assert resp.json()["device_id"] == "TEMP-MOCK"

        # After context exit, the mock is removed
        time.sleep(1)
        mocks = proxy_client.list_mocks()
        assert "GET /api/v1/status" not in mocks


# ── Scenario loading ──────────────────────────────────────────


class TestScenarioLoading:
    """Load complete YAML scenario files to set up groups of mocks."""

    def test_load_happy_path(
        self, backend_server, proxy_client, http_session,
    ):
        proxy_client.load_mock_scenario(str(SCENARIOS_DIR / "happy-path"))
        time.sleep(1)

        resp = http_session.get(
            "http://example.com/api/v1/status", timeout=10,
        )
        data = resp.json()
        assert data["source"] == "mock"
        assert data["device_id"] == "DUT-MOCK-999"
        assert data["firmware_version"] == "2.5.1"

    def test_load_update_available(
        self, backend_server, proxy_client, http_session,
    ):
        proxy_client.load_mock_scenario(
            str(SCENARIOS_DIR / "update-available"),
        )
        time.sleep(1)

        resp = http_session.get(
            "http://example.com/api/v1/updates/check", timeout=10,
        )
        data = resp.json()
        assert data["source"] == "mock"
        assert data["update_available"] is True
        assert data["latest_version"] == "3.0.0"

    def test_load_backend_outage(
        self, backend_server, proxy_client, http_session,
    ):
        proxy_client.load_mock_scenario(
            str(SCENARIOS_DIR / "backend-outage"),
        )
        time.sleep(1)

        resp = http_session.get(
            "http://example.com/api/v1/status", timeout=10,
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "Service Unavailable"

    def test_mock_scenario_context_manager(
        self, backend_server, proxy_client, http_session,
    ):
        """mock_scenario() loads on entry, clears on exit."""
        with proxy_client.mock_scenario(
            str(SCENARIOS_DIR / "happy-path"),
        ):
            time.sleep(1)
            resp = http_session.get(
                "http://example.com/api/v1/status", timeout=10,
            )
            assert resp.json()["source"] == "mock"

        # After exit: mocks cleared → passthrough to real backend
        time.sleep(1)
        resp = http_session.get(
            f"{BACKEND_URL}/api/v1/status", timeout=10,
        )
        assert resp.json()["source"] == "real-backend"


# ── Request capture ───────────────────────────────────────────


class TestRequestCapture:
    """Demonstrate request capture and inspection."""

    def test_capture_context_manager(
        self, backend_server, proxy_client, http_session,
    ):
        proxy_client.set_mock(
            "GET", "/api/v1/status",
            body={"device_id": "CAP-001", "source": "mock"},
        )
        time.sleep(1)

        with proxy_client.capture() as cap:
            http_session.get(
                "http://example.com/api/v1/status", timeout=10,
            )
            cap.wait_for_request("GET", "/api/v1/status", 5.0)

        assert len(cap.requests) >= 1
        assert cap.requests[0]["method"] == "GET"
        assert cap.requests[0]["path"] == "/api/v1/status"

    def test_wait_for_request(
        self, backend_server, proxy_client, http_session,
    ):
        proxy_client.set_mock(
            "POST", "/api/v1/telemetry",
            body={"accepted": True, "source": "mock"},
        )
        time.sleep(1)

        http_session.post(
            "http://example.com/api/v1/telemetry",
            json={"cpu_temp": 42.5},
            timeout=10,
        )
        result = proxy_client.wait_for_request(
            "POST", "/api/v1/telemetry", 5.0,
        )
        assert result["method"] == "POST"
        assert result["path"] == "/api/v1/telemetry"
        assert result["response_status"] == 200

    def test_assert_request_made_with_wildcard(
        self, backend_server, proxy_client, http_session,
    ):
        proxy_client.set_mock(
            "GET", "/api/v1/config",
            body={"log_level": "debug", "source": "mock"},
        )
        time.sleep(1)

        http_session.get(
            "http://example.com/api/v1/config", timeout=10,
        )
        proxy_client.wait_for_request("GET", "/api/v1/config", 5.0)

        # Exact match
        result = proxy_client.assert_request_made("GET", "/api/v1/config")
        assert result["method"] == "GET"

        # Wildcard match
        result = proxy_client.assert_request_made("GET", "/api/v1/*")
        assert result["method"] == "GET"

        # Should fail for unmatched
        with pytest.raises(AssertionError, match="not captured"):
            proxy_client.assert_request_made("DELETE", "/api/v1/config")
