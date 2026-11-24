import time
from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator, Generator
from typing import TYPE_CHECKING

from .common import PowerReading
from jumpstarter.driver import Driver, export

if TYPE_CHECKING:
    from jumpstarter.metrics.driver import DriverMetrics


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




class _PowerTelemetry:
    """Shared telemetry for the reference power drivers (JEP-0013 Phase 4)."""

    if TYPE_CHECKING:
        # Provided by Driver, which every class using this mixin also inherits.
        # Declared here so the mixin type-checks on its own.
        metrics: DriverMetrics

        def emit_event(self, message: str, **attributes: object) -> None: ...

    def _record_transition(self, state: str, result: str, started: float) -> None:
        metrics = self.metrics
        metrics.counter(
            "transitions",
            "Power transitions performed by the driver",
            labels=("state", "result"),
        ).labels(state=state, result=result).inc()
        metrics.histogram(
            "transition_seconds",
            "Time spent in a power transition",
            labels=("state",),
            buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
        ).labels(state=state).observe(time.perf_counter() - started)
        self.emit_event(f"power {state}", operation=state, result=result)


class MockPower(PowerInterface, _PowerTelemetry, Driver):
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

    async def _transition(self, state: str) -> None:
        started = time.perf_counter()
        try:
            self.logger.info("power %s", state)
            self._state = state
        except Exception:
            self._record_transition(state, "failure", started)
            raise
        self._record_transition(state, "success", started)

    @export
    async def on(self) -> None:
        await self._transition("on")

    @export
    async def off(self) -> None:
        await self._transition("off")

    @export
    async def status(self) -> str:
        return self._state

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)


class SyncMockPower(PowerInterface, _PowerTelemetry, Driver):
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

    def _transition(self, state: str) -> None:
        started = time.perf_counter()
        try:
            self.logger.info("power %s", state)
            self._state = state
        except Exception:
            self._record_transition(state, "failure", started)
            raise
        self._record_transition(state, "success", started)

    @export
    def on(self) -> None:
        self._transition("on")

    @export
    def off(self) -> None:
        self._transition("off")

    @export
    def status(self) -> str:
        return self._state

    @export
    def read(self) -> Generator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)
