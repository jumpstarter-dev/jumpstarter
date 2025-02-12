from .common import PowerReading
from .driver import MockPower, SyncMockPower
from jumpstarter.common.utils import serve


def test_client_mock_power():
    with serve(MockPower()) as client:
        client.on()
        client.off()

        assert list(client.read()) == [
            PowerReading(voltage=0.0, current=0.0),
            PowerReading(voltage=5.0, current=2.0),
        ]


def test_client_sync_mock_power():
    with serve(SyncMockPower()) as client:
        client.on()
        client.off()

        assert list(client.read()) == [
            PowerReading(voltage=0.0, current=0.0),
            PowerReading(voltage=5.0, current=2.0),
        ]
