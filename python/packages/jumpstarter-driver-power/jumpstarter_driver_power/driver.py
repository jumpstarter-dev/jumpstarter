from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator, Generator

from .common import PowerReading
from jumpstarter.driver import Driver, export


class PowerInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.PowerClient"

    @abstractmethod
    async def on(self) -> None: ...

    @abstractmethod
    async def off(self) -> None: ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]: ...


class MockPower(PowerInterface, Driver):
    @export
    async def on(self) -> None:
        pass

    @export
    async def off(self) -> None:
        pass

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)


class SyncMockPower(PowerInterface, Driver):
    @export
    def on(self) -> None:
        pass

    @export
    def off(self) -> None:
        pass

    @export
    def read(self) -> Generator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)
