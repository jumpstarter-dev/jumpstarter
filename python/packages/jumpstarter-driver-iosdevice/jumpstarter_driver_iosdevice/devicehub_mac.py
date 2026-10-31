"""Client side of Device Hub access, for a Mac holding a lease.

The leased device appears to macOS as a network device on a private virtual
Ethernet link (see ``scripts/devicehub-link-macos.sh``): the Mac owns
``feth0`` and this module answers for the device on ``feth1``. Its Bonjour
advert is published only on that link, and every connection the Mac makes to
the device is carried to the exporter through the lease.
"""

import ctypes
import logging
import math
import select
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager, suppress

import anyio
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream, create_task_group, from_thread, to_thread

from .netstack import Stack

logger = logging.getLogger(__name__)

HOST_INTERFACE = "feth0"
DEVICE_INTERFACE = "feth1"
HOST_ADDRESS = "fd6a:7374:6a73::1"
DEVICE_ADDRESS = "fd6a:7374:6a73::2"
CONTROL_PORT = 49152
# A Mac paired with the stand-in connects within seconds of seeing its advert.
CONNECT_GRACE = 20.0
PAIRABLE_HOST_SERVICE = "_remotepairing-pairable-host._tcp"
SETUP_SCRIPT = "scripts/devicehub-link-macos.sh"

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
    """Wait for Device Hub to offer pairing; give up if the Mac connects meanwhile."""
    paired = False
    async with create_task_group() as tasks:

        async def connects():
            await connected.wait()
            tasks.cancel_scope.cancel()

        tasks.start_soon(connects)
        await pair(client, code, timeout=math.inf)
        paired = True
        tasks.cancel_scope.cancel()
    return paired


@contextmanager
def advertised(identifier: str, auth_tag: str, port: int):
    advert = Advert(identifier, auth_tag, port)
    try:
        yield
    finally:
        advert.close()


async def serve(client, *, code: Callable[[], Awaitable[str]], force_pair: bool, notify: Callable[[str], None]) -> None:
    """Make the leased device appear in Device Hub until cancelled."""
    check_link()
    async with Stack(DEVICE_INTERFACE, DEVICE_ADDRESS) as stack, create_task_group() as tasks:
        accepted = stack.listen(CONTROL_PORT)
        connected = anyio.Event()

        async def accept():
            while True:
                connection = await accepted.get()
                connected.set()
                tasks.start_soon(_carry, client, connection)

        tasks.start_soon(accept)
        info = await client.call_async("devicehub_info", CONTROL_PORT)
        name = info["name"]
        if force_pair or not info.get("paired_hosts"):
            notify(f"Pair this Mac with {name} once: in Device Hub choose File > Pair Nearby Device…")
            await pair(client, code)
        # The exporter cannot tell which Macs still hold their pairing: a Mac that
        # unpaired, or never paired, ignores the advert. Offer pairing if none comes.
        while True:
            with advertised(info["identifier"], info["auth_tag"], CONTROL_PORT):
                notify(f"{name} is available in Device Hub. Press Ctrl+C to remove it.")
                with anyio.move_on_after(CONNECT_GRACE):
                    await connected.wait()
                if not connected.is_set():
                    notify(
                        f"Device Hub has not connected to {name}. If this Mac is not paired with it, "
                        "choose File > Pair Nearby Device… in Device Hub."
                    )
                if connected.is_set() or not await _pair_unless_connected(client, code, connected):
                    await anyio.sleep_forever()
            # Advertise afresh so the newly paired Mac looks at it again.
