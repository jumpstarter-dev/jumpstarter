import math
import plistlib
import re
import struct
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from xml.parsers.expat import ExpatError

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream, IncompleteRead, Lock, fail_after, to_thread
from anyio.streams.buffered import BufferedByteReceiveStream
from jumpstarter_driver_network.driver import NetworkInterface

from .interface import IosDeviceInterface
from .usbmux import UsbMuxDaemon
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export, exportstream

LOCKDOWN_PORT = 62078
MAX_LOCKDOWN_MESSAGE = 1024 * 1024


def _canonical_udid(value: str) -> str:
    # Linux sysfs omits the dash inserted by usbmuxd for newer Apple serials.
    return value.replace("-", "").lower()


def _serial_at_port(usb_port: str) -> str:
    path = Path("/sys/bus/usb/devices") / usb_port / "serial"
    try:
        serial = path.read_text().strip()
    except OSError as error:
        raise RuntimeError(f"no iOS device at USB port {usb_port}; check the cable and power on the device") from error
    if not serial:
        raise RuntimeError(f"USB port {usb_port} has no device serial")
    return serial


async def _get_value(stream, reader, key: str) -> str:
    payload = plistlib.dumps({"Label": "Jumpstarter", "Request": "GetValue", "Key": key})
    await stream.send(struct.pack(">I", len(payload)) + payload)
    length = struct.unpack(">I", await reader.receive_exactly(4))[0]
    if not 0 < length <= MAX_LOCKDOWN_MESSAGE:
        raise RuntimeError("invalid lockdown message length")
    try:
        response = plistlib.loads(await reader.receive_exactly(length))
    except (plistlib.InvalidFileException, ValueError, ExpatError, OverflowError, RecursionError) as error:
        raise RuntimeError("invalid lockdown response") from error
    if not isinstance(response, dict) or response.get("Request") != "GetValue":
        raise RuntimeError("unexpected lockdown response")
    if "Error" in response:
        raise RuntimeError(f"cannot read {key} from lockdown: {response['Error']}")
    value = response.get("Value")
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"lockdown did not return {key}")
    return value


@dataclass(kw_only=True)
class IosDevice(IosDeviceInterface, Driver):
    """One USB bench port, or one UDID on an operator-provided usbmux endpoint.

    ``usb_port`` is a Linux sysfs name such as ``1-4.2``. For a Mac exporter,
    select a device explicitly with ``udid``. Network transport connects to a
    usbmux daemon, not directly to an iPhone's IP address.
    """

    transport: Literal["usb", "network"] = "usb"
    usb_port: str | None = None
    udid: str | None = None
    usbmux_socket: str = "/var/run/usbmuxd"
    usbmux_host: str | None = None
    usbmux_port: int = 27015
    kind: Literal["physical", "corellium"] = "physical"
    trust_mode: Literal["exporter", "passthrough"] = "exporter"
    forward_ports: list[int] = field(default_factory=list)
    http_port: int | None = None
    connect_timeout: float = 10.0

    def __post_init__(self):
        super().__post_init__()
        self._validate_config()
        self._trust_broker = None
        self._https_broker = None
        self._trust_generation = None
        self._trust_lock = Lock()
        for port in self.forward_ports:
            name = f"port_{port}"
            if name in self.children:
                raise ConfigurationError(f"child {name} conflicts with forward_ports")
            self.children[name] = IosPortForward(device=self, port=port)

    def _validate_config(self):  # noqa: C901
        if self.transport not in ("usb", "network"):
            raise ConfigurationError("transport must be usb or network")
        if self.kind not in ("physical", "corellium"):
            raise ConfigurationError("kind must be physical or corellium")
        if self.trust_mode not in ("exporter", "passthrough"):
            raise ConfigurationError("trust_mode must be exporter or passthrough")
        if self.http_port is not None:
            if type(self.http_port) is not int or not 1 <= self.http_port <= 65535 or self.http_port == LOCKDOWN_PORT:
                raise ConfigurationError(
                    "http_port must be a device port between 1 and 65535 other than lockdown 62078"
                )
            if self.trust_mode != "exporter":
                raise ConfigurationError("http_port requires exporter-owned trust")
            if self.http_port in self.forward_ports:
                raise ConfigurationError("http_port must not also be exposed through forward_ports")
        if self.trust_mode == "exporter" and LOCKDOWN_PORT in self.forward_ports:
            raise ConfigurationError("use ios serve for authenticated lockdown; forwarding port 62078 bypasses trust")
        if (
            isinstance(self.connect_timeout, bool)
            or not isinstance(self.connect_timeout, (int, float))
            or not math.isfinite(self.connect_timeout)
            or self.connect_timeout <= 0
        ):
            raise ConfigurationError("connect_timeout must be positive and finite")
        if self.udid is not None and not re.fullmatch(r"[A-Za-z0-9-]+", self.udid):
            raise ConfigurationError("udid must be a nonempty device identifier")
        if self.transport == "usb":
            if (self.usb_port is None) == (self.udid is None):
                raise ConfigurationError("USB transport requires exactly one of usb_port or udid")
            if self.usb_port is not None and not re.fullmatch(r"\d+-\d+(?:\.\d+)*", self.usb_port):
                raise ConfigurationError("usb_port must be a Linux USB port name, for example 1-4.2")
            if self.usbmux_host is not None:
                raise ConfigurationError("usbmux_host requires network transport")
            if not self.usbmux_socket:
                raise ConfigurationError("usbmux_socket must not be empty")
        elif not self.udid or not self.usbmux_host or self.usb_port is not None:
            raise ConfigurationError("network transport requires udid and usbmux_host, without usb_port")
        for port in [self.usbmux_port, *self.forward_ports]:
            if type(port) is not int or not 1 <= port <= 65535:
                raise ConfigurationError("ports must be integers between 1 and 65535")
        if len(self.forward_ports) != len(set(self.forward_ports)):
            raise ConfigurationError("forward_ports must not contain duplicates")

    def _daemon(self) -> UsbMuxDaemon:
        if self.transport == "network":
            return UsbMuxDaemon(host=self.usbmux_host, port=self.usbmux_port, timeout=self.connect_timeout)
        return UsbMuxDaemon(path=self.usbmux_socket, timeout=self.connect_timeout, connection_type="USB")

    async def _resolve_device(self, daemon: UsbMuxDaemon) -> dict:
        selector = self.udid
        if self.usb_port is not None:
            selector = await to_thread.run_sync(_serial_at_port, self.usb_port)
        candidates = []
        for device in await daemon.list_devices():
            properties = device.get("Properties", {})
            if not isinstance(properties, dict) or type(device.get("DeviceID")) is not int:
                continue
            serial = properties.get("SerialNumber")
            if not isinstance(serial, str) or _canonical_udid(serial) != _canonical_udid(selector):
                continue
            if self.transport == "usb" and properties.get("ConnectionType") != "USB":
                continue
            candidates.append(device)
        if len(candidates) != 1:
            if candidates:
                raise RuntimeError("multiple usbmux devices match this selector; use a dedicated USB endpoint")
            raise RuntimeError("device is not present in usbmuxd; check its cable, power and exporter pairing")
        return candidates[0]

    async def _describe(self) -> dict:
        daemon = self._daemon()
        with fail_after(self.connect_timeout):
            device = await self._resolve_device(daemon)
            udid = device["Properties"]["SerialNumber"]
            async with daemon.connect_device(udid, LOCKDOWN_PORT, device_id=device["DeviceID"]) as stream:
                reader = BufferedByteReceiveStream(stream)
                os_version = await _get_value(stream, reader, "ProductVersion")
                model = await _get_value(stream, reader, "ProductType")
        return {"udid": udid, "model": model, "os_version": os_version}

    @export
    async def info(self) -> dict:
        result = {
            "present": False,
            "reason": None,
            "udid": self.udid,
            "model": None,
            "os_version": None,
            "kind": self.kind,
            "transport": self.transport,
            "trust_mode": self.trust_mode,
            "protocols": ["usbmux"],
            "forward_ports": list(self.forward_ports),
            "https_services": [] if self.http_port is None else ["https"],
        }
        if self.usb_port is not None:
            result["usb_port"] = self.usb_port
        try:
            result.update(await self._describe())
            match = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)*", result["os_version"])
            if match is None:
                result["reason"] = "cannot verify device iOS version"
            elif tuple(map(int, match.groups())) < (17, 4):
                result["reason"] = "iOS 17.4+ required; update the device"
            else:
                result["present"] = True
        except (
            OSError,
            RuntimeError,
            ValueError,
            EndOfStream,
            IncompleteRead,
            BrokenResourceError,
            ClosedResourceError,
            TimeoutError,
        ) as error:
            result["reason"] = f"cannot access iOS device: {str(error) or type(error).__name__}"
        return result

    async def _require_device(self) -> str:
        info = await self.info()
        if not info["present"]:
            raise RuntimeError(info["reason"])
        return info["udid"]

    async def _broker_for_device(self, udid):
        from .trust import ExporterTrustBroker

        async with self._trust_lock:
            daemon = self._daemon()
            try:
                with fail_after(self.connect_timeout):
                    device = await self._resolve_device(daemon)
                if device["Properties"]["SerialNumber"] != udid:
                    raise RuntimeError("device changed while opening its authenticated transport; retry")
            except BaseException:
                if self._trust_broker is not None:
                    self._trust_broker.invalidate()
                    self._trust_broker = None
                    self._trust_generation = None
                raise
            generation = (udid, device["DeviceID"])
            if self._trust_broker is not None and (
                self._trust_generation != generation or not self._trust_broker.is_active
            ):
                await self._trust_broker.aclose()
                self._trust_broker = None
                self._trust_generation = None
            if self._trust_broker is None:
                self._trust_broker = ExporterTrustBroker(
                    udid=udid,
                    device_id=device["DeviceID"],
                    allowed_ports=self.forward_ports,
                    https_ports=() if self.http_port is None else (self.http_port,),
                    path=daemon.path,
                    host=daemon.host,
                    port=daemon.port,
                    timeout=daemon.timeout,
                    connection_type=daemon.connection_type,
                )
                self._trust_generation = generation
            return self._trust_broker

    def _invalidate_trust(self):
        self._https_broker = None
        if self._trust_broker is not None:
            self._trust_broker.invalidate()
        self._trust_broker = None
        self._trust_generation = None
        self._trust_lock = Lock()

    def reset(self):
        self._invalidate_trust()
        super().reset()

    def close(self):
        self._invalidate_trust()
        super().close()

    @export
    async def https_info(self) -> dict:
        """Return public HTTPS trust for the configured HTTP service on this device."""
        if self.http_port is None:
            raise RuntimeError("HTTPS is not configured; set http_port on the exporter")
        info = await self.info()
        if not info["present"]:
            raise RuntimeError(info["reason"] or "the iOS target is not present")
        broker = await self._broker_for_device(info["udid"])
        certificate = await broker.https_certificate(self.http_port)
        if broker is not self._trust_broker or not broker.is_active:
            raise RuntimeError("HTTPS device generation changed; request connection information again")
        self._https_broker = broker
        return {
            "ca_certificate": certificate,
            "metadata": {
                "device": {"udid": info["udid"], "platform": "iOS", "version": info["os_version"]},
            },
        }

    @exportstream
    @asynccontextmanager
    async def connect_https(self):
        """TLS through the router to the exporter, then HTTP over device USB."""
        if self.http_port is None:
            raise RuntimeError("HTTPS is not configured; set http_port on the exporter")
        async with self._trust_lock:
            broker = self._https_broker
            if broker is None or broker is not self._trust_broker or not broker.is_active:
                raise RuntimeError("request fresh HTTPS connection information before opening its HTTPS stream")
            try:
                with fail_after(self.connect_timeout):
                    device = await self._resolve_device(self._daemon())
                generation = (device["Properties"]["SerialNumber"], device["DeviceID"])
                if generation != self._trust_generation:
                    raise RuntimeError("HTTPS device generation changed; request connection information again")
            except BaseException:
                broker.invalidate()
                self._https_broker = None
                raise
        async with broker.open_https_forward(self.http_port) as stream:
            yield stream

    @exportstream
    @asynccontextmanager
    async def connect_usbmux(self):
        # Resolve once per stream; an open tunnel never follows a swapped phone.
        udid = await self._require_device()
        daemon = self._daemon() if self.trust_mode == "passthrough" else await self._broker_for_device(udid)
        async with daemon.proxy(udid) as stream:
            yield stream


@dataclass(kw_only=True)
class IosPortForward(NetworkInterface, Driver):
    """A configured TCP port on the selected device, never the exporter host."""

    device: IosDevice = field(repr=False)
    port: int

    @exportstream
    @asynccontextmanager
    async def connect(self):
        udid = await self.device._require_device()
        if self.device.trust_mode == "exporter":
            broker = await self.device._broker_for_device(udid)
            async with broker.open_forward(self.port) as stream:
                yield stream
        else:
            async with self.device._daemon().connect_device(udid, self.port) as stream:
                yield stream
