from abc import abstractmethod
from ..base import DriverBase, drivercall


class Serial(DriverBase):
    @property
    def interface(self):
        return "serial"

    @abstractmethod
    def read(self, size: int) -> bytes: ...

    @abstractmethod
    def write(self, data: bytes) -> int: ...

    @abstractmethod
    def flush(self): ...

    @property
    @abstractmethod
    def baudrate(self) -> int: ...

    @drivercall
    def get_baudrate(self) -> int:
        return self.baudrate

    @drivercall
    def set_baudrate(self, baudrate: int):
        self.baudrate = baudrate
