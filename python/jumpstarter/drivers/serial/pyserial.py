from .base import Serial
from ..base import drivercall
from dataclasses import dataclass
import serial


@dataclass
class PySerial(Serial):
    device: serial.Serial

    def __init__(self, device: serial.Serial):
        super().__init__()

        self.device = device

    @drivercall
    def read(self, size: int) -> bytes:
        return self.device.read(size)

    @drivercall
    def write(self, data: bytes) -> int:
        return self.device.write(data)

    @drivercall
    def flush(self):
        self.device.flush()

    @property
    @drivercall
    def baudrate(self) -> int:
        return self.device.baudrate

    @baudrate.setter
    @drivercall
    def baudrate(self, baudrate: int):
        self.device.baudrate = baudrate
