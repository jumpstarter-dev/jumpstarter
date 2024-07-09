from .base import Serial
from dataclasses import dataclass
import serial


@dataclass
class PySerial(Serial):
    device: serial.Serial

    def __init__(self, *args, device: serial.Serial, **kwargs):
        super().__init__(*args, **kwargs)

        self.device = device

    def read(self, size: int) -> bytes:
        return self.device.read(int(size)).decode("utf-8")

    def write(self, data: bytes) -> int:
        return self.device.write(data.encode("utf-8"))

    def flush(self):
        self.device.flush()

    @property
    def baudrate(self) -> int:
        return self.device.baudrate

    @baudrate.setter
    def baudrate(self, baudrate: int):
        self.device.baudrate = baudrate
