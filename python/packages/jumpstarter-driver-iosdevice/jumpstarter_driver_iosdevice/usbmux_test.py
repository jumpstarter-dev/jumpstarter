import asyncio
import socket
import struct
from contextlib import asynccontextmanager
from dataclasses import replace

import anyio.lowlevel
import pytest
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    Event,
    IncompleteRead,
    create_task_group,
    create_tcp_listener,
    fail_after,
)
from anyio.abc import SocketAttribute
from anyio.streams.buffered import BufferedByteReceiveStream

from .usbmux import (
    HEADER,
    MAX_MESSAGE_SIZE,
    UsbMuxDaemon,
    UsbMuxError,
    UsbMuxResultError,
    encode_message,
    receive_message,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def attached(device_id, udid):
    return {
        "MessageType": "Attached",
        "DeviceID": device_id,
        "Properties": {"DeviceID": device_id, "SerialNumber": udid, "ConnectionType": "USB"},
    }


def wifi(device_id, udid):
    event = attached(device_id, udid)
    event["Properties"]["ConnectionType"] = "Network"
    return event


class MockMux:
    """Two-device daemon with recorded requests and injectable failures."""

    def __init__(self):
        self.devices = [attached(7, "leased-udid"), attached(8, "other-udid")]
        self.requests = []
        self.raw_writes = []
        self.events = []
        self.connect_result = 0
        self.replace_during_connect = False
        self.bad_tag = False
        self.open_connections = 0
        self.stall = False
        self.reset_after_connect = None

    async def handle(self, stream):
        self.open_connections += 1
        try:
            async with stream:
                await self._handle(stream)
        except (EndOfStream, IncompleteRead, BrokenResourceError, ClosedResourceError):
            pass
        finally:
            self.open_connections -= 1

    async def _handle(self, stream):
        reader = BufferedByteReceiveStream(stream)
        while True:
            tag, request = await receive_message(reader)
            self.requests.append(request)
            if self.stall:
                await reader.receive(1)
                return
            message = request["MessageType"]
            if message == "ListDevices":
                response = {"DeviceList": self.devices}
            elif message == "ReadPairRecord":
                response = {"PairRecordData": request["PairRecordID"].encode() + b"-pair-record"}
            elif message == "ReadBUID":
                response = {"BUID": "exporter-host-id"}
            elif message == "Listen":
                frames = encode_message({"MessageType": "Result", "Number": 0}, tag)
                frames += b"".join(encode_message(event, 0) for event in self.events)
                await stream.send(frames)
                await reader.receive(1)
                return
            elif message == "Connect":
                if await self._connect(stream, reader, tag):
                    return
                continue
            else:
                raise AssertionError(f"unsafe request reached the daemon: {message}")
            await stream.send(encode_message(response, tag + int(self.bad_tag)))

    async def _connect(self, stream, reader, tag):
        if self.replace_during_connect:
            self.devices = [attached(7, "other-udid")]
        data = encode_message({"MessageType": "Result", "Number": self.connect_result}, tag)
        if self.connect_result:
            await stream.send(data)
            return False
        # A daemon can coalesce the Connect acknowledgement and first device
        # bytes in one packet. The proxy must retain the raw bytes.
        await stream.send(data + b"device-banner")
        if self.reset_after_connect is not None:
            await self.reset_after_connect.wait()
            stream.extra(SocketAttribute.raw_socket).setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            await stream.aclose()
            return True
        async for data in reader:
            self.raw_writes.append(data)
            await stream.send(data)
        await stream.send_eof()
        return True


@asynccontextmanager
async def running_mux():
    mock = MockMux()
    listener = await create_tcp_listener(local_host="127.0.0.1")
    address = listener.extra(SocketAttribute.local_address)
    daemon = UsbMuxDaemon(host=address[0], port=address[1], timeout=1)
    async with listener, create_task_group() as tasks:
        tasks.start_soon(listener.serve, mock.handle)
        try:
            yield daemon, mock
        finally:
            tasks.cancel_scope.cancel()


async def exchange(stream, reader, request, tag=42):
    await stream.send(encode_message(request, tag))
    with fail_after(2):
        response_tag, response = await receive_message(reader)
    assert response_tag == tag
    return response


async def test_lists_only_leased_device_and_preserves_tags():
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        reader = BufferedByteReceiveStream(stream)
        result = await exchange(stream, reader, {"MessageType": "ListDevices"}, 1234)
        assert result == {"DeviceList": [attached(7, "leased-udid")]}
        mock.devices = [attached(8, "other-udid")]
        assert await exchange(stream, reader, {"MessageType": "ListDevices"}) == {"DeviceList": []}


@pytest.mark.parametrize(
    "payload",
    [
        {"MessageType": "SavePairRecord", "PairRecordID": "leased-udid", "PairRecordData": b"bad"},
        {"MessageType": "DeletePairRecord", "PairRecordID": "leased-udid"},
        {"MessageType": "ReadPairRecord", "PairRecordID": "other-udid"},
        {"MessageType": "ReadPairRecord", "PairRecordID": "../leased-udid"},
        {"MessageType": "ReadPairRecord"},
        {"MessageType": "ListListeners"},
        {"MessageType": "UnrecognizedFutureRequest"},
        {"MessageType": ["ListDevices"]},
        {},
    ],
)
async def test_disallowed_requests_never_reach_shared_daemon(payload):
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        response = await exchange(stream, BufferedByteReceiveStream(stream), payload)
        assert response == {"MessageType": "Result", "Number": 1}
        assert mock.requests == []


async def test_pair_read_and_buid_sanitize_extra_client_fields():
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        reader = BufferedByteReceiveStream(stream)
        response = await exchange(
            stream,
            reader,
            {
                "MessageType": "ReadPairRecord",
                "PairRecordID": "leased-udid",
                "DeviceID": 8,
                "PairRecordData": b"injected",
                "UntrustedExtension": "other-udid",
            },
        )
        assert response == {"PairRecordData": b"leased-udid-pair-record"}
        assert "DeviceID" not in mock.requests[-1]
        assert "PairRecordData" not in mock.requests[-1]
        assert "UntrustedExtension" not in mock.requests[-1]
        assert await exchange(stream, reader, {"MessageType": "ReadBUID"}) == {"BUID": "exporter-host-id"}


async def test_connect_refuses_other_device_and_revalidates_stale_ids():
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        reader = BufferedByteReceiveStream(stream)
        response = await exchange(
            stream,
            reader,
            {
                "MessageType": "Connect",
                "DeviceID": 8,
                "PortNumber": socket.htons(62078),
            },
        )
        assert response == {"MessageType": "Result", "Number": 2}
        assert all(request["MessageType"] == "ListDevices" for request in mock.requests)
        await exchange(stream, reader, {"MessageType": "ListDevices"})
        mock.devices = [attached(7, "other-udid"), attached(9, "leased-udid")]
        response = await exchange(
            stream,
            reader,
            {
                "MessageType": "Connect",
                "DeviceID": 7,
                "PortNumber": socket.htons(62078),
            },
        )
        assert response["Number"] == 2
        assert not any(request["MessageType"] == "Connect" for request in mock.requests)


@pytest.mark.parametrize(("device_id", "port"), [(True, 1), ("7", 1), (7, True), (7, 65536), (7, -1), (7, 0)])
async def test_connect_rejects_invalid_numeric_fields(device_id, port):
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        response = await exchange(
            stream,
            BufferedByteReceiveStream(stream),
            {
                "MessageType": "Connect",
                "DeviceID": device_id,
                "PortNumber": port,
            },
        )
        assert response["Number"] == 1
        assert mock.requests == []


async def test_connect_becomes_raw_and_preserves_pipelined_bytes_both_directions():
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        reader = BufferedByteReceiveStream(stream)
        request = {"MessageType": "Connect", "DeviceID": 7, "PortNumber": socket.htons(62078)}
        await stream.send(encode_message(request, 321) + b"client-pipeline\x00\xff")
        with fail_after(2):
            assert await receive_message(reader) == (321, {"MessageType": "Result", "Number": 0})
            assert await reader.receive_exactly(13) == b"device-banner"
            assert await reader.receive_exactly(17) == b"client-pipeline\x00\xff"
            await stream.send(b"subsequent raw bytes")
            assert await reader.receive_exactly(20) == b"subsequent raw bytes"
        assert b"".join(mock.raw_writes) == b"client-pipeline\x00\xffsubsequent raw bytes"
        connect = next(item for item in mock.requests if item["MessageType"] == "Connect")
        assert connect["PortNumber"] == socket.htons(62078)


async def test_device_id_reused_during_connect_exposes_no_raw_bytes():
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        mock.replace_during_connect = True
        reader = BufferedByteReceiveStream(stream)
        await stream.send(
            encode_message(
                {
                    "MessageType": "Connect",
                    "DeviceID": 7,
                    "PortNumber": socket.htons(62078),
                },
                5,
            )
            + encode_message({"MessageType": "ListDevices"}, 6)
        )
        with fail_after(2):
            assert await receive_message(reader) == (5, {"MessageType": "Result", "Number": 2})
            assert await receive_message(reader) == (6, {"DeviceList": []})
        assert mock.raw_writes == []


async def test_failed_connect_stays_in_plist_mode():
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        mock.connect_result = 3
        reader = BufferedByteReceiveStream(stream)
        response = await exchange(
            stream,
            reader,
            {
                "MessageType": "Connect",
                "DeviceID": 7,
                "PortNumber": socket.htons(8100),
            },
        )
        assert response["Number"] == 3
        assert await exchange(stream, reader, {"MessageType": "ListDevices"}) == {
            "DeviceList": [attached(7, "leased-udid")],
        }


async def test_listen_filters_initial_events_detach_pair_and_reused_ids():
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        mock.events = [
            attached(8, "other-udid"),
            {"MessageType": "Paired", "DeviceID": 8},
            attached(7, "leased-udid"),
            {"MessageType": "Detached", "DeviceID": 8},
            {"MessageType": "Paired", "DeviceID": 7},
            attached(7, "other-udid"),
            {"MessageType": "Paired", "DeviceID": 7},
            {"MessageType": "Detached", "DeviceID": 7},
            attached(9, "leased-udid"),
            {"MessageType": "UnknownNotification", "DeviceID": 9},
            {"MessageType": "Detached", "DeviceID": 9},
        ]
        reader = BufferedByteReceiveStream(stream)
        assert await exchange(stream, reader, {"MessageType": "Listen"}) == {"MessageType": "Result", "Number": 0}
        expected = [
            attached(7, "leased-udid"),
            {"MessageType": "Paired", "DeviceID": 7},
            {"MessageType": "Detached", "DeviceID": 7},
            attached(9, "leased-udid"),
            {"MessageType": "Detached", "DeviceID": 9},
        ]
        with fail_after(2):
            for event in expected:
                assert await receive_message(reader) == (0, event)


async def test_fragmented_and_coalesced_frames():
    async with running_mux() as (daemon, _), daemon.proxy("leased-udid") as stream:
        reader = BufferedByteReceiveStream(stream)
        frame = encode_message({"MessageType": "ListDevices"}, 99)
        for chunk in (frame[:1], frame[1:7], frame[7:16], frame[16:30], frame[30:]):
            await stream.send(chunk)
        await stream.send(encode_message({"MessageType": "ReadBUID"}, 100))
        with fail_after(2):
            assert await receive_message(reader) == (99, {"DeviceList": [attached(7, "leased-udid")]})
            assert await receive_message(reader) == (100, {"BUID": "exporter-host-id"})


@pytest.mark.parametrize(
    "frame",
    [
        HEADER.pack(15, 1, 8, 1),
        HEADER.pack(16, 1, 8, 1),
        HEADER.pack(MAX_MESSAGE_SIZE + 1, 1, 8, 1),
        HEADER.pack(17, 0, 8, 1) + b"x",
        HEADER.pack(17, 1, 2, 1) + b"x",
        HEADER.pack(17, 1, 8, 1) + b"x",
        HEADER.pack(24, 1, 8, 1) + b"<array/>",
    ],
)
async def test_invalid_protocol_frames_close_without_contacting_daemon(frame):
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        await stream.send(frame)
        with fail_after(2), pytest.raises(EndOfStream):
            await stream.receive()
        assert mock.requests == []


async def test_daemon_response_tag_must_match():
    async with running_mux() as (daemon, mock):
        mock.bad_tag = True
        with pytest.raises(UsbMuxError, match="tag"):
            await daemon.list_devices()


async def test_connect_device_helper_checks_identity_and_preserves_initial_bytes():
    async with running_mux() as (daemon, mock):
        async with daemon.connect_device("leased-udid", 8100) as stream:
            with fail_after(2):
                assert await stream.receive() == b"device-banner"
                await stream.send(b"GET /status")
                assert await stream.receive() == b"GET /status"
        assert next(item for item in mock.requests if item["MessageType"] == "Connect")["PortNumber"] == socket.htons(
            8100
        )
        with pytest.raises(UsbMuxResultError):
            async with daemon.connect_device("leased-udid", 8100, device_id=8):
                pytest.fail("wrong device accepted")


async def test_usb_selection_ignores_wifi_copy_of_leased_device():
    async with running_mux() as (daemon, mock):
        mock.devices = [wifi(9, "leased-udid"), attached(7, "leased-udid"), attached(8, "other-udid")]
        # Without a link restriction, two entries for one device stay ambiguous.
        with pytest.raises(UsbMuxResultError):
            async with daemon.connect_device("leased-udid", 8100):
                raise AssertionError("ambiguous device accepted")
        usb = replace(daemon, connection_type="USB")
        async with usb.connect_device("leased-udid", 8100) as stream:
            with fail_after(2):
                assert await stream.receive() == b"device-banner"
        async with usb.proxy("leased-udid") as stream:
            reader = BufferedByteReceiveStream(stream)
            listed = await exchange(stream, reader, {"MessageType": "ListDevices"})
            assert listed == {"DeviceList": [attached(7, "leased-udid")]}
            response = await exchange(
                stream, reader, {"MessageType": "Connect", "DeviceID": 9, "PortNumber": socket.htons(62078)}
            )
            assert response == {"MessageType": "Result", "Number": 2}


async def test_usb_listen_hides_wifi_copy_of_leased_device():
    async with running_mux() as (daemon, mock), replace(daemon, connection_type="USB").proxy("leased-udid") as stream:
        mock.events = [
            wifi(9, "leased-udid"),
            attached(7, "leased-udid"),
            {"MessageType": "Paired", "DeviceID": 9},
            {"MessageType": "Detached", "DeviceID": 9},
            {"MessageType": "Detached", "DeviceID": 7},
        ]
        reader = BufferedByteReceiveStream(stream)
        assert await exchange(stream, reader, {"MessageType": "Listen"}) == {"MessageType": "Result", "Number": 0}
        with fail_after(2):
            for event in [attached(7, "leased-udid"), {"MessageType": "Detached", "DeviceID": 7}]:
                assert await receive_message(reader) == (0, event)


@pytest.mark.parametrize("message", ["Listen", "Connect"])
async def test_cancelling_proxy_closes_daemon_connection(message):
    async with running_mux() as (daemon, mock):
        async with daemon.proxy("leased-udid") as stream:
            request = {"MessageType": message, "DeviceID": 7, "PortNumber": socket.htons(8100)}
            assert (await exchange(stream, BufferedByteReceiveStream(stream), request))["Number"] == 0
            assert mock.open_connections >= 1
        with fail_after(2):
            while mock.open_connections:
                await anyio.lowlevel.checkpoint()


async def test_connect_forwards_half_close_and_finishes_response():
    async with running_mux() as (daemon, _), daemon.proxy("leased-udid") as stream:
        reader = BufferedByteReceiveStream(stream)
        request = {"MessageType": "Connect", "DeviceID": 7, "PortNumber": socket.htons(8100)}
        assert (await exchange(stream, reader, request))["Number"] == 0
        await stream.send(b"last request")
        await stream.send_stream.aclose()
        with fail_after(2):
            assert await reader.receive_exactly(25) == b"device-bannerlast request"
            with pytest.raises(EndOfStream):
                await reader.receive()


async def test_query_timeout_is_bounded_and_closes_daemon_connection():
    async with running_mux() as (daemon, mock):
        mock.stall = True
        daemon.timeout = 0.01
        with fail_after(2), pytest.raises(TimeoutError):
            await daemon.list_devices()
        with fail_after(2):
            while mock.open_connections:
                await anyio.lowlevel.checkpoint()


async def test_device_connection_reset_closes_client_instead_of_hanging():
    async with running_mux() as (daemon, mock), daemon.proxy("leased-udid") as stream:
        mock.reset_after_connect = Event()
        reader = BufferedByteReceiveStream(stream)
        request = {"MessageType": "Connect", "DeviceID": 7, "PortNumber": socket.htons(8100)}
        assert (await exchange(stream, reader, request))["Number"] == 0
        assert await reader.receive_exactly(13) == b"device-banner"
        mock.reset_after_connect.set()
        with fail_after(2), pytest.raises(EndOfStream):
            await reader.receive()


@pytest.mark.parametrize("failure", [ValueError("caller failed"), asyncio.CancelledError("caller cancelled")])
async def test_proxy_preserves_caller_exception_without_task_group_wrapping(failure):
    daemon = UsbMuxDaemon()
    with pytest.raises(type(failure)) as caught:
        async with daemon.proxy("leased-udid"):
            raise failure
    assert caught.value is failure


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"timeout": True},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"host": "localhost"},
        {"port": 123},
        {"host": "localhost", "port": True},
        {"host": "localhost", "port": 0},
        {"host": "localhost", "port": 65536},
        {"connection_type": ""},
        {"connection_type": 1},
    ],
)
def test_invalid_daemon_config(kwargs):
    with pytest.raises(ValueError):
        UsbMuxDaemon(**kwargs)
