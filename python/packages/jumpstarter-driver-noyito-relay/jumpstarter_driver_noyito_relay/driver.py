import os
import sys
from collections.abc import Generator
from dataclasses import dataclass

import serial
from jumpstarter_driver_power.driver import PowerInterface, PowerReading

from jumpstarter.driver import Driver, export

# Protocol constants
_CMD_STATUS = bytes([0xFF])
_BAUD_RATE = 9600
_SERIAL_TIMEOUT = 2


def _build_command(channel: int, state: int) -> bytes:
    """Build 4-byte relay command. Checksum = (0xA0 + channel + state) & 0xFF."""
    checksum = (0xA0 + channel + state) & 0xFF
    return bytes([0xA0, channel, state, checksum])


@dataclass(kw_only=True)
class NoyitoPowerSerial(PowerInterface, Driver):
    """Driver for the NOYITO 5V 2-Channel USB Relay Module.

    Controls one relay channel on the NOYITO USB relay board via the CH340
    USB-to-serial chip at 9600 baud with a 4-byte binary protocol.

    Set ``dual=True`` to switch both channels simultaneously for high-current
    applications where the two relay contacts are wired in parallel.
    """

    port: str
    channel: int = 1
    dual: bool = False

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_noyito_relay.client.NoyitoPowerClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if not self.dual and self.channel not in (1, 2):
            raise ValueError(f"channel must be 1 or 2, got {self.channel!r}")

    def _send_command(self, cmd: bytes) -> None:
        with serial.Serial(self.port, baudrate=_BAUD_RATE, timeout=_SERIAL_TIMEOUT) as ser:
            ser.write(cmd)

    def _query_status(self) -> dict[str, str]:
        with serial.Serial(self.port, baudrate=_BAUD_RATE, timeout=_SERIAL_TIMEOUT) as ser:
            ser.write(_CMD_STATUS)
            raw = ser.read(32)
        text = raw.decode("ascii", errors="replace").strip()
        result: dict[str, str] = {}
        for part in text.replace("\r", "").split("\n"):
            part = part.strip()
            if ":" in part:
                key, _, val = part.partition(":")
                result[key.strip()] = val.strip()
        if not result:
            raise ValueError(f"Unexpected status response: {raw!r}")
        return result

    def _channels(self) -> list[int]:
        return [1, 2] if self.dual else [self.channel]

    @export
    def on(self) -> None:
        for ch in self._channels():
            self.logger.info("Relay channel %d ON", ch)
            self._send_command(_build_command(ch, 1))

    @export
    def off(self) -> None:
        for ch in self._channels():
            self.logger.info("Relay channel %d OFF", ch)
            self._send_command(_build_command(ch, 0))

    @export
    def read(self) -> Generator[PowerReading, None, None]:
        yield PowerReading(voltage=0.0, current=0.0)

    @export
    def status(self) -> str:
        all_channels = self._query_status()
        states = set()
        for ch in self._channels():
            key = f"CH{ch}"
            if key not in all_channels:
                raise ValueError(f"Channel {key} not found in status response: {all_channels!r}")
            states.add(all_channels[key].lower())
        if len(states) == 1:
            return states.pop()
        return "partial"


@dataclass(kw_only=True)
class NoyitoPowerHID(PowerInterface, Driver):
    """Driver for the NOYITO 4/8-Channel HID Drive-free USB Relay Module.

    Uses USB HID (hid library) instead of serial. Status query is not
    supported by this hardware series.

    vendor_id / product_id default to the NOYITO HID module values (5131 / 2007).
    Set num_channels to 4 or 8 to match the physical board.
    Set all_channels=True to fire every channel simultaneously for high-current use.
    """

    vendor_id: int = 5131
    product_id: int = 2007
    num_channels: int = 4
    channel: int = 1
    all_channels: bool = False

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_noyito_relay.client.NoyitoPowerClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if self.num_channels not in (4, 8):
            raise ValueError(f"num_channels must be 4 or 8, got {self.num_channels!r}")
        if not self.all_channels and self.channel not in range(1, self.num_channels + 1):
            raise ValueError(
                f"channel must be 1..{self.num_channels}, got {self.channel!r}"
            )

    def _channels(self) -> list[int]:
        return list(range(1, self.num_channels + 1)) if self.all_channels else [self.channel]

    def _send_command(self, cmd: bytes) -> None:
        # On Apple Silicon Macs, Homebrew installs hidapi to /opt/homebrew/lib
        # which is not in ctypes's default search path.  Extend
        # DYLD_FALLBACK_LIBRARY_PATH before the first import so dlopen finds it.
        if sys.platform == "darwin":
            _brew_lib = os.path.join(os.environ.get("HOMEBREW_PREFIX", "/opt/homebrew"), "lib")
            if os.path.isdir(_brew_lib):
                _fallback = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
                if _brew_lib not in _fallback.split(":"):
                    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = _brew_lib + (":" + _fallback if _fallback else "")
        import hid  # noqa: PLC0415
        with hid.Device(self.vendor_id, self.product_id) as device:
            device.write(b"\x00" + cmd)  # 0x00 = HID report ID

    @export
    def on(self) -> None:
        for ch in self._channels():
            self.logger.info("HID Relay channel %d ON", ch)
            self._send_command(_build_command(ch, 1))

    @export
    def off(self) -> None:
        for ch in self._channels():
            self.logger.info("HID Relay channel %d OFF", ch)
            self._send_command(_build_command(ch, 0))

    @export
    def read(self) -> Generator[PowerReading, None, None]:
        yield PowerReading(voltage=0.0, current=0.0)
