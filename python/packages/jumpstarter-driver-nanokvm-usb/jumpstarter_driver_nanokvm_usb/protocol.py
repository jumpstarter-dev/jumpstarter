"""NanoKVM-USB serial protocol: packet framing and device info parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

HEAD1 = 0x57
HEAD2 = 0xAB


class CmdEvent(IntEnum):
    GET_INFO = 0x01
    SEND_KB_GENERAL_DATA = 0x02
    SEND_KB_MEDIA_DATA = 0x03
    SEND_MS_ABS_DATA = 0x04
    SEND_MS_REL_DATA = 0x05
    SEND_MY_HID_DATA = 0x06
    READ_MY_HID_DATA = 0x87
    GET_PARA_CFG = 0x08
    SET_PARA_CFG = 0x09
    GET_USB_STRING = 0x0A
    SET_USB_STRING = 0x0B
    SET_DEFAULT_CFG = 0x0C
    RESET = 0x0F


@dataclass
class CmdPacket:
    addr: int = 0x00
    cmd: int = 0x00
    data: list[int] = field(default_factory=list)

    def encode(self) -> bytes:
        length = len(self.data)
        raw = [HEAD1, HEAD2, self.addr, self.cmd, length, *self.data]
        checksum = sum(raw) & 0xFF
        raw.append(checksum)
        return bytes(raw)

    @classmethod
    def decode(cls, buf: bytes | list[int]) -> CmdPacket:
        data = list(buf)
        header_idx = _find_header(data)
        if header_idx < 0:
            raise ValueError("Cannot find packet header [0x57, 0xAB]")

        remaining = len(data) - header_idx
        if remaining < 6:
            raise ValueError(f"Packet too short: {remaining} bytes after header")

        addr = data[header_idx + 2]
        cmd = data[header_idx + 3]
        data_len = data[header_idx + 4]

        if remaining < 5 + data_len + 1:
            raise ValueError(f"Packet truncated: need {5 + data_len + 1}, have {remaining}")

        checksum = data[header_idx + 5 + data_len]

        expected = 0
        for i in range(header_idx, header_idx + 5 + data_len):
            expected += data[i]
        expected &= 0xFF

        if expected != checksum:
            raise ValueError(
                f"Checksum mismatch: expected 0x{expected:02X}, got 0x{checksum:02X}"
            )

        payload = data[header_idx + 5 : header_idx + 5 + data_len]
        return cls(addr=addr, cmd=cmd, data=payload)


def _find_header(data: list[int]) -> int:
    for i in range(len(data) - 1):
        if data[i] == HEAD1 and data[i + 1] == HEAD2:
            return i
    return -1


@dataclass
class InfoPacket:
    chip_version: str
    is_connected: bool
    num_lock: bool
    caps_lock: bool
    scroll_lock: bool

    @classmethod
    def from_data(cls, data: list[int]) -> InfoPacket:
        if not data or data[0] < 0x30:
            raise ValueError(f"Invalid version byte: {data[0] if data else 'empty'}")

        version_e = data[0] - 0x30
        version = 1.0 + version_e / 10
        chip_version = f"V{version:.1f}"

        is_connected = data[1] != 0 if len(data) > 1 else False

        lock_byte = data[2] if len(data) > 2 else 0
        num_lock = bool(lock_byte & 1)
        caps_lock = bool(lock_byte & 2)
        scroll_lock = bool(lock_byte & 4)

        return cls(
            chip_version=chip_version,
            is_connected=is_connected,
            num_lock=num_lock,
            caps_lock=caps_lock,
            scroll_lock=scroll_lock,
        )
