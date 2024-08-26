import pytest

from jumpstarter.drivers.power.common import PowerReading
from jumpstarter.drivers.power.driver import MockPower, SyncMockPower

pytestmark = pytest.mark.anyio


async def test_driver_mock_power():
    driver = MockPower()

    assert await driver.on() == "ok"
    assert await driver.off() == "ok"

    assert [v async for v in driver.read()] == [
        PowerReading(voltage=0.0, current=0.0),
        PowerReading(voltage=5.0, current=2.0),
    ]


def test_driver_sync_mock_power():
    driver = SyncMockPower()

    assert driver.on() == "ok"
    assert driver.off() == "ok"

    assert list(driver.read()) == [
        PowerReading(voltage=0.0, current=0.0),
        PowerReading(voltage=5.0, current=2.0),
    ]
