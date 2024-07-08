from abc import abstractmethod
from .. import DriverBase, DriverStub


class Serial(DriverBase, interface="serial"):
    @abstractmethod
    def read(self, size: int) -> bytes: ...

    @abstractmethod
    def write(self, data: bytes) -> int: ...

    @abstractmethod
    def flush(self): ...

    @property
    @abstractmethod
    def baudrate(self) -> int: ...

    @baudrate.setter
    @abstractmethod
    def baudrate(self, baudrate: int): ...
