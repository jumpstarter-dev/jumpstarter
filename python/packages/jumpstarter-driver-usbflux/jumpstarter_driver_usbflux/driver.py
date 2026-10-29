import math
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from anyio import fail_after

from .interface import UsbFluxInterface
from .usbmux import UsbMuxDaemon
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export, exportstream


def canonical_udid(value: str) -> str:
    # Linux sysfs omits the dash inserted by usbmuxd for newer Apple serials.
    return value.replace("-", "").lower()


@dataclass(kw_only=True)
class UsbFlux(UsbFluxInterface, Driver):
    """One device on a local or remote usbmux daemon, exposed per lease.

    Tools see only the selected device, may read its pairing record and the
    daemon's host identity, and cannot change pairing or other daemon state.
    The daemon's real pairing record is handed to tools; for exporter-owned
    pairing credentials use ``jumpstarter-driver-iosdevice`` instead.
    Network transport connects to a usbmux daemon, not to a device's own IP.
    """

    transport: Literal["usb", "network"] = "usb"
    udid: str
    usbmux_socket: str = "/var/run/usbmuxd"
    usbmux_host: str | None = None
    usbmux_port: int = 27015
    connect_timeout: float = 10.0

    def __post_init__(self):
        super().__post_init__()
        self._validate_config()

    def _validate_config(self):
        if self.transport not in ("usb", "network"):
            raise ConfigurationError("transport must be usb or network")
        if not isinstance(self.udid, str) or not re.fullmatch(r"[A-Za-z0-9-]+", self.udid):
            raise ConfigurationError("udid must be a nonempty device identifier")
        if self.transport == "usb":
            if self.usbmux_host is not None:
                raise ConfigurationError("usbmux_host requires network transport")
            if not isinstance(self.usbmux_socket, str) or not self.usbmux_socket:
                raise ConfigurationError("usbmux_socket must not be empty")
        elif not isinstance(self.usbmux_host, str) or not self.usbmux_host:
            raise ConfigurationError("network transport requires usbmux_host")
        if type(self.usbmux_port) is not int or not 1 <= self.usbmux_port <= 65535:
            raise ConfigurationError("usbmux_port must be an integer between 1 and 65535")
        if (
            isinstance(self.connect_timeout, bool)
            or not isinstance(self.connect_timeout, (int, float))
            or not math.isfinite(self.connect_timeout)
            or self.connect_timeout <= 0
        ):
            raise ConfigurationError("connect_timeout must be positive and finite")

    def _daemon(self) -> UsbMuxDaemon:
        if self.transport == "network":
            return UsbMuxDaemon(host=self.usbmux_host, port=self.usbmux_port, timeout=self.connect_timeout)
        return UsbMuxDaemon(path=self.usbmux_socket, timeout=self.connect_timeout, connection_type="USB")

    async def _resolve_device(self, daemon: UsbMuxDaemon) -> dict:
        candidates = []
        for device in await daemon.list_devices():
            properties = device.get("Properties", {})
            if not isinstance(properties, dict) or type(device.get("DeviceID")) is not int:
                continue
            serial = properties.get("SerialNumber")
            if not isinstance(serial, str) or canonical_udid(serial) != canonical_udid(self.udid):
                continue
            if self.transport == "usb" and properties.get("ConnectionType") != "USB":
                continue
            candidates.append(device)
        if len(candidates) != 1:
            if candidates:
                raise RuntimeError("multiple usbmux devices match this udid; use a dedicated USB endpoint")
            raise RuntimeError("device is not present in usbmuxd; check its cable, power and pairing")
        return candidates[0]

    @export
    async def info(self) -> dict:
        result = {
            "present": False,
            "reason": None,
            "udid": self.udid,
            "device_id": None,
            "connection_type": None,
            "transport": self.transport,
            "protocols": ["usbmux"],
        }
        try:
            with fail_after(self.connect_timeout):
                device = await self._resolve_device(self._daemon())
        except (OSError, RuntimeError, TimeoutError) as error:
            result["reason"] = str(error) or type(error).__name__
            return result
        result.update(
            present=True,
            udid=device["Properties"]["SerialNumber"],
            device_id=device["DeviceID"],
            connection_type=device["Properties"].get("ConnectionType"),
        )
        return result

    @exportstream
    @asynccontextmanager
    async def connect_usbmux(self):
        # Resolve once per stream; an open proxy never follows a swapped device.
        daemon = self._daemon()
        with fail_after(self.connect_timeout):
            device = await self._resolve_device(daemon)
        async with daemon.proxy(device["Properties"]["SerialNumber"]) as stream:
            yield stream
