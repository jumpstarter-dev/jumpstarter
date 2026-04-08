from abc import abstractmethod
from collections.abc import AsyncGenerator, Generator

from .common import PowerReading
from jumpstarter.driver import Driver, DriverInterface, export


class PowerInterface(DriverInterface):
    """Control power delivery to a device under test."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.PowerClient"

    @abstractmethod
    async def on(self):
        """Energize the power relay, delivering power to the DUT."""
        ...

    @abstractmethod
    async def off(self):
        """De-energize the power relay, cutting power to the DUT."""
        ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        """Stream real-time power measurements from the DUT power rail."""
        ...


class VirtualPowerInterface(DriverInterface):
    """Control a virtual power source with optional resource cleanup."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.VirtualPowerClient"

    @abstractmethod
    async def on(self):
        """Activate the virtual power source."""
        ...

    @abstractmethod
    async def off(self, destroy: bool = False):
        """Deactivate the virtual power source, optionally destroying associated resources."""
        ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        """Stream real-time power measurements from the virtual power source."""
        ...



class MockPower(PowerInterface, Driver):
    """
    MockPower is a mock driver implementing the PowerInterface

    >>> with serve(MockPower()) as power:
    ...     power.on()
    ...     power.off()
    ...
    ...     assert list(power.read()) == [
    ...         PowerReading(voltage=0.0, current=0.0),
    ...         PowerReading(voltage=5.0, current=2.0),
    ...     ]
    """

    @export
    async def on(self) -> None:
        self.logger.info("power on")

    @export
    async def off(self) -> None:
        self.logger.info("power off")

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)


class SyncMockPower(PowerInterface, Driver):
    """
    SyncMockPower is a mock driver implementing the PowerInterface

    >>> with serve(SyncMockPower()) as power:
    ...     power.on()
    ...     power.off()
    ...
    ...     assert list(power.read()) == [
    ...         PowerReading(voltage=0.0, current=0.0),
    ...         PowerReading(voltage=5.0, current=2.0),
    ...     ]
    """

    @export
    def on(self) -> None:
        self.logger.info("power on")

    @export
    def off(self) -> None:
        self.logger.info("power off")

    @export
    def read(self) -> Generator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)
