from jumpstarter.drivers.power.mock import MockPower
from jumpstarter.drivers.power.base import PowerReading


def test_mock_power():
    p = MockPower()
    assert p.call("on", [])
    assert p.call("off", [])
    assert p.call("read", []) == PowerReading(5.0, 2.0)
