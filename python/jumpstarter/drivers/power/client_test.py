from jumpstarter.common.utils import serve
from jumpstarter.drivers.power.common import PowerReading
from jumpstarter.drivers.power.driver import MockPower, SyncMockPower


def test_client_mock_power():
    with serve(MockPower()) as client:
        assert client.on() == "ok"
        assert client.off() == "ok"

        assert list(client.read()) == [
            PowerReading(voltage=0.0, current=0.0),
            PowerReading(voltage=5.0, current=2.0),
        ]


def test_client_sync_mock_power():
    with serve(SyncMockPower()) as client:
        assert client.on() == "ok"
        assert client.off() == "ok"

        assert list(client.read()) == [
            PowerReading(voltage=0.0, current=0.0),
            PowerReading(voltage=5.0, current=2.0),
        ]
