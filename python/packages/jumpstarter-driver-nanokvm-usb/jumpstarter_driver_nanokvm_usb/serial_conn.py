"""Serial port wrapper for NanoKVM-USB communication."""

from __future__ import annotations

import time

import serial
import serial.tools.list_ports

from .protocol import HEAD1, CmdPacket, find_header

DEFAULT_BAUD_RATE = 57600
READ_TIMEOUT_S = 0.5
_MAX_PACKET_DATA = 64


def _pop_frame(buf: bytearray) -> CmdPacket | None:
    """Decode a packet that already starts at ``buf[0]``.

    Returns None if more bytes are required. Drops the first byte when the frame
    is invalid so the caller can resync.
    """
    if len(buf) < 5:
        return None
    data_len = buf[4]
    if data_len > _MAX_PACKET_DATA:
        del buf[0]
        return None
    need = 5 + data_len + 1
    if len(buf) < need:
        return None
    try:
        packet = CmdPacket.decode(bytes(buf[:need]))
    except ValueError:
        del buf[0]
        return None
    del buf[:need]
    return packet


def _take_packet(buf: bytearray) -> CmdPacket | None:
    """Pop one framed packet from ``buf``, or return None if more bytes are needed."""
    while True:
        start = find_header(buf)
        if start < 0:
            if buf and buf[-1] == HEAD1:
                del buf[:-1]
            else:
                buf.clear()
            return None
        if start:
            del buf[:start]
        before = len(buf)
        packet = _pop_frame(buf)
        if packet is not None or len(buf) == before:
            return packet


class SerialConnection:
    def __init__(self) -> None:
        self._port: serial.Serial | None = None
        self._rx_buf = bytearray()

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
        self._rx_buf.clear()

    def write(self, data: bytes) -> None:
        if not self._port or not self._port.is_open:
            raise ConnectionError("Serial port not open")
        self._port.write(data)
        self._port.flush()

    def reset_input_buffer(self) -> None:
        if not self._port or not self._port.is_open:
            raise ConnectionError("Serial port not open")
        self._rx_buf.clear()
        self._port.reset_input_buffer()

    def read_packet(self, timeout: float) -> CmdPacket | None:
        """Read one ``57 AB`` frame, or return None when ``timeout`` elapses."""
        if not self._port or not self._port.is_open:
            raise ConnectionError("Serial port not open")

        deadline = time.monotonic() + timeout
        while True:
            packet = _take_packet(self._rx_buf)
            if packet is not None:
                return packet
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._port.timeout = remaining
            chunk = self._port.read(5 + _MAX_PACKET_DATA + 1)
            if chunk:
                self._rx_buf.extend(chunk)

    @staticmethod
    def list_ports():
        return list(serial.tools.list_ports.comports())
