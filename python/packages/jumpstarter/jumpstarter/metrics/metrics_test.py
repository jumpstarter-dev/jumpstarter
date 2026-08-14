"""Exporter-local Prometheus metrics tests."""

from __future__ import annotations

import re
import urllib.error
import urllib.request

import pytest
import structlog

from jumpstarter.client.core import DriverError, DriverMethodNotImplemented
from jumpstarter.driver import Driver, export
from jumpstarter.metrics import (
    DEFAULT_EXEMPLAR_KEYS,
    get_registry,
    start_metrics_server,
)
from jumpstarter.metrics._testing import reset_registry_for_tests
from jumpstarter.metrics.registry import (
    exemplars_from_log_context,
    exporter_from_log_context,
    filter_exemplars,
)
from jumpstarter.metrics.server import _parse_bind_addr

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


def test_get_registry_returns_eager_singleton():
    first = get_registry()
    second = get_registry()
    assert first is second
    replaced = reset_registry_for_tests()
    assert replaced is get_registry()
    assert replaced is not first


def _sample_value(reg, name: str, labels: dict[str, str]) -> float | None:
    for family in reg.collector_registry.collect():
        for sample in family.samples:
            if sample.name != name:
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return float(sample.value)
    return None


def test_default_exemplar_keys():
    assert DEFAULT_EXEMPLAR_KEYS == ("client", "lease_id")


def test_filter_exemplars_allowlist_and_empty():
    assert filter_exemplars(None) is None
    assert filter_exemplars({}) is None
    assert filter_exemplars({"client": "", "lease_id": "x"}) == {"lease_id": "x"}
    assert filter_exemplars({"client": "c", "lease_id": "l", "ignored": "nope"}) == {
        "client": "c",
        "lease_id": "l",
    }


def test_exemplars_and_exporter_from_log_context():
    structlog.contextvars.clear_contextvars()
    assert exemplars_from_log_context() is None
    assert exporter_from_log_context() == "unknown"

    structlog.contextvars.bind_contextvars(client="ci-bot", lease_id="lease-1", exporter="lab-01")
    try:
        assert exemplars_from_log_context() == {"client": "ci-bot", "lease_id": "lease-1"}
        assert exporter_from_log_context() == "lab-01"
    finally:
        structlog.contextvars.clear_contextvars()


def test_parse_bind_addr_defaults_to_loopback():
    assert _parse_bind_addr(":8080") == ("127.0.0.1", 8080)
    assert _parse_bind_addr("8080") == ("127.0.0.1", 8080)
    assert _parse_bind_addr("0.0.0.0:9090") == ("0.0.0.0", 9090)
    assert _parse_bind_addr(":0") == ("127.0.0.1", 0)
    assert _parse_bind_addr("127.0.0.1:0")[0] == "127.0.0.1"


def test_add_stream_bytes_ignores_non_positive():
    reg = get_registry()
    reg.add_stream_bytes(exporter="lab-01", driver_type="serial", direction="tx", nbytes=0)
    reg.add_stream_bytes(exporter="lab-01", driver_type="serial", direction="tx", nbytes=-5)
    body = reg.generate_latest().decode()
    # Series may be absent entirely until a positive observation.
    assert 'direction="tx"' not in body or _sample_value(
        reg,
        "jumpstarter_stream_bytes_total",
        {"exporter": "lab-01", "driver_type": "serial", "direction": "tx"},
    ) in (None, 0.0)


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

    assert 'exporter="lab-01"' in body
    assert 'operation="on"' in body
    assert 'result="success"' in body
    assert 'driver_type="power"' in body
    assert "jumpstarter_operation_duration_seconds_bucket" in body or (
        "jumpstarter_operation_duration_seconds_count" in body
    )
    assert 'error_type="timeout"' in body
    assert 'direction="tx"' in body

    success = _sample_value(
        reg,
        "jumpstarter_operations_total",
        {
            "exporter": "lab-01",
            "operation": "on",
            "result": "success",
            "driver_type": "power",
        },
    )
    assert success == 1.0

    errors = _sample_value(
        reg,
        "jumpstarter_operation_errors_total",
        {
            "exporter": "lab-01",
            "operation": "flash",
            "driver_type": "storage",
            "error_type": "timeout",
        },
    )
    assert errors == 1.0

    stream = _sample_value(
        reg,
        "jumpstarter_stream_bytes_total",
        {"exporter": "lab-01", "driver_type": "serial", "direction": "tx"},
    )
    assert stream == 128.0


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
    assert re.search(
        r'jumpstarter_operations_total.*# \{.*client="ci-bot".*lease_id="lease-xyz"',
        body,
        re.DOTALL,
    )


def test_metrics_http_endpoint_serves_prometheus_text():
    reg = get_registry()
    reg.set_active_sessions(exporter="lab-01", value=2)
    listen, shutdown = start_metrics_server("127.0.0.1:0", registry=reg)
    assert listen, "expected non-empty listen address"
    assert shutdown is not None
    try:
        with urllib.request.urlopen(f"http://{listen}/metrics", timeout=2) as resp:
            assert resp.status == 200
            body = resp.read().decode()
            ctype = resp.headers.get("Content-Type", "")
        assert "text/plain" in ctype or "openmetrics" in ctype
        assert "jumpstarter_active_sessions" in body
    finally:
        shutdown()


def test_metrics_server_disabled_when_addr_zero():
    listen, shutdown = start_metrics_server("0")
    assert listen == ""
    assert shutdown is None
    listen, shutdown = start_metrics_server("")
    assert listen == ""
    assert shutdown is None


def test_metrics_server_ephemeral_bind_returns_concrete_port():
    listen, shutdown = start_metrics_server(":0")
    assert shutdown is not None
    try:
        assert listen.startswith("127.0.0.1:")
        port = int(listen.rsplit(":", 1)[1])
        assert port > 0
        with urllib.request.urlopen(f"http://{listen}/metrics", timeout=2) as resp:
            assert resp.status == 200
    finally:
        shutdown()


def test_metrics_server_shutdown_stops_listening():
    listen, shutdown = start_metrics_server("127.0.0.1:0")
    assert listen and shutdown is not None
    with urllib.request.urlopen(f"http://{listen}/metrics", timeout=2) as resp:
        assert resp.status == 200
    shutdown()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://{listen}/metrics", timeout=1)


def test_metrics_server_bind_failure_is_non_fatal():
    """A taken fixed port must not abort the exporter process."""
    import socket

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    occupied_port = holder.getsockname()[1]
    try:
        listen, shutdown = start_metrics_server(f"127.0.0.1:{occupied_port}")
        assert listen == ""
        assert shutdown is None
    finally:
        holder.close()


def test_metrics_server_invalid_bind_addr_is_non_fatal():
    """Non-numeric ports must not abort the exporter process."""
    listen, shutdown = start_metrics_server("127.0.0.1:not-a-port")
    assert listen == ""
    assert shutdown is None


class _TimeoutDriver(Driver):
    driver_type = "power"

    @classmethod
    def client(cls):
        return "jumpstarter.client.DriverClient"

    @export
    def boom(self):
        raise TimeoutError("deadline exceeded")


def test_driver_call_maps_timeout_error_type():
    from jumpstarter.common.utils import serve

    with serve(_TimeoutDriver()) as client:
        with pytest.raises(DriverError):
            client.call("boom")
        body = get_registry().generate_latest().decode()
        assert 'error_type="timeout"' in body
        assert 'operation="boom"' in body
        assert 'result="failure"' in body


class _Unencodable:
    """Intentionally not JSON-serializable for encode_value failure tests."""


class _UnencodableResultDriver(Driver):
    driver_type = "power"

    @classmethod
    def client(cls):
        return "jumpstarter.client.DriverClient"

    @export
    def boom(self):
        return _Unencodable()


def test_driver_call_encode_failure_records_failure_not_success():
    """Success metrics must not be recorded if response serialization fails."""
    from jumpstarter.common.utils import serve

    with serve(_UnencodableResultDriver()) as client:
        with pytest.raises(DriverError):
            client.call("boom")
        success = _sample_value(
            get_registry(),
            "jumpstarter_operations_total",
            {
                "exporter": "unknown",
                "operation": "boom",
                "result": "success",
                "driver_type": "power",
            },
        )
        failure = _sample_value(
            get_registry(),
            "jumpstarter_operations_total",
            {
                "exporter": "unknown",
                "operation": "boom",
                "result": "failure",
                "driver_type": "power",
            },
        )
        assert success in (None, 0.0)
        assert failure == 1.0


def test_driver_call_succeeds_when_metrics_recording_raises(monkeypatch):
    """Metrics failures must not discard a successful DriverCall response."""
    from jumpstarter_driver_power.driver import MockPower

    from jumpstarter.common.utils import serve
    from jumpstarter.metrics.registry import MetricsRegistry

    def _boom(*_args, **_kwargs):
        raise RuntimeError("metrics broken")

    monkeypatch.setattr(MetricsRegistry, "record_operation", _boom)

    with serve(MockPower()) as client:
        client.on()


def test_unknown_driver_method_does_not_record_operation_metric():
    """AbortError from method lookup must not create an operation time series."""
    from jumpstarter_driver_power.driver import MockPower

    from jumpstarter.common.utils import serve

    with serve(MockPower()) as client:
        with pytest.raises(DriverMethodNotImplemented):
            client.call("definitely_not_a_real_method_xyz")
        body = get_registry().generate_latest().decode()
        assert "definitely_not_a_real_method_xyz" not in body
        assert 'error_type="internal_error"' not in body


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

        success = _sample_value(
            get_registry(),
            "jumpstarter_operations_total",
            {
                "exporter": "unknown",
                "operation": "on",
                "result": "success",
                "driver_type": "power",
            },
        )
        if success is None:
            # Exporter label comes from session name when set.
            assert 'result="success"' in body
            assert 'operation="on"' in body or 'operation="On"' in body
        else:
            assert success == 1.0
