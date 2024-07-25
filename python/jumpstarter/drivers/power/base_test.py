import pytest
from anyio.to_thread import run_sync

from jumpstarter.common.grpc import serve
from jumpstarter.drivers.power import MockPower, PowerReading, SyncMockPower

pytestmark = pytest.mark.anyio


async def test_drivers_power_mock():
    async with serve(MockPower(name="power")) as client:

        def blocking():
            assert client.on() == "ok"
            assert client.off() == "ok"

        await run_sync(blocking)

        assert [reading async for reading in client.read()] == [
            PowerReading(voltage=0.0, current=0.0),
            PowerReading(voltage=5.0, current=2.0),
        ]


async def test_drivers_sync_power_mock():
    async with serve(SyncMockPower(name="power")) as client:

        def blocking():
            assert client.on() == "ok"
            assert client.off() == "ok"

        await run_sync(blocking)

        assert [reading async for reading in client.read()] == [
            PowerReading(voltage=0.0, current=0.0),
            PowerReading(voltage=5.0, current=2.0),
        ]
