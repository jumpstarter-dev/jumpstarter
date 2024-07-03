from .base import Serial
from ..base import drivercall
from dataclasses import dataclass
import serial


@dataclass
class PySerial(Serial):
    port: serial.Serial

    def __init__(self, port: str):
        super().__init__()

        self.port = serial.Serial(port)

    @drivercall
    def read(self, size: int) -> bytes:
        return self.port.read(size)

    @drivercall
    def write(self, data: bytes) -> int:
        return self.port.write(data)

    @drivercall
    def flush(self):
        self.port.flush()

    @property
    def baudrate(self) -> int:
        return self.port.baudrate

    @baudrate.setter
    def baudrate(self, baudrate: int):
        self.port.baudrate = baudrate
