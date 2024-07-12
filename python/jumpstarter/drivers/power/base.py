from abc import abstractmethod
from collections.abc import Generator
from dataclasses import dataclass
from .. import DriverBase


@dataclass
class PowerReading:
    voltage: float
    current: float
    apparent_power: float

    def __init__(self, voltage: float, current: float):
        self.voltage = voltage
        self.current = current
        self.apparent_power = voltage * current

    def __repr__(self):
        return f"<PowerReading: {self.voltage}V {self.current}A {self.apparent_power}W>"


class Power(DriverBase, interface="power"):
    @abstractmethod
    async def on(self) -> str: ...

    @abstractmethod
    async def off(self) -> str: ...

    @abstractmethod
    async def read(self) -> Generator[PowerReading, None, None]:
        yield None
