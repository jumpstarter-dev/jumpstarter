"""Telemetry adoption tests for the SD Wire storage mux."""

from __future__ import annotations

import logging

import pytest

from .driver import SDWire
from jumpstarter.metrics import get_registry
from jumpstarter.metrics._testing import reset_registry_for_tests


class FakeDevice:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[dict] = []

    def ctrl_transfer(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("usb error")
        return b"\x00"


def _sdwire(device: FakeDevice) -> SDWire:
    sdwire = object.__new__(SDWire)
    sdwire.dev = device
    sdwire.logger = logging.getLogger("test-sdwire")
    return sdwire


def _sample(series: str, **labels):
    for family in get_registry().collector_registry.collect():
        for sample in family.samples:
            if sample.name == series and all(
                sample.labels.get(key) == value for key, value in labels.items()
            ):
                return sample
    return None


def test_select_records_host_and_dut():
    reset_registry_for_tests()
    sdwire = _sdwire(FakeDevice())

    sdwire.select(0xF1)  # host
    sdwire.select(0xF0)  # dut

    for operation in ("host", "dut"):
        assert _sample(
            "jumpstarter_driver_sdwire_operations_total",
            exporter="unknown",
            operation=operation,
            result="success",
        ).value == 1.0
    assert _sample("jumpstarter_driver_sdwire_operation_seconds_count", operation="host") is not None


def test_select_failure_is_counted_and_reraised():
    reset_registry_for_tests()
    sdwire = _sdwire(FakeDevice(fail=True))

    with pytest.raises(RuntimeError, match="usb error"):
        sdwire.select(0xF1)

    assert _sample(
        "jumpstarter_driver_sdwire_operations_total", operation="host", result="failure"
    ).value == 1.0


def test_sdwire_uses_the_storage_category():
    reset_registry_for_tests()
    sdwire = _sdwire(FakeDevice())
    sdwire.select(0xF1)
    definitions = {d.name: d for d in get_registry().driver_catalog()}
    counter = definitions["jumpstarter_driver_sdwire_operations_total"]
    assert counter.namespace == "sdwire"
    assert counter.type == "counter"
