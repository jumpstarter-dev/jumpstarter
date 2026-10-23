from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator, Generator

from .common import PowerReading
from jumpstarter.driver import Driver, export


class PowerInterface(metaclass=ABCMeta):
    """Interface for power drivers.

    Drivers may also export an optional ``status()`` returning the power state (for
    example "on", "off" or "unknown"). It is not abstract, so drivers that can't
    report their state don't need it; ``PowerClient.status()`` returns None for them.
    """

    driver_type = "power"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.PowerClient"

    @abstractmethod
    async def on(self) -> None: ...

    @abstractmethod
    async def off(self) -> None: ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]: ...


class VirtualPowerInterface(metaclass=ABCMeta):
    driver_type = "power"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.VirtualPowerClient"

    @abstractmethod
    async def on(self) -> None: ...

    @abstractmethod
    async def off(self, destroy: bool = False) -> None: ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]: ...



class MockPower(PowerInterface, Driver):
    """
    MockPower is a mock driver implementing the PowerInterface

    >>> with serve(MockPower()) as power:
    ...     power.on()
    ...     assert power.status() == "on"
    ...     power.off()
    ...     assert power.status() == "off"
    ...
    ...     assert list(power.read()) == [
    ...         PowerReading(voltage=0.0, current=0.0),
    ...         PowerReading(voltage=5.0, current=2.0),
    ...     ]
    """

    _state: str = "off"

    @export
    async def on(self) -> None:
        self.logger.info("power on")
        self._state = "on"

    @export
    async def off(self) -> None:
        self.logger.info("power off")
        self._state = "off"

    @export
    async def status(self) -> str:
        return self._state

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)


class SyncMockPower(PowerInterface, Driver):
    """
    SyncMockPower is a mock driver implementing the PowerInterface

    >>> with serve(SyncMockPower()) as power:
    ...     power.on()
    ...     assert power.status() == "on"
    ...     power.off()
    ...     assert power.status() == "off"
    ...
    ...     assert list(power.read()) == [
    ...         PowerReading(voltage=0.0, current=0.0),
    ...         PowerReading(voltage=5.0, current=2.0),
    ...     ]
    """

    _state: str = "off"

    @export
    def on(self) -> None:
        self.logger.info("power on")
        self._state = "on"

    @export
    def off(self) -> None:
        self.logger.info("power off")
        self._state = "off"

    @export
    def status(self) -> str:
        return self._state

    @export
    def read(self) -> Generator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)
