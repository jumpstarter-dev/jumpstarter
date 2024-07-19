from jumpstarter.drivers import Driver, DriverClient, drivercall, streamingdrivercall
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


class PowerInterface(ABC):
    def interface(self) -> str:
        return "power"

    def version(self) -> str:
        return "0.0.1"

    @abstractmethod
    async def on(self) -> str: ...

    @abstractmethod
    async def off(self) -> str: ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]: ...


class PowerClient(PowerInterface, DriverClient):
    async def on(self) -> str:
        return await self.drivercall("on")

    async def off(self) -> str:
        return await self.drivercall("off")

    async def read(self) -> AsyncGenerator[PowerReading, None]:
        async for v in self.streamingdrivercall("read"):
            yield PowerReading(voltage=v["voltage"], current=v["current"])


class MockPower(PowerInterface, Driver):
    @drivercall
    async def on(self) -> str:
        return "ok"

    @drivercall
    async def off(self) -> str:
        return "ok"

    @streamingdrivercall
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)
