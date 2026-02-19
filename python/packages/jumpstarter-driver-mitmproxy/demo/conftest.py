"""Pytest fixtures for the mitmproxy demo.

Run via::

    cd python/packages/jumpstarter-driver-mitmproxy/demo
    jmp shell --exporter exporter.yaml -- pytest . -v
"""

from __future__ import annotations

import socket
import threading
import time
from http.server import HTTPServer

import pytest
import requests
from backend import DemoBackendHandler

BACKEND_PORT = 9000
PROXY_PORT = 8080


def _wait_for_port(host: str, port: int, timeout: float = 10) -> bool:
    """TCP retry loop to confirm a port is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ── Backend server ────────────────────────────────────────────


@pytest.fixture(scope="session")
def backend_server():
    """Start the demo backend HTTP server in a daemon thread."""
    server = HTTPServer(("127.0.0.1", BACKEND_PORT), DemoBackendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert _wait_for_port("127.0.0.1", BACKEND_PORT), (
        f"Backend server did not start on port {BACKEND_PORT}"
    )
    yield server
    server.shutdown()


# ── Proxy ─────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def proxy(client):
    """Start the mitmproxy driver in mock mode.

    The ``client`` fixture is injected by Jumpstarter when tests
    run inside ``jmp shell --exporter exporter.yaml -- pytest``.
    """
    proxy = client.proxy
    proxy.start("mock")
    assert _wait_for_port("127.0.0.1", PROXY_PORT), (
        f"Proxy did not start on port {PROXY_PORT}"
    )
    yield proxy
    proxy.stop()


@pytest.fixture
def proxy_client(proxy):
    """Per-test wrapper: clears mocks and captures before/after each test."""
    proxy.clear_mocks()
    proxy.clear_captured_requests()
    yield proxy
    proxy.clear_mocks()
    proxy.clear_captured_requests()


@pytest.fixture
def http_session():
    """Requests session pre-configured to route through the proxy."""
    session = requests.Session()
    session.proxies = {"http": f"http://127.0.0.1:{PROXY_PORT}"}
    return session
