"""Exporter side of Device Hub access to a leased device.

The Mac pairs with a stand-in identity that lives on the exporter and reaches
it through the lease. When the Mac asks for a tunnel, the exporter opens the
device's own CoreDevice tunnel over USB with its real pairing and splices the
two: after both handshakes each side carries plain IPv6 packets, and the
device's addresses are passed through unchanged.
"""

import base64
import hashlib
import json
import logging
import os
import ssl
import struct
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    Event,
    IncompleteRead,
    create_task_group,
    fail_after,
)
from anyio.abc import ByteStream
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.tls import TLSStream

from .remotepairing import PACKET_MAGIC, ControlChannel, DeviceIdentity, HostPairing, RemotePairingError
from .usbmux import _BufferedDuplexStream, _memory_pair

logger = logging.getLogger(__name__)

CORE_DEVICE_PROXY = "com.apple.internal.devicecompute.CoreDeviceProxy"
CDTUNNEL_MAGIC = b"CDTunnel"
MAX_HANDSHAKE = 0xFFFF
RELAY_CHUNK = 65536
# Lockdown values the Mac shows for a network device; none identify its owner.
SHOWN_VALUES = (
    "BuildVersion",
    "CPUArchitecture",
    "DeviceClass",
    "DeviceColor",
    "DeviceEnclosureColor",
    "DeviceName",
    "HardwareModel",
    "HumanReadableProductVersionString",
    "ProductName",
    "ProductType",
    "ProductVersion",
    "SupplementalBuildVersion",
)


async def _read_cdtunnel(reader: BufferedByteReceiveStream) -> dict:
    if await reader.receive_exactly(len(CDTUNNEL_MAGIC)) != CDTUNNEL_MAGIC:
        raise RemotePairingError("not a CoreDevice tunnel handshake")
    (length,) = struct.unpack(">H", await reader.receive_exactly(2))
    message = json.loads(await reader.receive_exactly(length))
    if not isinstance(message, dict):
        raise RemotePairingError("invalid CoreDevice tunnel handshake")
    return message


def _cdtunnel(message: dict) -> bytes:
    body = json.dumps(message).encode()
    if len(body) > MAX_HANDSHAKE:
        raise RemotePairingError("CoreDevice tunnel handshake too large")
    return CDTUNNEL_MAGIC + struct.pack(">H", len(body)) + body


async def _pump(source, destination) -> None:
    try:
        while True:
            await destination.send(await source.receive(RELAY_CHUNK))
    except (EndOfStream, BrokenResourceError, ClosedResourceError):
        pass
    # TLS streams cannot half-close; the caller tears the tunnel down instead.
    # anyio's TLSStream.send_eof() raises TypeError once the TLS session has ended.
    with suppress(BrokenResourceError, ClosedResourceError, OSError, NotImplementedError, TypeError):
        await destination.send_eof()


class DeviceHubBridge:
    """The stand-in identity for one device and the streams that use it."""

    def __init__(self, broker, *, udid: str, state: Path | None = None, presented_udid: str | None = None):
        self.broker = broker
        self.udid = udid
        self.presented_udid = presented_udid or udid
        self.state = state
        self.port: int | None = None
        self.identity: DeviceIdentity | None = None
        self._tunnel_keys: deque[bytes] = deque(maxlen=8)
        self._code: str | None = None
        self._code_ready = Event()

    def _save(self) -> None:
        if self.state is not None and self.identity is not None:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            # The file holds the stand-in's signing key: never readable by others, even briefly.
            descriptor = os.open(self.state, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as file:
                file.write(json.dumps(self.identity.to_dict()))

    async def _saved(self) -> None:
        self._save()

    async def prepare(self, port: int) -> dict:
        """Load or create the identity and refresh the properties the Mac shows."""
        values = await self.broker.lockdown_values([None, "com.apple.security.mac.amfi"])
        general = values.get("NULL", {})
        name = str(general.get("DeviceName") or "iOS device")
        model = str(general.get("ProductType") or "iPhone")
        if self.identity is None and self.state is not None and self.state.exists():
            identity = DeviceIdentity.from_dict(json.loads(self.state.read_text()))
            if identity.udid == self.presented_udid:
                self.identity = identity
        if self.identity is None:
            self.identity = DeviceIdentity.generate(name=name, model=model, udid=self.presented_udid)
            self._save()
        self.identity.name, self.identity.model = name, model
        if self.presented_udid == self.udid and isinstance(general.get("UniqueChipID"), int):
            self.identity.ecid = general["UniqueChipID"]
        else:
            # A stand-in under a different UDID must not share the real device's ECID.
            digest = hashlib.sha256(self.presented_udid.encode()).digest()
            self.identity.ecid = int.from_bytes(digest[:6], "big")
        self.identity.kvs = {"NULL": {key: general[key] for key in SHOWN_VALUES if key in general}}
        self.identity.kvs["NULL"]["UserAssignedDeviceName"] = name
        self.identity.kvs["NULL"]["DeviceSupportsLockdown"] = True
        if "com.apple.security.mac.amfi" in values:
            self.identity.kvs["com.apple.security.mac.amfi"] = values["com.apple.security.mac.amfi"]
        self.port = port
        return {
            "identifier": self.identity.identifier,
            "auth_tag": self.identity.auth_tag,
            "name": name,
            "model": model,
            "paired_hosts": len(self.identity.hosts),
        }

    async def _request(self, name: str, body: dict) -> dict | None:
        if name != "createListener":
            return None
        if body.get("transportProtocolType") != "tcp" or self.port is None:
            raise RemotePairingError("only TCP tunnels are supported")
        self._tunnel_keys.append(base64.b64decode(body["key"]))
        return {"port": self.port}

    async def serve_control(self, stream: ByteStream, reader: BufferedByteReceiveStream | None = None) -> None:
        if self.identity is None:
            raise RemotePairingError("prepare Device Hub access before connecting")
        channel = ControlChannel(stream, self.identity, on_request=self._request, on_paired=self._saved, reader=reader)
        try:
            await channel.serve()
        except (EndOfStream, BrokenResourceError, ClosedResourceError):
            pass

    def _tunnel_context(self) -> ssl.SSLContext:
        if not self._tunnel_keys:
            raise RemotePairingError("no tunnel was requested on the control channel")
        key = self._tunnel_keys[-1]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("PSK")
        # jmp puts truststore's SSLContext in place of ssl.SSLContext. Up to 0.10.5 it
        # keeps a PSK callback to itself instead of passing it to the context it wraps,
        # and OpenSSL then refuses every PSK cipher suite: "no shared cipher".
        getattr(context, "_ctx", context).set_psk_server_callback(lambda _identity: key)
        return context

    async def serve_tunnel(self, stream: ByteStream, reader: BufferedByteReceiveStream | None = None) -> None:
        """Terminate the Mac's TLS-PSK tunnel and splice it onto the device's tunnel."""
        if reader is not None:
            stream = _BufferedDuplexStream(stream, reader)
        with fail_after(self.broker.timeout):
            mac = await TLSStream.wrap(
                stream, server_side=True, ssl_context=self._tunnel_context(), standard_compatible=False
            )
            mac_reader = BufferedByteReceiveStream(mac)
            request = await _read_cdtunnel(mac_reader)
        logger.info("Mac requested a tunnel: %s", request)
        async with self.broker.open_service(CORE_DEVICE_PROXY) as device:
            device_reader = BufferedByteReceiveStream(device)
            with fail_after(self.broker.timeout):
                await device.send(_cdtunnel(request))
                response = await _read_cdtunnel(device_reader)
            logger.info("device tunnel parameters: %s", response)
            await mac.send(_cdtunnel(response))
            async with create_task_group() as tasks:
                tasks.start_soon(_pump, _BufferedDuplexStream(mac, mac_reader), device)
                await _pump(_BufferedDuplexStream(device, device_reader), mac)
                tasks.cancel_scope.cancel()

    def set_pairing_code(self, code: str) -> None:
        self._code = code
        self._code_ready.set()

    async def _wait_code(self) -> str:
        await self._code_ready.wait()
        assert self._code is not None
        code, self._code, self._code_ready = self._code, None, Event()
        return code

    async def serve_pairing(self, stream: ByteStream) -> str:
        """Pair the stand-in with a Mac that is showing "Pair Nearby Device"."""
        if self.identity is None:
            raise RemotePairingError("prepare Device Hub access before pairing")
        host = await HostPairing(stream, self.identity, self._wait_code).pair()
        self._save()
        return host


@asynccontextmanager
async def served(handler: Callable[[ByteStream], Awaitable[object]]):
    """Yield the client end of a stream whose other end runs ``handler``."""
    client, server = _memory_pair()
    caller_error = None

    async def run():
        try:
            async with server:
                await handler(server)
        except (EndOfStream, IncompleteRead, BrokenResourceError, ClosedResourceError, TimeoutError) as error:
            logger.info("Device Hub stream ended: %r", error)
        except Exception as error:  # noqa: BLE001 - a bad Mac connection or device detach must not fail the stream RPC
            logger.warning("Device Hub stream failed: %s", error)

    async with client, server, create_task_group() as tasks:
        tasks.start_soon(run)
        try:
            yield client
        except BaseException as exc:  # noqa: BLE001 - Preserve caller failures, including cancellation.
            caller_error = exc
        finally:
            tasks.cancel_scope.cancel()
    if caller_error is not None:
        raise caller_error


async def serve_connection(bridge: DeviceHubBridge, stream: ByteStream) -> None:
    """Serve a connection the Mac made to the stand-in: control channel or tunnel."""
    # Read a whole chunk and hand it back through a fresh buffer: feed_data appends.
    chunk = await stream.receive()
    reader = BufferedByteReceiveStream(stream)
    reader.feed_data(chunk)
    first = chunk[:1]
    if first == PACKET_MAGIC[:1]:
        await bridge.serve_control(stream, reader)
    elif first == b"\x16":
        await bridge.serve_tunnel(stream, reader)
    else:
        raise RemotePairingError(f"unexpected first byte {first!r} from the Mac")
