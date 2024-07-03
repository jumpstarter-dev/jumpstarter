from .base import Serial
from dataclasses import dataclass
import serial


@dataclass
class PySerial(Serial):
    device: serial.Serial

    def __init__(self, device: serial.Serial):
        super().__init__()

        self.device = device

    def read(self, size: int) -> bytes:
        return self.device.read(size)

    def write(self, data: bytes) -> int:
        return self.device.write(data)

    def flush(self):
        self.device.flush()

    @property
    def baudrate(self) -> int:
        return self.device.baudrate

    @baudrate.setter
    def baudrate(self, baudrate: int):
        self.device.baudrate = baudrate
