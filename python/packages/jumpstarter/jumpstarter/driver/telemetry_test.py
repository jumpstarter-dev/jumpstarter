"""Driver integration tests: attribution, events, streams (JEP-0013 Phase 4)."""

from __future__ import annotations

import ast
import asyncio  # noqa: TID251
import logging
import pathlib
from collections import deque

import anyio
import pytest
import structlog

import jumpstarter.metrics
from jumpstarter.common import LogSource
from jumpstarter.driver import Driver
from jumpstarter.exporter.logging import LogHandler
from jumpstarter.metrics import get_registry


class FakeDriver(Driver):
    driver_type = "power"
    metrics_namespace = "fake"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"


@pytest.fixture()
def driver(fresh_registry) -> FakeDriver:
    return FakeDriver(labels={"jumpstarter.dev/name": "fake-1"}, children={})


@pytest.fixture()
def captured(driver):
    """Attach a LogHandler to the driver logger and return its queue."""
    queue: deque = deque()
    handler = LogHandler(queue, source=LogSource.DRIVER)
    driver.logger.addHandler(handler)
    try:
        yield queue
    finally:
        driver.logger.removeHandler(handler)


def _sample(series: str, **labels):
    for family in get_registry().collector_registry.collect():
        for sample in family.samples:
            if sample.name == series and all(
                sample.labels.get(key) == value for key, value in labels.items()
            ):
                return sample
    return None


# -- attribution ----------------------------------------------------------


def test_metric_event_and_operation_metric_agree(driver, captured):
    with driver.metrics.driver_type("storage"):
        driver.metrics.counter("ops", "Ops").inc()
        driver.emit_event("read done", operation="read", result="success")
        driver._record_operation_metrics(
            operation="read", result="success", duration_seconds=0.01
        )

    assert _sample("jumpstarter_driver_fake_ops_total", driver_type="storage") is not None
    event = captured[-1]
    assert event.driver_type == "storage"
    assert _sample(
        "jumpstarter_operations_total", operation="read", driver_type="storage"
    ) is not None


def test_context_does_not_leak_across_tasks(driver):
    async def record(category: str) -> None:
        with driver.metrics.driver_type(category):
            await asyncio.sleep(0)
            driver.metrics.counter("ops", "Ops").inc()

    async def run() -> None:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(record, "storage")
            task_group.start_soon(record, "network")

    asyncio.run(run())

    for category in ("storage", "network"):
        assert _sample("jumpstarter_driver_fake_ops_total", driver_type=category) is not None


def test_stream_bytes_use_the_declared_type_not_the_context(driver):
    with driver.metrics.driver_type("storage"):
        get_registry().add_stream_bytes(
            exporter="fake-1",
            driver_type=driver.driver_type,
            direction="tx",
            nbytes=4,
        )

    # Documented limitation: the session passes the class attribute, so a
    # metrics context block does not re-attribute stream bytes.
    assert _sample("jumpstarter_stream_bytes_total", driver_type="power", direction="tx") is not None
    assert _sample("jumpstarter_stream_bytes_total", driver_type="storage") is None


def test_metrics_package_does_not_import_the_driver_package():
    root = pathlib.Path(jumpstarter.metrics.__file__).parent
    for path in root.glob("*.py"):
        if path.name.endswith("_test.py"):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("jumpstarter.driver"):
                raise AssertionError(f"{path.name} imports {node.module}; metrics must not depend on drivers")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("jumpstarter.driver"):
                        raise AssertionError(f"{path.name} imports {alias.name}; metrics must not depend on drivers")


# -- events ---------------------------------------------------------------


def test_event_carries_lease_id_and_result(driver, captured):
    with structlog.contextvars.bound_contextvars(lease_id="lease-1", client="ci-bot"):
        driver.emit_event(
            "flash failed",
            operation="flash",
            result="failure",
            fields={"image": "quay.io/example/image:1"},
        )

    event = captured[-1]
    assert event.driver_type == "power"
    assert event.operation == "flash"
    assert event.structured_fields["result"] == "failure"
    assert event.structured_fields["lease_id"] == "lease-1"
    assert event.structured_fields["client"] == "ci-bot"
    assert event.structured_fields["image"] == "quay.io/example/image:1"


def test_event_severity_maps_to_level(driver, captured):
    driver.emit_event("failed", operation="flash", result="failure", severity="warning")
    assert captured[-1].severity == "WARNING"


def test_event_without_context_does_not_raise(driver, captured):
    driver.emit_event("hello", operation="probe")
    event = captured[-1]
    assert "lease_id" not in event.structured_fields


def test_event_truncates_oversized_fields(driver, captured):
    fields = {f"key{i}": "v" * 300 for i in range(17)}
    driver.emit_event("many", fields=fields)

    event = captured[-1]
    assert len(event.structured_fields) == 16
    assert all(len(value) <= 256 for value in event.structured_fields.values())
    assert all(len(key) <= 64 for key in event.structured_fields)


def test_event_truncates_long_key_and_value(driver, captured):
    driver.emit_event("one", fields={"k" * 100: "v" * 300})

    event = captured[-1]
    key = next(iter(event.structured_fields))
    assert len(key) == 64
    assert len(event.structured_fields[key]) == 256


def test_event_drops_reserved_keys(driver, captured):
    driver.emit_event("reserved", fields={"message": "x", "levelname": "y", "keep": "z"})

    event = captured[-1]
    assert "message" not in event.structured_fields
    assert "levelname" not in event.structured_fields
    assert event.structured_fields["keep"] == "z"
    assert event.message == "reserved"


def test_event_failure_is_isolated(driver, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=driver.logger.name)

    def boom(*args, **kwargs):
        raise RuntimeError("logging exploded")

    monkeypatch.setattr(driver.logger, "log", boom)
    driver.emit_event("event")  # must not raise
