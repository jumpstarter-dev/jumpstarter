from abc import abstractmethod
from ..base import DriverBase, drivercall


class Serial(DriverBase):
    def __init__(self):
        super().__init__()

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
