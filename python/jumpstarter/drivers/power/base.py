from jumpstarter.drivers import Driver, DriverClient
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass(kw_only=True)
class PowerReading:
    voltage: float
    current: float
    apparent_power: float = field(init=False)

    def __post_init__(self):
        self.apparent_power = self.voltage * self.current


class Power(ABC):
    @abstractmethod
    async def on(self) -> str: ...

    @abstractmethod
    async def off(self) -> str: ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]: ...


class PowerClient(DriverClient, Power):
    async def on(self) -> str:
        return self.drivercall("on")

    async def off(self) -> str:
        return self.drivercall("off")

    async def read(self) -> AsyncGenerator[PowerReading, None]:
        async for v in self.streamingdrivercall("read"):
            yield v


class MockPower(Driver, Power):
    async def on(self) -> str:
        return "ok"

    async def off(self) -> str:
        return "ok"

    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(0.0, 0.0)
        yield PowerReading(5.0, 2.0)
