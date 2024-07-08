from .base import Power, PowerReading


class MockPower(Power):
    def on(self):
        return "ok"

    def off(self):
        return "ok"

    def read(self) -> PowerReading:
        return PowerReading(5.0, 2.0)
