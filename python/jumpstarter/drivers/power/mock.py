from .base import Power, PowerReading


class MockPower(Power):
    def on(self):
        return True

    def off(self):
        return True

    def read(self) -> PowerReading:
        return PowerReading(5.0, 2.0)
