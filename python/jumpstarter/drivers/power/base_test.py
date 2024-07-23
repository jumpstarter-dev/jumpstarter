import pytest

from jumpstarter.common.grpc import serve
from jumpstarter.drivers.power import MockPower, PowerReading, SyncMockPower

pytestmark = pytest.mark.anyio


async def test_drivers_power_mock():
    async with serve(MockPower(labels={"jumpstarter.dev/name": "mock"})) as client:
        assert await client.on() == "ok"
        assert await client.off() == "ok"

        assert [reading async for reading in client.read()] == [
            PowerReading(voltage=0.0, current=0.0),
            PowerReading(voltage=5.0, current=2.0),
        ]


async def test_drivers_sync_power_mock():
    async with serve(SyncMockPower(labels={"jumpstarter.dev/name": "mock"})) as client:
        assert await client.on() == "ok"
        assert await client.off() == "ok"

        assert [reading async for reading in client.read()] == [
            PowerReading(voltage=0.0, current=0.0),
            PowerReading(voltage=5.0, current=2.0),
        ]
