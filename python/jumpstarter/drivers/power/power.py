from abc import abstractmethod
from ..base import DriverBase

class PowerReading:
    def __init__(self, voltage: float, current: float):
        self.voltage = voltage
        self.current = current
        self.apparent_power = voltage * current

    def __repr__(self):
        return f'<PowerReading: {self.voltage}V {self.current}A {self.apparent_power}W>'

class Power(DriverBase):
    def __init__(self):
        pass

    @abstractmethod
    def on(self):
        pass

    @abstractmethod
    def off(self):
        pass

    def read() -> PowerReading:
        raise NotImplementedError


