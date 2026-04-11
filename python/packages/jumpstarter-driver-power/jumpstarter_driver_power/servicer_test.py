"""Unit tests for the PowerInterface servicer adapter."""

from unittest.mock import MagicMock

import grpc
import pytest

from jumpstarter_driver_power.protocol.power.v1.servicer import PowerInterfaceServicer, SERVICE_NAME
from jumpstarter.exporter.registry import DriverRegistry


class _AbortError(Exception):
    pass


def _make_context(uuid=None):
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    metadata = []
    if uuid is not None:
        metadata.append(("x-jumpstarter-driver-uuid", uuid))
    ctx.invocation_metadata.return_value = metadata
    ctx.abort = MagicMock(side_effect=_AbortError)
    return ctx


class FakeAsyncPowerDriver:
    """Fake async driver with @export-style methods."""

    def __init__(self):
        self.on_called = False
        self.off_called = False

    async def on(self):
        self.on_called = True

    async def off(self):
        self.off_called = True

    async def read(self):
        from jumpstarter_driver_power.common import PowerReading

        yield PowerReading(voltage=5.0, current=2.0)
        yield PowerReading(voltage=3.3, current=1.0)


@pytest.mark.anyio
async def test_on_delegates_to_driver():
    reg = DriverRegistry()
    driver = FakeAsyncPowerDriver()
    reg.register("uuid-1", SERVICE_NAME, driver)

    servicer = PowerInterfaceServicer(reg)
    ctx = _make_context()
    from google.protobuf.empty_pb2 import Empty

    result = await servicer.On(Empty(), ctx)
    assert driver.on_called
    assert isinstance(result, Empty)


@pytest.mark.anyio
async def test_off_delegates_to_driver():
    reg = DriverRegistry()
    driver = FakeAsyncPowerDriver()
    reg.register("uuid-1", SERVICE_NAME, driver)

    servicer = PowerInterfaceServicer(reg)
    ctx = _make_context()
    from google.protobuf.empty_pb2 import Empty

    result = await servicer.Off(Empty(), ctx)
    assert driver.off_called
    assert isinstance(result, Empty)


@pytest.mark.anyio
async def test_read_streams_power_readings():
    reg = DriverRegistry()
    driver = FakeAsyncPowerDriver()
    reg.register("uuid-1", SERVICE_NAME, driver)

    servicer = PowerInterfaceServicer(reg)
    ctx = _make_context()
    from google.protobuf.empty_pb2 import Empty

    readings = []
    async for reading in servicer.Read(Empty(), ctx):
        readings.append((reading.voltage, reading.current))

    assert readings == [(5.0, 2.0), (3.3, 1.0)]


@pytest.mark.anyio
async def test_uuid_routing():
    reg = DriverRegistry()
    d1 = FakeAsyncPowerDriver()
    d2 = FakeAsyncPowerDriver()
    reg.register("uuid-1", SERVICE_NAME, d1)
    reg.register("uuid-2", SERVICE_NAME, d2)

    servicer = PowerInterfaceServicer(reg)
    ctx = _make_context(uuid="uuid-2")
    from google.protobuf.empty_pb2 import Empty

    await servicer.On(Empty(), ctx)
    assert not d1.on_called
    assert d2.on_called
