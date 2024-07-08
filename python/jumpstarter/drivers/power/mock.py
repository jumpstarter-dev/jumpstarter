from collections.abc import Generator
from . import Power, PowerReading


class MockPower(Power):
    def on(self):
        return "ok"

    def off(self):
        return "ok"

    def read(self) -> Generator[PowerReading, None, None]:
        yield PowerReading(5.0, 2.0)
