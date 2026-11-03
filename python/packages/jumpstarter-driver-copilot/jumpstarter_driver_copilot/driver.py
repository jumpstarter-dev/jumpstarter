from __future__ import annotations

import errno
import math
import os
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, cast

from jumpstarter_driver_gpiod.client import PinState
from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.driver import PowerInterface

from jumpstarter.driver import Driver, export

if TYPE_CHECKING:
    import gpiod
else:
    try:
        import gpiod
    except ImportError:
        gpiod = None

# Errors meaning the CoPilot USB device is gone or its GPIO request is stale.
# Any other OSError is reported without disposing of a still-valid request.
_USB_LOSS_ERRNOS = frozenset({errno.ENOENT, errno.ENODEV, errno.ENXIO, errno.EIO, errno.EBADF, errno.ESTALE})


@dataclass(kw_only=True)
class CoPilotGPIO(Driver):
    """Generic GPIO and HAT control for a BayLibre CoPilot.

    The unit is selected by USB serial number. HATs are explicitly described
    because the relay board uses jumpers and therefore cannot be discovered
    reliably in software.
    """

    driver_type = "gpio"

    serial: str | None = None
    device: str | None = None
    managed_lines: list[int] = field(default_factory=lambda: [0, 1, 2, 3])
    hats: list[dict] = field(default_factory=list)
    active_low: bool = False
    initial_active: bool = False
    _chip: gpiod.Chip | None = field(default=None, init=False, repr=False)
    _request: gpiod.LineRequest | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _device_override: str | None = field(default=None, init=False, repr=False)
    _identity: tuple | None = field(default=None, init=False, repr=False)
    _asserted: set[int] = field(default_factory=set, init=False, repr=False)
    _interrupted: set[int] = field(default_factory=set, init=False, repr=False)
    _connections: int = field(default=0, init=False, repr=False)
    _last_error: str | None = field(default=None, init=False, repr=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_copilot.client.CoPilotClient"

    def __post_init__(self):
        super().__post_init__()
        if gpiod is None:
            raise ImportError("CoPilot requires Linux with the gpiod Python bindings (version 2.2 or newer)")
        if self.serial is not None and (not self.serial or self.serial in {".", ".."} or "/" in self.serial):
            raise ValueError("serial must be a non-empty USB serial, not a path")
        if self.serial is not None and self.device is not None:
            raise ValueError("configure either serial or device, not both")
        if not isinstance(self.active_low, bool) or not isinstance(self.initial_active, bool):
            raise TypeError("active_low and initial_active must be booleans")
        self._device_override = self.device
        if not self.managed_lines:
            raise ValueError("managed_lines must not be empty")
        if any(isinstance(line, bool) or not isinstance(line, int) or line < 0 for line in self.managed_lines):
            raise ValueError("managed_lines must contain non-negative integers")
        if len(set(self.managed_lines)) != len(self.managed_lines):
            raise ValueError("managed_lines contains duplicates")
        self._validate_hats()
        self._open(initial=self.initial_active)

    @staticmethod
    def _path_identity(path):
        node = os.stat(path)
        return (node.st_dev, node.st_ino, node.st_rdev)

    def _open(self, *, initial=False):
        # Re-resolve the selected serial, never auto-select a different CoPilot
        # after a disconnect. A path override remains the caller's responsibility.
        self.device = self._resolve_device()
        identity = self._path_identity(self.device)
        chip = gpiod.Chip(self.device)
        request = None
        try:
            settings = gpiod.LineSettings(
                direction=gpiod.line.Direction.OUTPUT,
                drive=gpiod.line.Drive.PUSH_PULL,
                active_low=self.active_low,
                output_value=(gpiod.line.Value.ACTIVE if initial else gpiod.line.Value.INACTIVE),
            )
            request = chip.request_lines(config={tuple(self.managed_lines): settings}, consumer="jumpstarter-copilot")
            if self._path_identity(self.device) != identity:
                raise OSError(errno.ENODEV, "CoPilot changed during GPIO acquisition")
        except BaseException:
            try:
                if request is not None:
                    request.release()
            finally:
                chip.close()
            raise
        self._chip, self._request, self._identity = chip, request, identity
        self._asserted = set(self.managed_lines) if initial else set()
        self._connections += 1

    def _dispose(self, *, suppress=False):
        request, chip = self._request, self._chip
        self._request = self._chip = self._identity = None
        self._asserted.clear()
        try:
            if request is not None:
                for line in self.managed_lines:
                    try:
                        request.set_value(line, gpiod.line.Value.INACTIVE)
                    except OSError:
                        pass  # USB disappearance can invalidate the old request.
                request.release()
        except OSError:
            if not suppress:
                raise
        finally:
            if chip is not None:
                try:
                    chip.close()
                except OSError:
                    if not suppress:
                        raise

    def _lost(self, error):
        self._last_error = str(error)
        self._interrupted.update(self._asserted)
        self._dispose(suppress=True)

    def _ensure_connected(self):
        if self._closed:
            raise RuntimeError("CoPilot is closed")
        if self._request is not None:
            try:
                if self._path_identity(self.device) != self._identity:
                    raise OSError(errno.ENODEV, "CoPilot USB device was replaced")
                # Also detects an invalid request when Linux reused the device
                # node. Reads are safe to retry; writes are NEVER retried.
                self._request.get_values(self.managed_lines)
            except OSError as error:
                if error.errno not in _USB_LOSS_ERRNOS:
                    raise
                self._lost(error)
        if self._request is None:
            try:
                self._open(initial=False)
            except (OSError, ValueError) as error:
                self._last_error = str(error)
                raise

    def _connected_request(self) -> gpiod.LineRequest:
        if self._request is None:
            raise RuntimeError("CoPilot GPIO request is not open")
        return self._request

    @export
    def reconnect(self) -> dict:
        """Explicitly release all outputs, reconnect and acknowledge interruption."""
        with self._lock:
            if self._closed:
                raise RuntimeError("CoPilot is closed")
            self._interrupted.update(self._asserted)
            self._dispose(suppress=True)
            self._open(initial=False)
            self._interrupted.clear()
            self._last_error = None
            return self.info()

    def _resolve_device(self) -> str:
        if self._device_override:
            return self._device_override
        if self.serial:
            candidate = Path("/dev/copilot/by-id") / self.serial / "gpiochip"
            if not candidate.exists():
                raise FileNotFoundError(f"CoPilot serial {self.serial!r} not found at {candidate}")
            return str(candidate)
        candidates = sorted(Path("/dev/copilot/by-id").glob("*/gpiochip"))
        if len(candidates) == 1:
            self.serial = candidates[0].parent.name
            return str(candidates[0])
        if not candidates:
            raise FileNotFoundError("no CoPilot found under /dev/copilot/by-id")
        serials = [candidate.parent.name for candidate in candidates]
        raise ValueError(f"multiple CoPilots found; configure serial explicitly: {serials}")

    def _validate_hats(self) -> None:
        if sum(isinstance(hat, dict) and hat.get("type") == "triple-relay" for hat in self.hats) > 1:
            raise ValueError("at most one triple-relay HAT is supported")
        supported = {"triple-relay", "usb-extension", "custom"}
        for hat in self.hats:
            if not isinstance(hat, dict):
                raise TypeError("each HAT must be a mapping")
            hat_type = hat.get("type")
            if hat_type not in supported:
                raise ValueError(f"unsupported CoPilot HAT {hat_type!r}; supported: {sorted(supported)}")
            if hat_type == "triple-relay":
                self._validate_relay_hat(hat)
            if hat_type == "usb-extension":
                self._validate_usb_hat(hat)
        usb_hats = [hat for hat in self.hats if hat.get("type") == "usb-extension" and "usb_lines" in hat]
        if len(usb_hats) > 1:
            raise ValueError("at most one usb-extension HAT with usb_lines is supported")
        relay_hats = [hat for hat in self.hats if hat.get("type") == "triple-relay"]
        if usb_hats and relay_hats:
            shared = set(usb_hats[0]["usb_lines"]) & set(relay_hats[0].get("relay_lines", self.managed_lines))
            if shared:
                raise ValueError(f"usb_lines and relay_lines share GPIO lines {sorted(shared)}")

    def _validate_usb_hat(self, hat: dict) -> None:
        ports = hat.get("ports")
        if ports is not None and (isinstance(ports, bool) or not isinstance(ports, int) or ports < 1):
            raise ValueError("usb-extension ports must be a positive integer")
        if "usb_lines" not in hat:
            return
        usb_lines = hat["usb_lines"]
        if not isinstance(usb_lines, list) or not usb_lines:
            raise ValueError("usb_lines must be a non-empty list")
        if any(isinstance(line, bool) or not isinstance(line, int) for line in usb_lines):
            raise ValueError("usb_lines must contain integers")
        if len(set(usb_lines)) != len(usb_lines):
            raise ValueError("usb_lines must be distinct")
        if not set(usb_lines).issubset(self.managed_lines):
            raise ValueError("usb-extension usb_lines must be included in managed_lines")
        if ports is not None and ports != len(usb_lines):
            raise ValueError("usb-extension ports must match the number of usb_lines")

    def _validate_relay_hat(self, hat: dict) -> None:
        relay_lines = hat.get("relay_lines", self.managed_lines)
        if not isinstance(relay_lines, list) or len(relay_lines) != 3:
            raise ValueError("triple-relay HAT requires exactly three relay_lines")
        if any(isinstance(line, bool) or not isinstance(line, int) for line in relay_lines):
            raise ValueError("relay_lines must contain integers")
        if len(set(relay_lines)) != 3:
            raise ValueError("relay_lines must be distinct")
        if not set(relay_lines).issubset(self.managed_lines):
            raise ValueError("triple-relay relay_lines must be included in managed_lines")

    @staticmethod
    def _integer(value, name):
        # Jumpstarter's protobuf Struct transport represents RPC numbers as
        # doubles. Accept integral floats, never silently truncate a GPIO index.
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or int(value) != value
        ):
            raise ValueError(f"{name} must be an integer")
        return int(value)

    def _validate_line(self, line: int):
        line = self._integer(line, "line")
        if line not in self.managed_lines:
            raise ValueError(f"GPIO line {line} is not managed; allowed: {self.managed_lines}")
        return line

    def _relay_line(self, channel: int) -> int:
        channel = self._integer(channel, "channel")
        relay_hats = [hat for hat in self.hats if hat.get("type") == "triple-relay"]
        if not relay_hats:
            raise ValueError("no triple-relay HAT is configured")
        relay_lines = relay_hats[0].get("relay_lines", self.managed_lines)
        if channel < 1 or channel > len(relay_lines):
            raise ValueError(f"relay channel must be between 1 and {len(relay_lines)}")
        return int(relay_lines[channel - 1])

    def _usb_lines(self) -> list[int]:
        for hat in self.hats:
            if hat.get("type") == "usb-extension" and "usb_lines" in hat:
                return [int(line) for line in hat["usb_lines"]]
        raise ValueError("no usb-extension HAT with usb_lines is configured")

    def _usb_line(self, port: int) -> int:
        port = self._integer(port, "port")
        usb_lines = self._usb_lines()
        if port < 1 or port > len(usb_lines):
            raise ValueError(f"USB port must be between 1 and {len(usb_lines)}")
        return usb_lines[port - 1]

    @staticmethod
    def _duration(value, name):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 1.0 <= value <= 30
        ):
            raise ValueError(f"{name} must be between 1 and 30 seconds")
        return float(value)

    def extra_labels(self) -> dict[str, str]:
        return {
            "baylibre.com/device": "copilot",
            "baylibre.com/serial": self.serial or "path-override",
        }

    @export
    def info(self) -> dict:
        with self._lock:
            try:
                self._ensure_connected()
            except (OSError, RuntimeError, ValueError) as error:
                self._last_error = str(error)
            return self._info()

    def _info(self):
        capabilities = ["gpio"]
        if 0 in self.managed_lines:
            capabilities.append("power-switch-control")
        for hat in self.hats:
            if hat["type"] == "triple-relay":
                capabilities.append("relay")
            elif hat["type"] == "usb-extension":
                capabilities.append("usb-expansion")
                if "usb_lines" in hat:
                    capabilities.append("usb-port-control")
        return {
            "serial": self.serial,
            "device": self.device,
            "managed_lines": self.managed_lines,
            "hats": self.hats,
            "capabilities": capabilities,
            "connected": self._request is not None and not self._closed,
            "connection_generation": self._connections,
            "interrupted_lines": sorted(self._interrupted),
            "last_error": self._last_error,
        }

    @export
    def set_line(self, line: int, active: bool) -> None:
        with self._lock:
            if not isinstance(active, bool):
                raise TypeError("active must be a boolean")
            line = self._validate_line(line)
            if self._closed and not active:
                return  # Shared reference graph cleanup is idempotent.
            self._ensure_connected()
            if active and self._interrupted:
                raise RuntimeError(
                    "CoPilot disconnected during an action; outputs are inactive. "
                    "Release interrupted lines or use 'j copilot reconnect' before retrying."
                )
            value = gpiod.line.Value.ACTIVE if active else gpiod.line.Value.INACTIVE
            if active:
                self._asserted.add(line)  # A failed write may already have reached hardware.
            try:
                self._connected_request().set_value(line, value)
            except OSError as error:
                if error.errno in _USB_LOSS_ERRNOS:
                    self._lost(error)
                raise  # Never retry a write that may have succeeded before USB loss.
            if not active:
                self._asserted.discard(line)
                if line in self._interrupted:
                    self._interrupted.remove(line)
                    raise RuntimeError("CoPilot disconnected during the action; output released, action interrupted")
            self.logger.info("CoPilot %s line %d -> %s", self.device, line, "active" if active else "inactive")

    @export
    def read_line(self, line: int) -> bool:
        with self._lock:
            line = self._validate_line(line)
            self._ensure_connected()
            try:
                return self._connected_request().get_value(line) == gpiod.line.Value.ACTIVE
            except OSError as error:
                if error.errno in _USB_LOSS_ERRNOS:
                    self._lost(error)
                raise

    @export
    def pulse(self, line: int, duration: float = 1.0) -> None:
        duration = self._duration(duration, "duration")
        with self._lock:
            try:
                self.set_line(line, True)
                time.sleep(duration)
            finally:
                self.set_line(line, False)

    @export
    def set_relay(self, channel: int, active: bool) -> None:
        self.set_line(self._relay_line(channel), active)

    @export
    def read_relay(self, channel: int) -> bool:
        return self.read_line(self._relay_line(channel))

    @export
    def pulse_relay(self, channel: int, duration: float = 1.0) -> None:
        self.pulse(self._relay_line(channel), duration)

    @export
    def usb_ports(self) -> int:
        return len(self._usb_lines())

    @export
    def set_usb(self, port: int, active: bool) -> None:
        self.set_line(self._usb_line(port), active)

    @export
    def read_usb(self, port: int) -> bool:
        return self.read_line(self._usb_line(port))

    @export
    def cycle_usb(self, port: int, wait: float = 2.0) -> None:
        """Switch a USB port off, wait, then on again; the port ends enabled."""
        line = self._usb_line(port)
        wait = self._duration(wait, "wait")
        with self._lock:
            self.set_line(line, False)
            time.sleep(wait)
            self.set_line(line, True)

    def close(self):
        # YAML references share this single request; the export tree can close
        # the same controller through several proxies.
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._dispose()
            finally:
                super().close()


@dataclass(kw_only=True)
class CoPilotOutput(Driver):
    """Expose one already-owned CoPilot line as a standard digital output.

    ``children.controller`` references a CoPilotGPIO instance. The controller
    remains the sole owner of the GPIO request; this adapter never opens GPIO.
    An active output means an asserted relay, not that the DUT is powered on.
    """

    line: int

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_gpiod.client.DigitalOutputClient"

    def __post_init__(self):
        super().__post_init__()
        if "controller" not in self.children:
            raise ValueError("CoPilotOutput requires children.controller")
        if isinstance(self.line, bool) or not isinstance(self.line, int) or self.line < 0:
            raise ValueError("line must be a non-negative integer")

    @export
    def on(self) -> None:
        cast(CoPilotGPIO, self.children["controller"]).set_line(self.line, True)

    @export
    def off(self) -> None:
        cast(CoPilotGPIO, self.children["controller"]).set_line(self.line, False)

    @export
    def read_pin(self):
        return (
            PinState.ACTIVE
            if cast(CoPilotGPIO, self.children["controller"]).read_line(self.line)
            else PinState.INACTIVE
        )


@dataclass(kw_only=True)
class CoPilotUSB(Driver):
    """Per-port control of the triple-USB HAT channels.

    ``children.controller`` references a CoPilotGPIO whose ``usb-extension``
    HAT declares ``usb_lines``. Port N drives ``usb_lines[N - 1]``. An active
    line enables the channel's power; this adapter never opens GPIO itself.
    """

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_copilot.client.CoPilotUSBClient"

    def __post_init__(self):
        super().__post_init__()
        if "controller" not in self.children:
            raise ValueError("CoPilotUSB requires children.controller")

    def _controller(self) -> CoPilotGPIO:
        return cast(CoPilotGPIO, self.children["controller"])

    @export
    def ports(self) -> int:
        return self._controller().usb_ports()

    @export
    def on(self, port: int) -> None:
        self._controller().set_usb(port, True)

    @export
    def off(self, port: int) -> None:
        self._controller().set_usb(port, False)

    @export
    def read(self, port: int) -> bool:
        return self._controller().read_usb(port)

    @export
    def cycle(self, port: int, wait: float = 2.0) -> None:
        self._controller().cycle_usb(port, wait)


@dataclass(kw_only=True)
class CoPilotPower(PowerInterface, Driver):
    """Native GPIO0 supply switch, independent of HATs and DUT button wiring.

    J1 must select GPIO-controlled passthrough, not always-on bypass. GPIO
    readback reports the commanded switch, never measured DUT voltage/current.
    """

    @classmethod
    def client(cls):
        return "jumpstarter_driver_copilot.client.CoPilotPowerClient"

    def __post_init__(self):
        super().__post_init__()
        if "controller" not in self.children:
            raise ValueError("CoPilotPower requires children.controller")

    def _controller(self):
        controller = cast(CoPilotGPIO, self.children["controller"])
        if controller.active_low:
            raise ValueError("CoPilot native power requires GPIO0 physical high=ON; use active_low: false")
        return controller

    @export
    def on(self):
        self._controller().set_line(0, True)

    @export
    def off(self):
        self._controller().set_line(0, False)

    @export
    def state(self):
        return {
            "switch_enabled": self._controller().read_line(0),
            "electrical_state_verified": False,
            "note": "GPIO0 readback only; check J1 jumper and any alternate power feeds",
        }

    @export
    def read(self) -> Generator[PowerReading, None, None]:
        raise NotImplementedError("CoPilot Lite does not provide voltage/current measurements through this driver")
        yield  # Expose the standard streaming power-read interface, without fake readings.
