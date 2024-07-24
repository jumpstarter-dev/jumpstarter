from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from jumpstarter.drivers import Driver, DriverClient, export


@dataclass(kw_only=True)
class PowerReading:
    voltage: float
    current: float
    apparent_power: float = field(init=False)

    def __post_init__(self, *args):
        self.apparent_power = self.voltage * self.current


class PowerInterface(metaclass=ABCMeta):
    @classmethod
    def client_module(cls) -> str:
        return "jumpstarter.drivers.power"

    @classmethod
    def client_class(cls) -> str:
        return "PowerClient"

    @abstractmethod
    async def on(self) -> str: ...

    @abstractmethod
    async def off(self) -> str: ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]: ...


class PowerClient(PowerInterface, DriverClient):
    async def on(self) -> str:
        return await self.call("on")

    async def off(self) -> str:
        return await self.call("off")

    async def read(self) -> AsyncGenerator[PowerReading, None]:
        async for v in self.streamingcall("read"):
            yield PowerReading(voltage=v["voltage"], current=v["current"])


class MockPower(PowerInterface, Driver):
    @export
    async def on(self) -> str:
        return "ok"

    @export
    async def off(self) -> str:
        return "ok"

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)


class SyncMockPower(PowerInterface, Driver):
    @export
    def on(self) -> str:
        return "ok"

    @export
    def off(self) -> str:
        return "ok"

    @export
    def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)
