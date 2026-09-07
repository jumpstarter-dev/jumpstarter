"""Serial port wrapper for NanoKVM-USB communication."""

from __future__ import annotations

import time

import serial
import serial.tools.list_ports

DEFAULT_BAUD_RATE = 57600
READ_TIMEOUT_S = 0.5


class SerialConnection:
    def __init__(self) -> None:
        self._port: serial.Serial | None = None

    @property
    def is_open(self) -> bool:
        return self._port is not None and self._port.is_open

    def open(self, port: str, baud_rate: int = DEFAULT_BAUD_RATE) -> None:
        if self._port and self._port.is_open:
            self.close()

        self._port = serial.Serial(
            port=port,
            baudrate=baud_rate,
            timeout=READ_TIMEOUT_S,
            write_timeout=READ_TIMEOUT_S,
        )

    def close(self) -> None:
        if self._port and self._port.is_open:
            self._port.close()
        self._port = None

    def write(self, data: bytes) -> None:
        if not self._port or not self._port.is_open:
            raise ConnectionError("Serial port not open")
        self._port.write(data)
        self._port.flush()

    def read(self, min_size: int, timeout: float = READ_TIMEOUT_S) -> list[int]:
        if not self._port or not self._port.is_open:
            raise ConnectionError("Serial port not open")

        result: list[int] = []
        deadline = time.monotonic() + timeout

        while len(result) < min_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            self._port.timeout = min(remaining, 0.1)
            chunk = self._port.read(min_size - len(result))
            if chunk:
                result.extend(chunk)

        return result

    @staticmethod
    def list_ports():
        return list(serial.tools.list_ports.comports())
