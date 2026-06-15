from __future__ import annotations

import errno
import platform
import re
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum

import anyio
import anyio.to_thread
import usb.core
import usb.util
from jumpstarter_driver_opendal.driver import StorageMuxFlasherInterface

from jumpstarter.common.storage import read_from_storage_device, write_to_storage_device
from jumpstarter.driver import Driver, export

# Programmed by sd-mux-ctrl --init: Samsung VID, product "sd-wire".
_PROGRAMMED_VID = 0x04E8
_PROGRAMMED_PID = 0x6001
_PROGRAMMED_PRODUCT = "sd-wire"

# Factory-default FTDI FT200X. The mux only works if CBUS0 is set to GPIO in the
# EEPROM (one-time pyftdi fix, see README); otherwise switching is ignored.
_FTDI_VID = 0x0403
_FT200X_PID = 0x6015

# Badgerd SDWire3 (Realtek-based USB3 reader + mux on one interface).
_SDWIRE3_VID = 0x0BDA
_SDWIRE3_PID = 0x0316
_SDWIRE3_MASS_STORAGE_INTERFACE = 0

# SD Wire internal USB hub; the SD reader sits on port 1.
_SMSC_HUB_VID = 0x0424
_SMSC_HUB_PID = 0x2640
_SMSC_HUB_PORT = 1

# SD card reader behind the hub (shares the SMSC vendor id).
_SMSC_READER_VID = 0x0424
_SMSC_READER_PID = 0x4050


class _DeviceKind(Enum):
    FT200X = "ft200x"
    SDWIRE3 = "sdwire3"


def _hex_id(value: int) -> str:
    return f"0x{value:04x}"


def _usb_serial(dev: usb.core.Device) -> str | None:
    try:
        if dev.iSerialNumber:
            return usb.util.get_string(dev, dev.iSerialNumber)
    except usb.core.USBError:
        pass
    return None


def _sdwire3_identity(dev: usb.core.Device) -> str:
    serial = _usb_serial(dev) or "unknown"
    port_numbers = getattr(dev, "port_numbers", None)
    if port_numbers:
        return f"{serial}.{'.'.join(map(str, port_numbers))}"
    return f"{serial}:{dev.bus}.{dev.address}"


def _serial_matches(dev: usb.core.Device, configured: str, kind: _DeviceKind) -> bool:
    if configured == _sdwire3_identity(dev):
        return True
    usb_serial = _usb_serial(dev)
    if usb_serial is not None and configured == usb_serial:
        return True
    if kind == _DeviceKind.SDWIRE3 and configured == f"{dev.bus}.{dev.address}":
        return True
    return False


def _find_smsc_hub(dev: usb.core.Device) -> usb.core.Device | None:
    # The reader and the FT200X share an internal hub, so the hub's USB path is
    # the FT200X path minus its last port. Match by bus + path to stay scoped to
    # this SD Wire when several are attached.
    hubs = list(usb.core.find(idVendor=_SMSC_HUB_VID, idProduct=_SMSC_HUB_PID, find_all=True))
    if not hubs:
        return None

    def ports(device: usb.core.Device) -> tuple:
        try:
            port_numbers = getattr(device, "port_numbers", None)
            return tuple(port_numbers) if port_numbers else ()
        except (NotImplementedError, AttributeError):
            return ()

    dev_ports = ports(dev)
    if dev_ports:
        parent_ports = dev_ports[:-1]
        for hub in hubs:
            if hub.bus == dev.bus and ports(hub) == parent_ports:
                return hub
        return None  # topology known but no match: don't guess

    return hubs[0] if len(hubs) == 1 else None


def _power_cycle_smsc_port(dev: usb.core.Device, logger=None) -> None:
    # diskutil eject leaves the reader stopped; toggling its hub port power makes
    # macOS re-enumerate it so the card is accessible again.
    hub = _find_smsc_hub(dev)
    if hub is None:
        if logger:
            logger.warning("could not find the SMSC hub to power-cycle; card may not re-mount")
        return
    try:
        hub.ctrl_transfer(0x23, 1, 8, _SMSC_HUB_PORT, None)  # CLEAR PORT_POWER
        time.sleep(1)
        hub.ctrl_transfer(0x23, 3, 8, _SMSC_HUB_PORT, None)  # SET PORT_POWER
        time.sleep(3)
    except usb.core.USBError:
        pass  # best-effort; caller raises if the disk never appears


def _iter_block_links(udevice) -> str | None:
    for child in udevice.children:
        if child.subsystem == "block":
            for storage_device in filter(
                lambda link: link.startswith("/dev/disk/by-diskseq/"), child.device_links
            ):
                return storage_device
        found = _iter_block_links(child)
        if found is not None:
            return found
    return None


def _find_storage_device_linux(dev: usb.core.Device, *, kind: _DeviceKind) -> str | None:
    try:
        import pyudev

        context = pyudev.Context()
        for udevice in (
            context.list_devices(subsystem="usb")
            .match_attribute("busnum", dev.bus)
            .match_attribute("devnum", dev.address)
        ):
            if kind == _DeviceKind.SDWIRE3:
                found = _iter_block_links(udevice)
                if found is not None:
                    return found
                continue

            for block in filter(lambda d: d.subsystem == "block", udevice.parent.children):
                for storage_device in filter(
                    lambda link: link.startswith("/dev/disk/by-diskseq/"), block.device_links
                ):
                    return storage_device
    except (ImportError, OSError, AttributeError):
        pass
    return None


def _node_is_ft200x_controller(node: dict, serial: str | None) -> bool:
    if serial is not None:
        return node.get("serial_num") == serial
    vid = node.get("vendor_id", "").lower()
    pid = node.get("product_id", "").lower()
    return (_hex_id(_PROGRAMMED_VID) in vid and _hex_id(_PROGRAMMED_PID) in pid) or (
        _hex_id(_FTDI_VID) in vid and _hex_id(_FT200X_PID) in pid
    )


def _node_is_sdwire3(node: dict, serial: str | None, bus: int | None, address: int | None) -> bool:
    vid = node.get("vendor_id", "").lower()
    pid = node.get("product_id", "").lower()
    if not (_hex_id(_SDWIRE3_VID) in vid and _hex_id(_SDWIRE3_PID) in pid):
        return False
    if serial is None:
        return True
    node_serial = node.get("serial_num")
    if node_serial == serial:
        return True
    if bus is not None and address is not None:
        location_id = node.get("location_id", "")
        if location_id.endswith(f"/ {address}"):
            return serial.endswith(f":{bus}.{address}") or serial.endswith(f".{bus}.{address}")
    return False


def _reader_bsd(node: dict) -> str | None:
    # The disk node lives under the reader's nested "Media" list, not at top level.
    vid = node.get("vendor_id", "").lower()
    pid = node.get("product_id", "").lower()
    if not (_hex_id(_SMSC_READER_VID) in vid and _hex_id(_SMSC_READER_PID) in pid):
        return None
    for media in node.get("Media", []):
        bsd = media.get("bsd_name")
        if bsd:
            return f"/dev/{bsd}"
    return None


def _sdwire3_bsd(node: dict) -> str | None:
    for media in node.get("Media", []):
        bsd = media.get("bsd_name")
        if bsd:
            return f"/dev/{bsd}"
    return None


def _load_macos_usb_tree() -> list[dict] | None:
    try:
        import json

        out = subprocess.check_output(
            ["system_profiler", "SPUSBDataType", "-json"],
            text=True,
            timeout=15,
        )
        data = json.loads(out)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return data.get("SPUSBDataType", [])


def _find_storage_device_macos(serial: str | None) -> str | None:
    # pyudev is Linux-only; on macOS walk system_profiler and pick the SD reader
    # that is a sibling of this SD Wire's FT200X.
    tree = _load_macos_usb_tree()
    if tree is None:
        return None

    results: list[str] = []

    def walk(items: list[dict]) -> None:
        for node in items:
            if _node_is_ft200x_controller(node, serial):
                for sibling in items:
                    bsd = _reader_bsd(sibling)
                    if bsd:
                        results.append(bsd)
            walk(node.get("_items", []))

    walk(tree)

    unique = list(dict.fromkeys(results))
    if not unique:
        return None
    if len(unique) > 1:
        raise RuntimeError(
            "found multiple SD Wire storage devices on macOS; specify storage_device or serial to disambiguate"
        )
    return unique[0]


def _ioreg_sdwire3_hosts() -> list[tuple[str, str | None]]:
    # Returns [(ioreg_name, usb_serial), ...] for each attached SDWire3.
    try:
        out = subprocess.check_output(
            ["ioreg", "-p", "IOUSB", "-l", "-w", "0"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    hosts: list[tuple[str, str | None]] = []
    for name in dict.fromkeys(re.findall(r"\+-o (USB3\.0-CRW@[0-9a-fA-F]+)", out)):
        serial = None
        try:
            detail = subprocess.check_output(
                ["ioreg", "-r", "-n", name, "-l", "-w", "0"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            hosts.append((name, serial))
            continue

        match = re.search(r'"kUSBSerialNumberString" = "([^"]*)"', detail)
        if match and match.group(1):
            serial = match.group(1)
        hosts.append((name, serial))

    return hosts


def _ioreg_whole_disk(ioreg_name: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["ioreg", "-r", "-n", ioreg_name, "-l", "-w", "0"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    whole = False
    for line in out.splitlines():
        if '"Whole" = Yes' in line:
            whole = True
            continue
        if whole and (match := re.search(r'"BSD Name" = "([^"]+)"', line)) is not None:
            return f"/dev/{match.group(1)}"
        if '"Whole" = No' in line:
            whole = False
    return None


def _sdwire3_host_matches(
    dev: usb.core.Device,
    configured_serial: str | None,
    ioreg_name: str,
    usb_serial: str | None,
) -> bool:
    if configured_serial is None:
        return True
    if configured_serial == usb_serial:
        return True
    location = ioreg_name.split("@", 1)[-1]
    identity = _sdwire3_identity(dev)
    if configured_serial in {identity, f"{usb_serial}.{location}", f"{usb_serial}:{dev.bus}.{dev.address}"}:
        return True
    return configured_serial == f"{dev.bus}.{dev.address}"


def _find_sdwire3_storage_device_macos(
    dev: usb.core.Device,
    serial: str | None,
) -> str | None:
    tree = _load_macos_usb_tree()
    if tree is not None:
        results: list[str] = []

        def walk(items: list[dict]) -> None:
            for node in items:
                if _node_is_sdwire3(node, serial, dev.bus, dev.address):
                    bsd = _sdwire3_bsd(node)
                    if bsd:
                        results.append(bsd)
                walk(node.get("_items", []))

        walk(tree)

        unique = list(dict.fromkeys(results))
        if unique:
            if len(unique) > 1:
                raise RuntimeError(
                    "found multiple SDWire3 storage devices on macOS; "
                    "specify storage_device or serial to disambiguate"
                )
            return unique[0]

    hosts = _ioreg_sdwire3_hosts()
    matches = [
        (name, usb_serial)
        for name, usb_serial in hosts
        if _sdwire3_host_matches(dev, serial, name, usb_serial)
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise RuntimeError(
            "found multiple SDWire3 storage devices on macOS; specify storage_device or serial to disambiguate"
        )

    return _ioreg_whole_disk(matches[0][0])


def _sdwire3_usb_error(exc: usb.core.USBError, action: str) -> RuntimeError:
    if platform.system() == "Darwin" and exc.errno in {errno.EACCES, 13}:
        return RuntimeError(
            f"SDWire3 {action} failed: macOS requires root privileges to capture the USB "
            f"mass-storage interface (run the exporter with sudo)"
        )
    return RuntimeError(f"SDWire3 {action} failed: {exc}")


def _sdwire3_host(dev: usb.core.Device) -> None:
    try:
        dev.attach_kernel_driver(_SDWIRE3_MASS_STORAGE_INTERFACE)
    except usb.core.USBError as exc:
        raise _sdwire3_usb_error(exc, "host switch") from exc
    try:
        dev.reset()
    except usb.core.USBError as exc:
        raise _sdwire3_usb_error(exc, "host reset") from exc


def _sdwire3_dut(dev: usb.core.Device) -> None:
    try:
        dev.detach_kernel_driver(_SDWIRE3_MASS_STORAGE_INTERFACE)
    except usb.core.USBError as exc:
        raise _sdwire3_usb_error(exc, "DUT switch") from exc
    try:
        dev.reset()
    except usb.core.USBError as exc:
        raise _sdwire3_usb_error(exc, "DUT reset") from exc


@dataclass(kw_only=True)
class SDWire(StorageMuxFlasherInterface, Driver):
    serial: str | None = field(default=None)
    dev: usb.core.Device = field(init=False)
    itf: usb.core.Interface | None = field(init=False, default=None)
    _serial: str | None = field(init=False, default=None)
    _kind: _DeviceKind = field(init=False)

    storage_device: str | None = field(default=None)
    storage_timeout: int = field(default=10)
    storage_leeway: int = field(default=6)
    storage_fsync_timeout: int = field(default=900)

    def effective_storage_device(self):
        if self.storage_device is not None:
            return self.storage_device
        if platform.system() == "Darwin":
            if self._kind == _DeviceKind.SDWIRE3:
                return _find_sdwire3_storage_device_macos(self.dev, self._serial)
            return _find_storage_device_macos(self._serial)
        return _find_storage_device_linux(self.dev, kind=self._kind)

    def _poll_storage_device(self, timeout: int) -> str | None:
        # macOS re-enumerates the reader after a switch, so retry discovery.
        deadline = time.monotonic() + timeout
        while True:
            device = self.effective_storage_device()
            if device is not None:
                return device
            if time.monotonic() >= deadline:
                return None
            time.sleep(1)

    async def _await_storage_device(self, timeout: int) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            device = await anyio.to_thread.run_sync(self.effective_storage_device)
            if device is not None:
                return device
            if time.monotonic() >= deadline:
                return None
            await anyio.sleep(1)

    def _find_device(self) -> tuple[usb.core.Device, str | None, _DeviceKind] | None:
        candidates: list[tuple[usb.core.Device, str | None, _DeviceKind]] = []

        for dev in usb.core.find(idVendor=_SDWIRE3_VID, idProduct=_SDWIRE3_PID, find_all=True):
            identity = _sdwire3_identity(dev)
            if self.serial is None or _serial_matches(dev, self.serial, _DeviceKind.SDWIRE3):
                candidates.append((dev, identity, _DeviceKind.SDWIRE3))

        # Programmed: Samsung VID + product "sd-wire".
        for dev in usb.core.find(idVendor=_PROGRAMMED_VID, idProduct=_PROGRAMMED_PID, find_all=True):
            try:
                product = usb.util.get_string(dev, dev.iProduct)
                serial = usb.util.get_string(dev, dev.iSerialNumber)
            except usb.core.USBError as e:
                self.logger.warning(f"failed to read USB descriptors at bus {dev.bus} addr {dev.address}: {e}")
                continue
            if product == _PROGRAMMED_PRODUCT and (self.serial is None or self.serial == serial):
                candidates.append((dev, serial, _DeviceKind.FT200X))

        # Unprogrammed FT200X: only by explicit serial, since any FT200X looks alike.
        if self.serial is not None:
            for dev in usb.core.find(idVendor=_FTDI_VID, idProduct=_FT200X_PID, find_all=True):
                try:
                    serial = usb.util.get_string(dev, dev.iSerialNumber)
                except usb.core.USBError as e:
                    self.logger.warning(f"failed to read USB descriptors at bus {dev.bus} addr {dev.address}: {e}")
                    continue
                if self.serial == serial:
                    candidates.append((dev, serial, _DeviceKind.FT200X))

        if self.serial is None and len(candidates) > 1:
            raise RuntimeError("found multiple sd-wire devices; specify a serial to disambiguate")

        return candidates[0] if candidates else None

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        found = self._find_device()
        if found is None:
            raise FileNotFoundError(
                "failed to find sd-wire device "
                "(checked SDWire3 0x0BDA/0x0316, programmed EEPROM 0x04E8/0x6001, and "
                "unprogrammed FTDI FT200X 0x0403/0x6015)"
            )

        dev, self._serial, self._kind = found
        self.dev = dev

        if self._kind == _DeviceKind.FT200X:
            self.itf = usb.util.find_descriptor(
                dev.get_active_configuration(),
                bInterfaceClass=0xFF,
                bInterfaceSubClass=0xFF,
                bInterfaceProtocol=0xFF,
            )
        else:
            self.itf = None

        if self.effective_storage_device() is None and self._kind == _DeviceKind.FT200X:
            raise FileNotFoundError("failed to find sdcard storage device on sd-wire")
        if self.effective_storage_device() is None and self._kind == _DeviceKind.SDWIRE3:
            self.logger.warning(
                "SDWire3 detected but no SD card disk is visible on host yet; "
                "storage operations will wait until the card appears after switching to host"
            )

    def select(self, target):
        if self._kind != _DeviceKind.FT200X:
            raise RuntimeError("select() is only supported on FT200X-based SD Wire devices")
        # CBUS bitbang: wValue = 0x20<<8 | (0xF0 DUT / 0xF1 HOST). Needs CBUS0=GPIO.
        self.dev.ctrl_transfer(
            bmRequestType=usb.ENDPOINT_OUT | usb.TYPE_VENDOR | usb.RECIP_DEVICE,
            bRequest=0xB,
            wIndex=0,
            wValue=(0x20 << 8) | target,
        )

    def query(self):
        if self._kind == _DeviceKind.SDWIRE3:
            try:
                return "host" if self.dev.is_kernel_driver_active(_SDWIRE3_MASS_STORAGE_INTERFACE) else "dut"
            except usb.core.USBError as exc:
                raise RuntimeError(f"failed to query SDWire3 mux state: {exc}") from exc

        return (
            "host"
            if self.dev.ctrl_transfer(
                bmRequestType=usb.ENDPOINT_IN | usb.TYPE_VENDOR | usb.RECIP_DEVICE,
                bRequest=0xC,
                wIndex=0,
                wValue=0,
                data_or_wLength=1,
            )[0]
            & 0x01
            else "dut"
        )

    def _eject_storage_for_dut(self) -> None:
        if platform.system() != "Darwin":
            return

        storage = self._poll_storage_device(self.storage_timeout)
        if storage is None:
            raise RuntimeError(
                "could not determine the SD card disk on macOS; refusing to switch to DUT "
                "(switching without a clean eject risks filesystem corruption)"
            )
        try:
            subprocess.run(
                ["diskutil", "eject", storage],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            self.logger.error(f"failed to eject {storage}: {e.stderr}")
            raise RuntimeError(
                f"failed to eject {storage} before switching to DUT; "
                f"the volume may be busy: {(e.stderr or '').strip()}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"timed out ejecting {storage} before switching to DUT") from e
        time.sleep(1)

    @export
    def host(self):
        if self._kind == _DeviceKind.SDWIRE3:
            _sdwire3_host(self.dev)
            return

        # Route the card back first, then (macOS) power-cycle so the re-enumeration
        # sees the card present.
        self.select(0xF1)
        if platform.system() == "Darwin":
            _power_cycle_smsc_port(self.dev, self.logger)

    @export
    def dut(self):
        if platform.system() == "Darwin":
            self._eject_storage_for_dut()

        if self._kind == _DeviceKind.SDWIRE3:
            _sdwire3_dut(self.dev)
            return

        # Power on the DUT within ~500 ms or the mux protection reverts to HOST.
        self.select(0xF0)

    @export
    def off(self):
        self.host()

    @export
    async def write(self, src: str):
        self.host()
        storage = await self._await_storage_device(self.storage_timeout)
        if storage is None:
            raise RuntimeError("SD card disk did not become available on host within storage_timeout")
        async with self.resource(src) as res:
            await write_to_storage_device(
                storage,
                res,
                timeout=self.storage_timeout,
                leeway=self.storage_leeway,
                fsync_timeout=self.storage_fsync_timeout,
                logger=self.logger,
            )

    @export
    async def read(self, dst: str):
        self.host()
        storage = await self._await_storage_device(self.storage_timeout)
        if storage is None:
            raise RuntimeError("SD card disk did not become available on host within storage_timeout")
        async with self.resource(dst) as res:
            await read_from_storage_device(
                storage,
                res,
                timeout=self.storage_timeout,
                logger=self.logger,
            )
