"""Telemetry adoption tests for the reference power drivers."""

from __future__ import annotations

import asyncio  # noqa: TID251

from .driver import MockPower, SyncMockPower
from jumpstarter.metrics import get_registry
from jumpstarter.metrics._testing import reset_registry_for_tests


def _sample(series: str, **labels):
    for family in get_registry().collector_registry.collect():
        for sample in family.samples:
            if sample.name == series and all(
                sample.labels.get(key) == value for key, value in labels.items()
            ):
                return sample
    return None


def _new_power(cls=MockPower):
    return cls(labels={"jumpstarter.dev/name": "lab-01"}, children={})


def test_async_power_records_success_transitions():
    reset_registry_for_tests()
    power = _new_power()

    asyncio.run(power.on())
    asyncio.run(power.off())

    assert _sample(
        "jumpstarter_driver_power_transitions_total", exporter="lab-01", state="on", result="success"
    ).value == 1.0
    assert _sample(
        "jumpstarter_driver_power_transitions_total", exporter="lab-01", state="off", result="success"
    ).value == 1.0
    assert _sample("jumpstarter_driver_power_transition_seconds_count", state="on") is not None


def test_sync_power_records_transitions():
    reset_registry_for_tests()
    power = _new_power(SyncMockPower)

    power.on()

    assert _sample("jumpstarter_driver_power_transitions_total", state="on", result="success") is not None


def test_power_failure_is_counted_and_reraised(monkeypatch):
    reset_registry_for_tests()
    power = _new_power()

    def boom(*args, **kwargs):
        raise RuntimeError("relay stuck")

    monkeypatch.setattr(power.logger, "info", boom)

    try:
        asyncio.run(power.on())
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("failure must propagate")

    assert _sample(
        "jumpstarter_driver_power_transitions_total", state="on", result="failure"
    ).value == 1.0
    assert power._state == "off"


def test_power_uses_the_declared_category():
    reset_registry_for_tests()
    asyncio.run(_new_power().on())
    definitions = {d.name: d for d in get_registry().driver_catalog()}
    counter = definitions["jumpstarter_driver_power_transitions_total"]
    assert counter.namespace == "power"
    assert counter.type == "counter"
