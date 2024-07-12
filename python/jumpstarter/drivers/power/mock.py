from collections.abc import Generator
from . import Power, PowerReading


class MockPower(Power):
    async def on(self):
        return "ok"

    async def off(self):
        return "ok"

    async def read(self) -> Generator[PowerReading, None, None]:
        yield PowerReading(5.0, 2.0)
