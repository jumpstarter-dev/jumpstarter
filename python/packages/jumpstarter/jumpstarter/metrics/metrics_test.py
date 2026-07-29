"""JEP-0013 Phase 2 exporter metrics tests."""

from __future__ import annotations

import re
import urllib.error
import urllib.request

import pytest

from jumpstarter.metrics import (
    DEFAULT_EXEMPLAR_KEYS,
    get_registry,
    reset_registry_for_tests,
    start_metrics_server,
)

SERIES = (
    "jumpstarter_operations_total",
    "jumpstarter_operation_duration_seconds",
    "jumpstarter_operation_errors_total",
    "jumpstarter_stream_bytes_total",
    "jumpstarter_active_sessions",
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


def test_default_exemplar_keys():
    assert DEFAULT_EXEMPLAR_KEYS == ("client", "lease_id")


def test_generate_latest_contains_named_series_after_increments():
    reg = get_registry()
    exemplars = {"client": "ci-bot", "lease_id": "lease-abc"}
    reg.record_operation(
        exporter="lab-01",
        operation="on",
        result="success",
        driver_type="power",
        duration_seconds=0.05,
        exemplars=exemplars,
    )
    reg.record_operation(
        exporter="lab-01",
        operation="flash",
        result="failure",
        driver_type="storage",
        duration_seconds=1.2,
        exemplars=exemplars,
        error_type="timeout",
    )
    reg.add_stream_bytes(
        exporter="lab-01",
        driver_type="serial",
        direction="tx",
        nbytes=128,
        exemplars=exemplars,
    )
    reg.set_active_sessions(exporter="lab-01", value=1)

    body = reg.generate_latest().decode()
    for name in SERIES:
        assert name in body, f"expected series {name} in exposition"

    assert 'jumpstarter_operations_total{' in body or "jumpstarter_operations_total{" in body
    assert 'exporter="lab-01"' in body
    assert 'operation="on"' in body
    assert 'result="success"' in body
    assert 'driver_type="power"' in body
    assert "jumpstarter_operation_duration_seconds_bucket" in body or (
        "jumpstarter_operation_duration_seconds_count" in body
    )
    assert 'error_type="timeout"' in body
    assert 'direction="tx"' in body
    assert "jumpstarter_active_sessions" in body


def test_exemplars_include_client_and_lease_id():
    reg = get_registry()
    reg.record_operation(
        exporter="lab-01",
        operation="on",
        result="success",
        driver_type="power",
        duration_seconds=0.01,
        exemplars={"client": "ci-bot", "lease_id": "lease-xyz"},
    )
    body = reg.generate_latest().decode()
    # OpenMetrics exemplar form: # {client="...",lease_id="..."}
    assert re.search(r'client="ci-bot"', body)
    assert re.search(r'lease_id="lease-xyz"', body)
    assert "# {" in body or " # {" in body


def test_metrics_http_endpoint_serves_prometheus_text():
    reg = get_registry()
    reg.set_active_sessions(exporter="lab-01", value=2)
    listen = start_metrics_server("127.0.0.1:0", registry=reg)
    assert listen, "expected non-empty listen address"

    with urllib.request.urlopen(f"http://{listen}/metrics", timeout=2) as resp:
        assert resp.status == 200
        body = resp.read().decode()
        ctype = resp.headers.get("Content-Type", "")
    assert "text/plain" in ctype or "openmetrics" in ctype
    assert "jumpstarter_active_sessions" in body


def test_metrics_server_disabled_when_addr_zero():
    assert start_metrics_server("0") == ""
    assert start_metrics_server("") == ""


def test_driver_call_increments_operations_and_active_sessions():
    """Minimal wiring: Session + DriverCall should bump named series."""
    from jumpstarter_driver_power.driver import MockPower

    from jumpstarter.common.utils import serve

    with serve(MockPower()) as client:
        client.on()
        body = get_registry().generate_latest().decode()
        assert "jumpstarter_active_sessions" in body
        assert "jumpstarter_operations_total" in body
        assert 'driver_type="power"' in body
        assert 'result="success"' in body
        assert 'operation="on"' in body or 'operation="On"' in body
