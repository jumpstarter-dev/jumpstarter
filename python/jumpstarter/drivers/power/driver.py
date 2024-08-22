from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator

from jumpstarter.driver import Driver, export

from .common import PowerReading


class PowerInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.power.client.PowerClient"

    @abstractmethod
    async def on(self) -> str: ...

    @abstractmethod
    async def off(self) -> str: ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]: ...


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
