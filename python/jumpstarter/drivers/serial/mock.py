from .base import Serial
from ..base import drivercall
from dataclasses import dataclass
import os


@dataclass
class MockSerial(Serial):
    r: int
    w: int
    _baudrate: int

    def __init__(self):
        super().__init__()

        (self.r, self.w) = os.pipe()
        self._baudrate = 115200

    @drivercall
    def read(self, size: int) -> bytes:
        return os.read(self.r, size)

    @drivercall
    def write(self, data: bytes) -> int:
        return os.write(self.w, data)

    @drivercall
    def flush(self):
        pass

    @property
    @drivercall
    def baudrate(self) -> int:
        return self._baudrate

    @baudrate.setter
    @drivercall
    def baudrate(self, baudrate: int):
        self._baudrate = baudrate
