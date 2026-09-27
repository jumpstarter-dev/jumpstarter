import socket
import ssl
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from anyio import (
    TASK_STATUS_IGNORED,
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    IncompleteRead,
    create_task_group,
    create_tcp_listener,
    fail_after,
    sleep_forever,
)
from anyio.abc import SocketAttribute
from anyio.lowlevel import checkpoint
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.tls import TLSStream

from .driver import IosDevice
from .https import HttpsIdentity
from .trust import ExporterTrustBroker
from .usbmux import UsbMuxError, encode_message, receive_message
from jumpstarter.common.exceptions import ConfigurationError

UDID = "test-https-device"
DEVICE_ID = 7
HTTP_PORT = 8100


@pytest.fixture
def anyio_backend():
    return "asyncio"


class HttpDevice:
    def __init__(self):
        self.device = {
            "DeviceID": DEVICE_ID,
            "Properties": {"SerialNumber": UDID, "ConnectionType": "USB"},
        }
        self.watchers = []
        self.requests = []
        self.connected_ports = []
        self.connect_result = 0
        self.received = bytearray()

    async def handle(self, stream):
        try:
            async with stream:
                reader = BufferedByteReceiveStream(stream)
                while True:
                    tag, request = await receive_message(reader)
                    self.requests.append(request)
                    name = request["MessageType"]
                    if name == "ListDevices":
                        await stream.send(encode_message({"DeviceList": [self.device]}, tag))
                    elif name == "Listen":
                        await stream.send(encode_message({"MessageType": "Result", "Number": 0}, tag))
                        self.watchers.append(stream)
                        await stream.send(encode_message({"MessageType": "Attached", **self.device}, 0))
                        try:
                            await stream.receive()
                        finally:
                            self.watchers.remove(stream)
                        return
                    elif name == "Connect":
                        assert request["DeviceID"] == self.device["DeviceID"]
                        port = socket.ntohs(request["PortNumber"])
                        self.connected_ports.append(port)
                        assert port == HTTP_PORT
                        await stream.send(encode_message({"MessageType": "Result", "Number": self.connect_result}, tag))
                        if self.connect_result:
                            return
                        async for data in reader:
                            self.received.extend(data)
                            await stream.send(data)
                        return
                    else:
                        raise AssertionError(f"unexpected USB request: {name}")
        except (EndOfStream, IncompleteRead, BrokenResourceError, ClosedResourceError):
            pass

    async def detach(self):
        for watcher in list(self.watchers):
            await watcher.send(encode_message({"MessageType": "Detached", "DeviceID": DEVICE_ID}, 0))


@asynccontextmanager
async def running_https():
    peer = HttpDevice()
    listener = await create_tcp_listener(local_host="127.0.0.1")
    host, port = listener.extra(SocketAttribute.local_address)
    driver = IosDevice(
        transport="network", udid=UDID, usbmux_host=host, usbmux_port=port, http_port=HTTP_PORT, connect_timeout=2
    )
    driver._describe = AsyncMock(return_value={"udid": UDID, "model": "iPadTest", "os_version": "26.2"})
    async with listener, create_task_group() as tasks:
        tasks.start_soon(listener.serve, peer.handle)
        try:
            yield driver, peer
        finally:
            if driver._trust_broker is not None:
                await driver._trust_broker.aclose()
            driver.close()
            tasks.cancel_scope.cancel()


@asynccontextmanager
async def remote_stream(driver):
    # The broker's cancellation scope belongs to the server task, as it does
    # under gRPC. The client task must observe EOF rather than be cancelled.
    async def serve(*, task_status=TASK_STATUS_IGNORED):
        async with driver.connect_https() as stream:
            task_status.started(stream)
            await sleep_forever()

    async with create_task_group() as tasks:
        stream = await tasks.start(serve)
        try:
            yield stream
        finally:
            tasks.cancel_scope.cancel()


def client_context(certificate):
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=certificate)
    return context


async def secure(stream, certificate):
    return await TLSStream.wrap(
        stream,
        server_side=False,
        ssl_context=client_context(certificate),
        hostname="127.0.0.1",
        standard_compatible=False,
    )


@pytest.mark.parametrize("port", [False, 0, -1, 65536, 62078, "8100", 8100.0])
def test_invalid_http_port(port):
    with pytest.raises(ConfigurationError, match="http_port"):
        IosDevice(udid=UDID, http_port=port)


@pytest.mark.parametrize("options", [{"trust_mode": "passthrough"}, {"forward_ports": [8100]}])
def test_https_requires_exporter_trust_and_cannot_be_forwarded_raw(options):
    with pytest.raises(ConfigurationError, match="http_port"):
        IosDevice(udid=UDID, http_port=8100, **options)


@pytest.mark.parametrize("ports", [[False], [0], [62078], [65536]])
def test_broker_rejects_invalid_https_port(ports):
    with pytest.raises(ValueError, match="HTTPS"):
        ExporterTrustBroker(udid=UDID, https_ports=ports)


def test_broker_rejects_raw_and_https_overlap():
    with pytest.raises(ValueError, match="raw"):
        ExporterTrustBroker(udid=UDID, https_ports=[8100], allowed_ports=[8100])


@pytest.mark.anyio
async def test_https_disabled_and_unprepared_streams_are_rejected():
    driver = IosDevice(udid=UDID)
    driver._describe = AsyncMock(return_value={"udid": UDID, "model": "TestPhone", "os_version": "26.2"})
    assert (await driver.info())["https_services"] == []
    with pytest.raises(RuntimeError, match="not configured"):
        await driver.https_info()
    with pytest.raises(RuntimeError, match="not configured"):
        async with driver.connect_https():
            pytest.fail("disabled stream yielded")
    async with running_https() as (driver, peer):
        with pytest.raises(RuntimeError, match="information"):
            async with driver.connect_https():
                pytest.fail("unprepared stream yielded")
        assert not peer.requests


@pytest.mark.anyio
async def test_public_https_metadata_is_stable_and_opens_no_raw_forward_or_pair_record():
    async with running_https() as (driver, peer):
        info = await driver.https_info()
        assert info == await driver.https_info()
        assert set(info) == {"ca_certificate", "metadata"}
        assert "PRIVATE" not in info["ca_certificate"]
        assert info["metadata"] == {
            "device": {"udid": UDID, "platform": "iOS", "version": "26.2"},
        }
        assert (await driver.info())["https_services"] == ["https"]
        assert not driver.children
        assert driver._trust_broker._identity is None
        assert peer.watchers and not peer.connected_ports
        assert {r["MessageType"] for r in peer.requests} == {"ListDevices", "Listen"}


@pytest.mark.anyio
async def test_https_real_tls_carries_large_payload_without_exporter_listener():
    async with running_https() as (driver, peer):
        info = await driver.https_info()
        async with driver.connect_https() as raw, await secure(raw, info["ca_certificate"]) as stream:
            payload = bytes(range(256)) * 2048
            async with create_task_group() as tasks:
                tasks.start_soon(stream.send, payload)
                with fail_after(3):
                    assert await BufferedByteReceiveStream(stream).receive_exactly(len(payload)) == payload
        assert peer.connected_ports == [HTTP_PORT]
        assert peer.received == payload


@pytest.mark.anyio
@pytest.mark.parametrize("authority", ["default", "other-generation"])
async def test_untrusted_tls_never_opens_usb(authority):
    async with running_https() as (driver, peer):
        await driver.https_info()
        context = ssl.create_default_context()
        if authority == "other-generation":
            context.load_verify_locations(cadata=HttpsIdentity.generate().certificate)
        async with driver.connect_https() as raw:
            with pytest.raises(ssl.SSLCertVerificationError):
                await TLSStream.wrap(
                    raw, server_side=False, ssl_context=context, hostname="127.0.0.1", standard_compatible=False
                )
        assert not peer.connected_ports


@pytest.mark.anyio
async def test_plain_http_never_opens_usb():
    async with running_https() as (driver, peer):
        await driver.https_info()
        async with driver.connect_https() as raw:
            await raw.send(b"GET /status HTTP/1.1\r\nHost: localhost\r\n\r\n")
            with fail_after(2), pytest.raises((EndOfStream, BrokenResourceError, ClosedResourceError)):
                await raw.receive()
        assert not peer.connected_ports


@pytest.mark.anyio
async def test_https_only_port_cannot_bypass_via_raw_usbmux_connect():
    async with running_https() as (driver, peer):
        await driver.https_info()
        broker = driver._trust_broker
        assert broker.https_ports == {HTTP_PORT}
        assert not broker.allowed_ports
        async with broker.proxy(UDID) as stream:
            await stream.send(
                encode_message({"MessageType": "Connect", "DeviceID": DEVICE_ID, "PortNumber": socket.htons(HTTP_PORT)})
            )
            _, response = await receive_message(BufferedByteReceiveStream(stream))
            assert response["Number"] != 0
        with pytest.raises(UsbMuxError, match="raw"):
            async with broker.open_forward(HTTP_PORT):
                pytest.fail("HTTPS port exposed raw")
        assert not peer.connected_ports


@pytest.mark.anyio
async def test_detach_between_metadata_and_connect_requires_fresh_info_and_ca():
    async with running_https() as (driver, peer):
        first = await driver.https_info()
        broker = driver._trust_broker
        await peer.detach()
        with fail_after(2):
            while broker.is_active:
                await checkpoint()
        assert broker._https_identity is None
        with pytest.raises(RuntimeError, match="information"):
            async with driver.connect_https():
                pytest.fail("detached generation yielded")
        # Same numeric DeviceID and UDID deliberately reappear after detach.
        second = await driver.https_info()
        assert second["ca_certificate"] != first["ca_certificate"]
        assert driver._trust_broker is not broker
        async with driver.connect_https() as raw:
            with pytest.raises(ssl.SSLCertVerificationError):
                await secure(raw, first["ca_certificate"])
        assert not peer.connected_ports


@pytest.mark.anyio
async def test_device_swap_before_connect_revokes_existing_identity():
    async with running_https() as (driver, peer):
        await driver.https_info()
        broker = driver._trust_broker
        peer.device["DeviceID"] += 1
        with pytest.raises(RuntimeError, match="generation changed"):
            async with driver.connect_https():
                pytest.fail("replaced device yielded")
        assert not broker.is_active and broker._https_identity is None
        assert not peer.connected_ports


@pytest.mark.anyio
@pytest.mark.parametrize("revoke", ["detach", "reset", "close"])
async def test_revocation_closes_active_tls_and_blocks_following_connections(revoke):
    async with running_https() as (driver, peer):
        info = await driver.https_info()
        broker = driver._trust_broker
        async with remote_stream(driver) as raw, await secure(raw, info["ca_certificate"]) as stream:
            await stream.send(b"before-revocation")
            assert await stream.receive() == b"before-revocation"
            if revoke == "detach":
                await peer.detach()
            else:
                getattr(driver, revoke)()
            with fail_after(2), pytest.raises((EndOfStream, BrokenResourceError, ClosedResourceError)):
                await stream.receive()
        assert not broker.is_active and broker._https_identity is None
        assert peer.received == b"before-revocation"
        with pytest.raises(RuntimeError, match="information"):
            async with driver.connect_https():
                pytest.fail("revoked stream yielded")


@pytest.mark.anyio
async def test_https_not_yet_listening_closes_tls_without_exception_group():
    async with running_https() as (driver, peer):
        info = await driver.https_info()
        peer.connect_result = 3
        async with driver.connect_https() as raw, await secure(raw, info["ca_certificate"]) as stream:
            with fail_after(2), pytest.raises((EndOfStream, BrokenResourceError, ClosedResourceError)):
                await stream.receive()
        assert peer.connected_ports == [HTTP_PORT]
        assert not peer.received
        assert driver._trust_broker.is_active
        peer.connect_result = 0
        async with driver.connect_https() as raw, await secure(raw, info["ca_certificate"]) as stream:
            await stream.send(b"ready")
            assert await stream.receive() == b"ready"
