"""A bounded usbmux plist transport and a proxy scoped to one device.

Clients may read the selected device's pair record and the daemon's host ID,
but cannot modify pairing or other shared daemon state.
"""

import math
import plistlib
import socket
import struct
from contextlib import asynccontextmanager
from dataclasses import dataclass
from xml.parsers.expat import ExpatError

from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    IncompleteRead,
    connect_tcp,
    connect_unix,
    create_memory_object_stream,
    create_task_group,
    fail_after,
)
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.stapled import StapledByteStream, StapledObjectStream

HEADER = struct.Struct("<IIII")
MAX_MESSAGE_SIZE = 16 * 1024 * 1024
RESULT_OK = 0
RESULT_BADCOMMAND = 1
RESULT_BADDEV = 2
RESULT_CONNREFUSED = 3


class UsbMuxError(RuntimeError):
    """The daemon rejected a request, or violated the usbmux protocol."""


class UsbMuxResultError(UsbMuxError):
    def __init__(self, number: int):
        self.number = number
        super().__init__(f"usbmuxd returned result {number}")


class _BufferedDuplexStream(StapledByteStream):
    async def send_eof(self):
        # Both halves share one socket. StapledByteStream's default closes the
        # sender entirely, which would discard the outstanding response too.
        await self.send_stream.send_eof()


def _uint(value, maximum=0xFFFFFFFF):
    return type(value) is int and 0 <= value <= maximum


def _result(number):
    return {"MessageType": "Result", "Number": number}


def encode_message(payload: dict, tag: int = 1) -> bytes:
    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)
    if len(data) + HEADER.size > MAX_MESSAGE_SIZE:
        raise UsbMuxError("usbmux message exceeds size limit")
    return HEADER.pack(len(data) + HEADER.size, 1, 8, tag) + data


async def receive_message(reader: BufferedByteReceiveStream) -> tuple[int, dict]:
    """Read one v1 plist frame without consuming following raw stream bytes."""
    length, version, message, tag = HEADER.unpack(await reader.receive_exactly(HEADER.size))
    if length <= HEADER.size or length > MAX_MESSAGE_SIZE:
        raise UsbMuxError("invalid usbmux message length")
    if version != 1 or message != 8:
        raise UsbMuxError("only usbmux protocol v1 plist messages are supported")
    data = await reader.receive_exactly(length - HEADER.size)
    try:
        payload = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError, OverflowError, ExpatError, RecursionError) as exc:
        raise UsbMuxError("invalid usbmux plist") from exc
    if not isinstance(payload, dict):
        raise UsbMuxError("usbmux plist must be a dictionary")
    return tag, payload


def _check_result(payload):
    if payload.get("MessageType") != "Result" or not _uint(payload.get("Number")):
        raise UsbMuxError("invalid usbmux result")
    if payload["Number"] != RESULT_OK:
        raise UsbMuxResultError(payload["Number"])


def _matches(device, udid, connection_type=None):
    if not isinstance(device, dict) or not _uint(device.get("DeviceID")):
        return False
    properties = device.get("Properties")
    if not isinstance(properties, dict) or properties.get("SerialNumber") != udid:
        return False
    # usbmuxd lists a phone once per link; Wi-Fi sync adds a Network entry.
    return connection_type is None or properties.get("ConnectionType") == connection_type


def _memory_pair():
    a_tx, a_rx = create_memory_object_stream[bytes](16)  # ty: ignore[call-non-callable]
    b_tx, b_rx = create_memory_object_stream[bytes](16)  # ty: ignore[call-non-callable]
    return StapledObjectStream(a_tx, b_rx), StapledObjectStream(b_tx, a_rx)


async def _copy(reader, destination, cancel_scope):
    try:
        async for data in reader:
            await destination.send(data)
        if hasattr(destination, "send_eof"):
            await destination.send_eof()
        else:
            await destination.send_stream.aclose()
    except (BrokenResourceError, ClosedResourceError, OSError):
        # A reset must stop the opposite pump, which may still be waiting for EOF.
        cancel_scope.cancel()


async def _relay(client, reader, upstream):
    async with create_task_group() as tasks:
        tasks.start_soon(_copy, reader, upstream, tasks.cancel_scope)
        tasks.start_soon(_copy, upstream, client, tasks.cancel_scope)


@dataclass
class UsbMuxDaemon:
    """Connection settings for an operator-managed local or remote usbmuxd.

    ``query`` is an internal helper, never exported to clients. ``proxy`` applies
    the allowlist on every request, independently of any client-provided fields.
    ``connection_type`` (for example ``"USB"``) restricts every device match to
    one link, so a Wi-Fi copy of the same device is neither visible nor usable.
    """

    path: str = "/var/run/usbmuxd"
    host: str | None = None
    port: int | None = None
    timeout: float = 10.0
    connection_type: str | None = None

    def __post_init__(self):
        if (self.host is None) != (self.port is None):
            raise ValueError("usbmux host and port must be configured together")
        if self.port is not None and (not _uint(self.port, 65535) or self.port == 0):
            raise ValueError("usbmux port must be between 1 and 65535")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            # Keep all invalid configuration values on the same validation API.
            raise ValueError("usbmux timeout must be a positive finite number")  # noqa: TRY004
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("usbmux timeout must be a positive finite number")
        if self.connection_type is not None and (not isinstance(self.connection_type, str) or not self.connection_type):
            raise ValueError("usbmux connection type must be a nonempty string")

    @asynccontextmanager
    async def _open(self):
        with fail_after(self.timeout):
            if self.host is None:
                stream = await connect_unix(self.path)
            else:
                stream = await connect_tcp(self.host, self.port)
        async with stream:
            yield stream

    async def _request(self, stream, reader, payload, tag=1):
        request = {"ClientVersionString": "jumpstarter", "ProgName": "jumpstarter", "kLibUSBMuxVersion": 3}
        request.update(payload)
        with fail_after(self.timeout):
            await stream.send(encode_message(request, tag))
            response_tag, response = await receive_message(reader)
        if response_tag != tag:
            raise UsbMuxError("usbmux response tag does not match request")
        return response

    async def query(self, payload: dict) -> dict:
        async with self._open() as stream:
            return await self._request(stream, BufferedByteReceiveStream(stream), payload)

    async def list_devices(self) -> list[dict]:
        response = await self.query({"MessageType": "ListDevices"})
        devices = response.get("DeviceList")
        if not isinstance(devices, list):
            raise UsbMuxError("usbmuxd did not return a device list")
        if any(not isinstance(item, dict) for item in devices):
            raise UsbMuxError("invalid usbmux device list")
        return devices

    async def _device_id(self, udid, device_id=None):
        devices = [item for item in await self.list_devices() if _matches(item, udid, self.connection_type)]
        if device_id is not None:
            devices = [item for item in devices if item["DeviceID"] == device_id]
        if len(devices) != 1:
            raise UsbMuxResultError(RESULT_BADDEV)
        return devices[0]["DeviceID"]

    @asynccontextmanager
    async def connect_device(self, udid: str, port: int, *, device_id: int | None = None):
        """Open a device TCP port, checking identity before exposing raw bytes.

        Device IDs are transient. Check the live mapping before Connect and again
        after its acknowledgement so re-enumeration during the handshake fails
        closed. No client data is sent until both checks succeed.
        """
        if not _uint(port, 65535) or port == 0:
            raise ValueError("device port must be between 1 and 65535")
        if device_id is not None and not _uint(device_id):
            raise UsbMuxResultError(RESULT_BADDEV)
        current_id = await self._device_id(udid, device_id)
        async with self._open() as stream:
            reader = BufferedByteReceiveStream(stream)
            response = await self._request(
                stream,
                reader,
                {"MessageType": "Connect", "DeviceID": current_id, "PortNumber": socket.htons(port)},
            )
            _check_result(response)
            await self._device_id(udid, current_id)
            yield _BufferedDuplexStream(stream, reader)

    @asynccontextmanager
    async def proxy(self, udid: str):
        """Yield a stream speaking usbmux for exactly ``udid``."""
        if not isinstance(udid, str) or not udid:
            raise ValueError("a nonempty UDID is required")
        client, server = _memory_pair()
        caller_error = None
        async with client, server, create_task_group() as tasks:
            tasks.start_soon(self._serve, server, udid)
            try:
                yield client
            except BaseException as exc:  # noqa: BLE001 - Preserve caller failures, including cancellation.
                # An ExceptionGroup would hide closed-RPC errors from gRPC cleanup.
                caller_error = exc
            finally:
                tasks.cancel_scope.cancel()
        if caller_error is not None:
            raise caller_error

    async def _serve(self, stream, udid):
        reader = BufferedByteReceiveStream(stream)
        try:
            async with stream:
                while True:
                    tag, request = await receive_message(reader)
                    if await self._dispatch(stream, reader, udid, tag, request):
                        return
        except (
            EndOfStream,
            IncompleteRead,
            BrokenResourceError,
            ClosedResourceError,
            UsbMuxError,
            OSError,
            TimeoutError,
        ):
            # Invalid input and daemon failures terminate only this client stream.
            return

    async def _dispatch(self, stream, reader, udid, tag, request):
        message = request.get("MessageType")
        if message == "ListDevices":
            devices = [item for item in await self.list_devices() if _matches(item, udid, self.connection_type)]
            response = {"DeviceList": devices}
        elif message == "ReadPairRecord" and request.get("PairRecordID") == udid:
            response = await self.query({"MessageType": "ReadPairRecord", "PairRecordID": udid})
        elif message == "ReadBUID":
            # Lockdown clients need the daemon's public host identity.
            response = await self.query({"MessageType": "ReadBUID"})
        elif message == "Listen":
            await self._listen(stream, reader, udid, tag)
            return True
        elif message == "Connect":
            return await self._connect_proxy(stream, reader, udid, tag, request)
        else:
            response = _result(RESULT_BADCOMMAND)
        await stream.send(encode_message(response, tag))
        return False

    async def _connect_proxy(self, stream, reader, udid, tag, request):
        device_id, wire_port = request.get("DeviceID"), request.get("PortNumber")
        if not _uint(device_id) or not _uint(wire_port, 65535) or wire_port == 0:
            await stream.send(encode_message(_result(RESULT_BADCOMMAND), tag))
            return False
        try:
            async with self.connect_device(udid, socket.ntohs(wire_port), device_id=device_id) as upstream:
                await stream.send(encode_message(_result(RESULT_OK), tag))
                await _relay(stream, reader, upstream)
                return True
        except UsbMuxResultError as exc:
            await stream.send(encode_message(_result(exc.number), tag))
            return False

    async def _listen(self, stream, reader, udid, tag):
        async with self._open() as upstream:
            upstream_reader = BufferedByteReceiveStream(upstream)
            response = await self._request(upstream, upstream_reader, {"MessageType": "Listen"})
            await stream.send(encode_message(response, tag))
            _check_result(response)
            async with create_task_group() as tasks:
                tasks.start_soon(self._listen_events, upstream_reader, stream, udid, tasks.cancel_scope)
                # Listen is a terminal protocol state. Stop the upstream listener
                # on client EOF (or any unexpected subsequent client request).
                try:
                    await reader.receive(1)
                except (EndOfStream, BrokenResourceError, ClosedResourceError):
                    pass
                finally:
                    tasks.cancel_scope.cancel()

    async def _listen_events(self, reader, stream, udid, cancel_scope):
        visible_ids = set()
        try:
            while True:
                tag, event = await receive_message(reader)
                message, device_id = event.get("MessageType"), event.get("DeviceID")
                if not _uint(device_id):
                    continue
                if message == "Attached":
                    if not _matches(event, udid, self.connection_type):
                        # An ID reused for another device must cease being visible.
                        if device_id in visible_ids:
                            visible_ids.remove(device_id)
                            await stream.send(encode_message({"MessageType": "Detached", "DeviceID": device_id}, tag))
                        continue
                    visible_ids.add(device_id)
                elif message in ("Detached", "Paired") and device_id in visible_ids:
                    if message == "Detached":
                        visible_ids.remove(device_id)
                else:
                    continue
                await stream.send(encode_message(event, tag))
        except (EndOfStream, IncompleteRead, BrokenResourceError, ClosedResourceError, UsbMuxError):
            return
        finally:
            cancel_scope.cancel()
