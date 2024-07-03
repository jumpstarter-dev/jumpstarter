from abc import abstractmethod
from ..base import DriverBase, drivercall
import inspect


class Serial(DriverBase):
    def __init__(self):
        super().__init__()

        def build_getter(prop):
            return drivercall(lambda: prop.fget(self))

        def build_setter(prop):
            return drivercall(lambda x: prop.fset(self, x))

        properties = inspect.getmembers_static(self, lambda m: isinstance(m, property))
        for name, prop in properties:
            if prop.fget:
                setattr(self, "get_" + name, build_getter(prop))
            if prop.fset:
                setattr(self, "set_" + name, build_setter(prop))

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
