"""Client side of Device Hub access, for a Mac holding a lease.

The leased device appears to macOS as a network device on a private virtual
Ethernet link (see ``scripts/devicehub-link-macos.sh``): the Mac owns
``feth0`` and this module answers for the device on ``feth1``. Its Bonjour
advert is published only on that link, and every connection the Mac makes to
the device is carried to the exporter through the lease.
"""

import ctypes
import json
import logging
import math
import plistlib
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from xml.parsers.expat import ExpatError

import anyio
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    create_task_group,
    fail_after,
    from_thread,
    move_on_after,
    to_thread,
)

from .netstack import Stack

logger = logging.getLogger(__name__)

HOST_INTERFACE = "feth0"
DEVICE_INTERFACE = "feth1"
HOST_ADDRESS = "fd6a:7374:6a73::1"
DEVICE_ADDRESS = "fd6a:7374:6a73::2"
CONTROL_PORT = 49152
PAIRABLE_HOST_SERVICE = "_remotepairing-pairable-host._tcp"
SETUP_SCRIPT = "scripts/devicehub-link-macos.sh"
XCODE_MINIMUM = 27
DEVICE_HUB_APP = "Contents/Applications/DeviceHub.app"
XCODE_FOLDERS = (Path("/Applications"), Path.home() / "Applications")
# Installed system-wide when Xcode 27 is first opened; Device Hub streams through it.
MEDIA_STREAM_SUPPORT = Path(
    "/Library/Developer/PrivateFrameworks/CoreDevice.framework/Versions/A/Frameworks/"
    "CoreDeviceMediaStreamSupport.framework"
)
XCODEBUILD = ("xcodebuild", "test-without-building")
WDA_READY = re.compile(r"ServerURLHere->(\S+)<-ServerURLHere")
WDA_TIMEOUT = 180.0
WDA_STOP_TIMEOUT = 15.0
# xcodebuild output that explains a failed WebDriverAgent start.
WDA_FAILURES = {
    "Timed out while enabling automation mode": (
        "the device refused UI automation: turn on Settings > Developer > Enable UI Automation and keep it unlocked"
    ),
    "ApplicationVerificationFailed": (
        "the device rejected the WebDriverAgent build: its provisioning profile must include this device"
    ),
    "Unable to find a destination matching": (
        "Xcode does not list the device; check that it is shown in Device Hub and that the build is for iOS devices"
    ),
}

_flags = ctypes.c_uint32
_ref = ctypes.c_void_p
_REGISTER_RECORD_CALLBACK = ctypes.CFUNCTYPE(None, _ref, _ref, _flags, ctypes.c_int32, ctypes.c_void_p)
_BROWSE_CALLBACK = ctypes.CFUNCTYPE(
    None,
    _ref,
    _flags,
    ctypes.c_uint32,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_void_p,
)
_RESOLVE_CALLBACK = ctypes.CFUNCTYPE(
    None,
    _ref,
    _flags,
    ctypes.c_uint32,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_uint16,
    ctypes.c_uint16,
    ctypes.c_void_p,
    ctypes.c_void_p,
)
_FLAG_UNIQUE = 0x20
_TYPE_AAAA = 28


def _dns_sd():
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    library.DNSServiceRefSockFD.restype = ctypes.c_int
    return library


def _xcode_apps() -> list[Path]:
    apps = []
    with suppress(OSError, subprocess.SubprocessError):
        developer = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, check=True, timeout=5)
        apps.append(Path(developer.stdout.strip()).parents[1])
    for folder in XCODE_FOLDERS:
        apps.extend(sorted(folder.glob("Xcode*.app")))
    return list(dict.fromkeys(apps))


def _xcode_version(app: Path) -> tuple[int, ...] | None:
    try:
        version = plistlib.loads((app / "Contents/version.plist").read_bytes())["CFBundleShortVersionString"]
        return tuple(int(part) for part in str(version).split("."))
    except (OSError, KeyError, ValueError, ExpatError):
        return None


def xcode_warning() -> str | None:
    """Say why Device Hub cannot show the device on this Mac, or None when it can."""
    apps = _xcode_apps()
    if not any((app / DEVICE_HUB_APP).is_dir() for app in apps):
        versions = [version for version in map(_xcode_version, apps) if version is not None]
        found = "Xcode " + ".".join(map(str, max(versions))) if versions else "no Xcode"
        return (
            f"Device Hub needs Xcode {XCODE_MINIMUM} or later, but this Mac has {found}. "
            "The device will not appear until a newer Xcode is installed."
        )
    if not MEDIA_STREAM_SUPPORT.is_dir():
        return "Xcode has not finished installing its device support. Open Xcode once, then run this again."
    return None


def check_link() -> None:
    """Fail with setup instructions unless the virtual link is configured."""
    if sys.platform != "darwin":
        raise RuntimeError("Device Hub access needs a Mac client")
    try:
        output = subprocess.run(["ifconfig", HOST_INTERFACE], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        output = ""
    if HOST_ADDRESS not in output:
        raise RuntimeError(
            f"the Device Hub link is not set up; run `sudo {SETUP_SCRIPT}` from jumpstarter-driver-iosdevice "
            "once after each boot"
        )


def _txt(entries: dict) -> bytes:
    record = b""
    for key, value in entries.items():
        item = f"{key}={value}".encode()
        record += bytes([len(item)]) + item
    return record


class Advert:
    """A ``_remotepairing._tcp`` advert for the stand-in, published only on the link."""

    def __init__(self, identifier: str, auth_tag: str, port: int, *, version: int = 26, minimum: int = 8):
        self._lib = _dns_sd()
        self._refs: list[_ref] = []
        index = socket.if_nametoindex(HOST_INTERFACE)
        host = f"jumpstarter-{identifier[:8].lower()}.local."
        connection = _ref()
        if self._lib.DNSServiceCreateConnection(ctypes.byref(connection)):
            raise OSError("cannot connect to mDNSResponder")
        self._refs.append(connection)
        record = _ref()
        address = socket.inet_pton(socket.AF_INET6, DEVICE_ADDRESS)
        self._callback = _REGISTER_RECORD_CALLBACK(lambda *args: None)
        error = self._lib.DNSServiceRegisterRecord(
            connection,
            ctypes.byref(record),
            _FLAG_UNIQUE,
            ctypes.c_uint32(index),
            host.encode(),
            ctypes.c_uint16(_TYPE_AAAA),
            ctypes.c_uint16(1),
            ctypes.c_uint16(len(address)),
            address,
            ctypes.c_uint32(120),
            self._callback,
            None,
        )
        if error:
            raise OSError(f"cannot publish the device address ({error})")
        txt = _txt({"identifier": identifier, "authTag": auth_tag, "ver": version, "minVer": minimum, "flags": 0})
        service = _ref()
        error = self._lib.DNSServiceRegister(
            ctypes.byref(service),
            0,
            ctypes.c_uint32(index),
            identifier.encode(),
            b"_remotepairing._tcp",
            None,
            host.encode(),
            ctypes.c_uint16(socket.htons(port)),
            ctypes.c_uint16(len(txt)),
            txt,
            None,
            None,
        )
        if error:
            raise OSError(f"cannot advertise the device ({error})")
        self._refs.append(service)

    def close(self) -> None:
        for ref in reversed(self._refs):
            self._lib.DNSServiceRefDeallocate(ref)
        self._refs.clear()


def _wait_dns(lib, ref: _ref, done: Callable[[], bool], deadline: float) -> None:
    fd = lib.DNSServiceRefSockFD(ref)
    while not done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Device Hub is not offering pairing; choose File > Pair Nearby Device… first")
        from_thread.check_cancelled()
        readable, _, _ = select.select([fd], [], [], min(remaining, 0.5))
        if readable:
            lib.DNSServiceProcessResult(ref)


def find_pairable_host(timeout: float) -> int:
    """Return the port of this Mac's "Pair Nearby Device" listener.

    Runs in a worker thread and stops when the task waiting on it is cancelled.
    """
    lib = _dns_sd()
    found: list[tuple[int, bytes, bytes, bytes]] = []
    ports: list[int] = []

    @_BROWSE_CALLBACK
    def browsed(_ref_, flags, index, error, name, regtype, domain, _context):
        if not error and flags & 0x2:  # kDNSServiceFlagsAdd
            found.append((index, name, regtype, domain))

    @_RESOLVE_CALLBACK
    def resolved(_ref_, _flags_, _index, error, _fullname, _host, port, _txt_len, _txt, _context):
        if not error:
            ports.append(socket.ntohs(port))

    deadline = time.monotonic() + timeout
    browse = _ref()
    if lib.DNSServiceBrowse(ctypes.byref(browse), 0, 0, PAIRABLE_HOST_SERVICE.encode(), None, browsed, None):
        raise OSError("cannot browse for Device Hub")
    try:
        _wait_dns(lib, browse, lambda: bool(found), deadline)
    finally:
        lib.DNSServiceRefDeallocate(browse)
    index, name, regtype, domain = found[0]
    resolve = _ref()
    if lib.DNSServiceResolve(ctypes.byref(resolve), 0, index, name, regtype, domain, resolved, None):
        raise OSError("cannot resolve Device Hub's pairing listener")
    try:
        _wait_dns(lib, resolve, lambda: bool(ports), deadline)
    finally:
        lib.DNSServiceRefDeallocate(resolve)
    return ports[0]


async def _pump(source, destination) -> None:
    try:
        while True:
            await destination.send(await source.receive())
    except (EndOfStream, BrokenResourceError, ClosedResourceError, OSError):
        pass
    with suppress(BrokenResourceError, ClosedResourceError, OSError, NotImplementedError):
        await destination.send_eof()


async def relay(left, right) -> None:
    async with create_task_group() as tasks:
        tasks.start_soon(_pump, right, left)
        await _pump(left, right)
        await anyio.sleep(0.5)  # let the other direction drain before tearing down
        tasks.cancel_scope.cancel()


async def _carry(client, connection) -> None:
    try:
        async with client.stream_async("connect_devicehub") as exporter, connection:
            await relay(connection, exporter)
    except Exception as error:  # noqa: BLE001 - one failed connection must not end the session
        logger.warning("Device Hub connection from the Mac failed: %s", error)


async def pair(client, code: Callable[[], Awaitable[str]], *, timeout: float = 300.0) -> None:
    """Pair the stand-in with this Mac while Device Hub shows "Pair Nearby Device"."""
    port = await to_thread.run_sync(find_pairable_host, timeout)
    async with (
        await anyio.connect_tcp("127.0.0.1", port) as mac,
        client.stream_async("connect_devicehub_pairing") as exporter,
        create_task_group() as tasks,
    ):
        tasks.start_soon(relay, mac, exporter)
        await client.call_async("devicehub_pairing_code", await code())


async def _pair_unless_connected(client, code: Callable[[], Awaitable[str]], connected: anyio.Event) -> bool:
    """Wait for Device Hub to offer pairing; give up if the Mac connects before a code is entered.

    A newly paired Mac connects as soon as pairing succeeds, often before it returns.
    """
    entered = False
    async with create_task_group() as tasks:

        async def connects():
            await connected.wait()
            if not entered:
                tasks.cancel_scope.cancel()

        async def entered_code() -> str:
            nonlocal entered
            value = await code()
            entered = True
            return value

        tasks.start_soon(connects)
        await pair(client, entered_code, timeout=math.inf)
        tasks.cancel_scope.cancel()
    return entered


async def _advertise_until_connected(client, code, info: dict, connected: anyio.Event, notify) -> Advert:
    """Advertise the device until this Mac connects to it; the advert stays published."""
    # Neither the exporter nor macOS tools can tell whether this Mac still holds its
    # pairing: a Mac that unpaired, or never paired, ignores the advert. So pairing
    # is offered from the start, and dropped if the Mac connects first.
    name = info["name"]
    advert = None
    try:
        while advert is None or not connected.is_set():
            if advert is not None:
                # Advertise afresh so the newly paired Mac looks at it again.
                advert.close()
            advert = Advert(info["identifier"], info["auth_tag"], CONTROL_PORT)
            notify(
                f"{name} is available in Device Hub. If it is not listed there, for example after Unpair, "
                "choose File > Pair Nearby Device… in Device Hub to pair this Mac with it. "
                "Press Ctrl+C to make it unavailable."
            )
            if await _pair_unless_connected(client, code, connected):
                notify(f"This Mac is paired with {name}.")
    except BaseException:
        if advert is not None:
            advert.close()
        raise
    return advert


@asynccontextmanager
async def shown(
    client, *, code: Callable[[], Awaitable[str]], force_pair: bool, notify: Callable[[str], None]
) -> AsyncGenerator[dict, None]:
    """Show the leased device in Device Hub; enter once this Mac has connected to it."""
    if (warning := xcode_warning()) is not None:
        notify(f"Warning: {warning}")
    check_link()
    error = None
    async with Stack(DEVICE_INTERFACE, DEVICE_ADDRESS) as stack, create_task_group() as tasks:
        accepted = stack.listen(CONTROL_PORT)
        connected = anyio.Event()

        async def accept():
            while True:
                connection = await accepted.get()
                connected.set()
                tasks.start_soon(_carry, client, connection)

        tasks.start_soon(accept)
        advert = None
        try:
            info = await client.call_async("devicehub_info", CONTROL_PORT)
            if force_pair or not info.get("paired_hosts"):
                notify(f"Pair this Mac with {info['name']} once: in Device Hub choose File > Pair Nearby Device…")
                await pair(client, code)
            advert = await _advertise_until_connected(client, code, info, connected, notify)
            yield info
        except BaseException as exc:  # noqa: BLE001 - re-raised below, outside the task group
            error = exc
        finally:
            if advert is not None:
                advert.close()
            tasks.cancel_scope.cancel()
    if error is not None:
        raise error


async def serve(client, *, code: Callable[[], Awaitable[str]], force_pair: bool, notify: Callable[[str], None]) -> None:
    """Make the leased device appear in Device Hub until cancelled."""
    async with shown(client, code=code, force_pair=force_pair, notify=notify):
        await anyio.sleep_forever()


@dataclass(frozen=True)
class WebDriverAgent:
    """A WebDriverAgent running on the leased device, reached through the Device Hub tunnel."""

    url: str
    udid: str
    platform_version: str

    def capabilities(self) -> dict:
        """Appium XCUITest capabilities for a session on this WebDriverAgent.

        Appium reaches real devices through usbmux to find them, read their logs
        and manage apps. This device is reached only through CoreDevice, so those
        steps are skipped: start apps with ``mobile: activateApp`` instead of
        ``appium:bundleId``.
        """
        return {
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:udid": self.udid,
            "appium:platformVersion": self.platform_version,
            "appium:webDriverAgentUrl": self.url,
            "appium:skipLogCapture": True,
            "appium:noReset": True,
        }


def coredevice(udids: list[str]) -> dict | None:
    """The CoreDevice record of the leased device, or None while macOS does not list it.

    macOS lists the device under the UDID it presents until the first tunnel
    reveals its own, so both are looked for.
    """
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "devices.json"
        try:
            subprocess.run(
                ["xcrun", "devicectl", "list", "devices", "--quiet", "--json-output", str(output)],
                capture_output=True,
                check=True,
                timeout=30,
            )
            devices = json.loads(output.read_text())["result"]["devices"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            return None
    for device in devices:
        if isinstance(device, dict) and device.get("hardwareProperties", {}).get("udid") in udids:
            return device
    return None


async def _listed(udids: list[str], timeout: float, *, tunnel: bool = False) -> dict:
    with move_on_after(timeout):
        while True:
            device = await to_thread.run_sync(coredevice, udids)
            properties = (device or {}).get("connectionProperties", {})
            if device is not None and (not tunnel or properties.get("tunnelIPAddress")):
                return device
            await anyio.sleep(1)
    what = "a tunnel to the device" if tunnel else "the device"
    raise RuntimeError(f"macOS did not list {what} within {timeout:.0f} seconds")


def _wda_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/status", timeout=5) as response:
            return bool(json.load(response).get("value", {}).get("ready"))
    except (OSError, ValueError, AttributeError):
        return False


def _wda_failure(lines) -> str:
    output = "\n".join(lines)
    for marker, reason in WDA_FAILURES.items():
        if marker in output:
            return f"WebDriverAgent did not start: {reason}"
    errors = [line for line in lines if "error" in line.lower()]
    return "WebDriverAgent did not start; xcodebuild reported:\n" + "\n".join((errors or list(lines))[-15:])


def _connect(udid: str) -> None:
    """Ask CoreDevice to connect to the device, which brings up its tunnel."""
    with tempfile.TemporaryDirectory() as folder, suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            [
                "xcrun",
                "devicectl",
                "device",
                "info",
                "details",
                "--device",
                udid,
                "--quiet",
                "--json-output",
                str(Path(folder) / "details.json"),
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )


@asynccontextmanager
async def webdriveragent(
    xctestrun: Path, *, udids: list[str], timeout: float = WDA_TIMEOUT, notify: Callable[[str], None]
) -> AsyncGenerator[WebDriverAgent, None]:
    """Start WebDriverAgent from a signed ``.xctestrun`` build over the Device Hub tunnel.

    xcodebuild installs and runs it through CoreDevice, which reaches the device
    only through the lease. It stops when the context closes.
    """
    device = await _listed(udids, timeout)
    # Until its tunnel is up, macOS lists the device under the presented UDID, which
    # xcodebuild does not offer as a destination.
    await to_thread.run_sync(_connect, device["hardwareProperties"]["udid"])
    device = await _listed(udids, timeout, tunnel=True)
    udid = device["hardwareProperties"]["udid"]
    notify(f"Starting WebDriverAgent from {xctestrun.name}…")
    command = [*XCODEBUILD, "-xctestrun", str(xctestrun), "-destination", f"id={udid}"]
    error = None
    # Detached from the terminal: Ctrl+C reaches only this process, which stops
    # xcodebuild itself, and xcodebuild cannot prompt for a password there.
    async with await anyio.open_process(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    ) as process:
        lines: deque[str] = deque(maxlen=200)
        started = anyio.Event()
        port: list[int] = []

        async def read():
            assert process.stdout is not None
            buffer = b""
            async for chunk in process.stdout:
                buffer += chunk
                *complete, buffer = buffer.split(b"\n")
                for raw in complete:
                    line = raw.decode(errors="replace")
                    lines.append(line)
                    if not started.is_set() and (match := WDA_READY.search(line)):
                        port.append(int(match.group(1).rstrip("/").rsplit(":", 1)[1]))
                        started.set()
            # xcodebuild ended: before the ready line, that is a failure.
            started.set()

        async with create_task_group() as tasks:
            tasks.start_soon(read)
            try:
                try:
                    with fail_after(timeout):
                        await started.wait()
                    if not port:
                        raise RuntimeError(_wda_failure(lines))
                    device = await _listed(udids, timeout, tunnel=True)
                    url = f"http://[{device['connectionProperties']['tunnelIPAddress']}]:{port[0]}"
                    with fail_after(timeout):
                        while not await to_thread.run_sync(_wda_ready, url):
                            await anyio.sleep(1)
                except TimeoutError:
                    raise RuntimeError(f"WebDriverAgent was not ready within {timeout:.0f} seconds") from None
                platform_version = str(device.get("deviceProperties", {}).get("osVersionNumber", ""))
                # Listed under the device's own UDID once the tunnel is up.
                udid = device["hardwareProperties"]["udid"]
                yield WebDriverAgent(url=url, udid=udid, platform_version=platform_version)
            except BaseException as exc:  # noqa: BLE001 - re-raised below, outside the task group
                error = exc
            finally:
                with anyio.CancelScope(shield=True):
                    await _stop(process)
                tasks.cancel_scope.cancel()
    if error is not None:
        raise error


async def _stop(process) -> None:
    if process.returncode is not None:
        return
    # xcodebuild stops the test runner, and with it WebDriverAgent, on SIGINT.
    with suppress(ProcessLookupError):
        process.send_signal(signal.SIGINT)
    with move_on_after(WDA_STOP_TIMEOUT):
        await process.wait()
        return
    with suppress(ProcessLookupError):
        process.kill()
    await process.wait()
