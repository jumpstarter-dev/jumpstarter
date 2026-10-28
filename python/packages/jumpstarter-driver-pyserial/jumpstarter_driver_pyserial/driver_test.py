import asyncio  # noqa: TID251
import contextlib
import errno
import logging
import os
import time
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
import serial_asyncio
from anyio import create_memory_object_stream
from anyio.abc import ObjectStream
from anyio.streams.memory import MemoryObjectSendStream
from anyio.streams.stapled import StapledObjectStream
from serial import PortNotOpenError, SerialException, SerialTimeoutException

from . import driver as driver_module
from .client import PySerialClient
from .driver import AsyncSerial, PySerial, ThrottledStream
from jumpstarter.common.utils import serve
from jumpstarter.streams.fanout import ExclusiveStream


def test_bare_pyserial():
    with serve(PySerial(url="loop://")) as client, client.stream() as stream:
        stream.send(b"hello")
        assert "hello".startswith(stream.receive().decode("utf-8"))


@pytest.mark.parametrize(
    "error_type", [SerialException, SerialTimeoutException, PortNotOpenError, ConnectionResetError, BrokenPipeError]
)
@pytest.mark.parametrize("cps", [None, 1000])
def test_async_serial_normalizes_write_disconnect(error_type, cps):
    writer = AsyncMock()
    error = error_type()
    writer.send.side_effect = error
    stream = AsyncSerial(reader=AsyncMock(), writer=writer, cps=cps)

    async def scenario():
        with pytest.raises(anyio.BrokenResourceError) as exc_info:
            await stream.send(b"input")
        assert exc_info.value.__cause__ is error
        writer.send.assert_awaited_once_with(b"i" if cps else b"input")

    anyio.run(scenario)


@pytest.mark.parametrize(
    "error",
    [
        OSError(errno.ENOMEM, "writer registration failed"),
        TimeoutError("write timed out"),
        RuntimeError("unexpected failure"),
        ValueError("invalid input"),
        asyncio.CancelledError(),
    ],
)
def test_async_serial_preserves_other_write_errors(error):
    writer = AsyncMock()
    writer.send.side_effect = error
    stream = AsyncSerial(reader=AsyncMock(), writer=writer)

    async def scenario():
        with pytest.raises(type(error)) as exc_info:
            await stream.send(b"input")
        assert exc_info.value is error

    anyio.run(scenario)


def test_async_serial_forwards_successful_write():
    writer = AsyncMock()
    stream = AsyncSerial(reader=AsyncMock(), writer=writer)
    anyio.run(stream.send, b"input")
    writer.send.assert_awaited_once_with(b"input")



def test_second_exclusive_connect_surfaces_console_in_use():
    """A second exclusive stream should fail with a clean 'console in use' error."""
    with (
        serve(PySerial(url="loop://")) as client,
        client.stream() as _held,
        pytest.raises(Exception) as exc_info,
        client.stream() as stream,
    ):
        stream.receive()

    combined = []
    cause = exc_info.value
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        combined.append(str(cause))
        details = getattr(cause, "details", None)
        if callable(details):
            combined.append(str(details()))
        next_cause = cause.__cause__
        if next_cause is None and not getattr(cause, "__suppress_context__", False):
            next_cause = cause.__context__
        cause = next_cause
    text = "\n".join(combined)
    assert "Console in use" in text
    assert "Unexpected <class" not in text



def test_bare_open_pyserial():
    with serve(PySerial(url="loop://")) as client:
        client = cast(PySerialClient, client)
        stream = client.open_stream()
        stream.send(b"hello")
        assert "hello".startswith(stream.receive().decode("utf-8"))
        client.close()


def test_pexpect_open_pyserial_forget_close():
    with serve(PySerial(url="loop://")) as client:
        client = cast(PySerialClient, client)  # this is only necessary for the editor to recognize the client methods
        pexpect = client.open()
        pexpect.sendline("hello")
        assert pexpect.expect("hello") == 0


def test_pexpect_open_pyserial():
    with serve(PySerial(url="loop://")) as client:
        client = cast(PySerialClient, client)
        pexpect = client.open()
        pexpect.sendline("hello")
        assert pexpect.expect("hello") == 0
        client.close()


def test_pexpect_context_pyserial():
    with serve(PySerial(url="loop://")) as client:
        client = cast(PySerialClient, client)
        with client.pexpect() as pexpect:
            pexpect.sendline("hello")
            assert pexpect.expect("hello") == 0


def test_can_open_not_present():
    with serve(PySerial(url="/dev/doesNotExist", check_present=False)):
        # we only verify that the context manager does not raise an exception
        pass


def test_cps_throttling():
    """Test that CPS throttling is configured correctly."""
    cps = 5  # 5 characters per second
    test_data = b"hello"  # 5 characters

    with serve(PySerial(url="loop://", cps=cps)) as client, client.stream() as stream:
        # Just verify that the throttling doesn't break functionality
        # The actual timing test is done at the async level
        stream.send(test_data)

        # Verify data was sent correctly (receive character by character)
        received_data = b""
        for _ in range(len(test_data)):
            received_data += stream.receive()
        assert test_data == received_data


def test_no_cps_throttling():
    """Test that without CPS throttling, transmission is fast."""
    test_data = b"hello"

    with serve(PySerial(url="loop://")) as client, client.stream() as stream:
        start_time = time.perf_counter()
        stream.send(test_data)
        end_time = time.perf_counter()

        elapsed_time = end_time - start_time
        # Without throttling, should be fast; allow headroom for CI noise
        assert elapsed_time < 0.5, f"Expected fast transmission, got {elapsed_time}s"

        received = stream.receive()
        assert test_data.decode("utf-8").startswith(received.decode("utf-8"))


def test_cps_zero_disables_throttling():
    """Test that CPS=0 disables throttling."""
    test_data = b"hello"

    with serve(PySerial(url="loop://", cps=0)) as client, client.stream() as stream:
        start_time = time.perf_counter()
        stream.send(test_data)
        end_time = time.perf_counter()

        elapsed_time = end_time - start_time
        # With CPS=0, should be fast (no throttling) - allow headroom
        assert elapsed_time < 0.5, f"Expected fast transmission with cps=0, got {elapsed_time}s"

        received = stream.receive()
        assert test_data.decode("utf-8").startswith(received.decode("utf-8"))


def test_throttled_stream_async():
    """Test that ThrottledStream works correctly at the async level."""
    import anyio

    async def _test():
        cps = 5  # 5 characters per second
        test_data = b"hello world!"  # 12 characters
        expected_min_time = (len(test_data) - 1) / cps  # Should take at least 11/5 = 2.2 seconds

        # Create a memory stream for testing
        tx, rx = create_memory_object_stream[bytes](32)
        stapled_stream = StapledObjectStream(tx, rx)
        # Wrap it with throttling and ensure proper closure
        async with ThrottledStream(stream=stapled_stream, cps=cps) as throttled_stream:
            start_time = time.perf_counter()
            await throttled_stream.send(test_data)
            end_time = time.perf_counter()

            elapsed_time = end_time - start_time
            # Allow some overhead for CI environments but not excessive delay
            expected_max_time = expected_min_time * 1.5  # 50% overhead for CI slowness
            assert expected_min_time <= elapsed_time <= expected_max_time, (
                f"Expected {expected_min_time}s-{expected_max_time}s, got {elapsed_time}s"
            )

            # Verify data was sent correctly (character by character)
            received_data = b""
            for _ in range(len(test_data)):
                received_data += await throttled_stream.receive()
            assert test_data == received_data

    anyio.run(_test)


def test_cps_with_pexpect():
    """Test that CPS throttling works with pexpect interface."""
    cps = 10  # 10 characters per second

    with serve(PySerial(url="loop://", cps=cps)) as client:
        client = cast(PySerialClient, client)
        with client.pexpect() as pexpect:
            # Just verify that pexpect works with throttling enabled
            pexpect.sendline("test")
            assert pexpect.expect("test") == 0
            # We don't test timing here since pexpect has complex buffering


def test_disable_hupcl_applies_termios_flags(monkeypatch):
    calls = {}

    class FakeSerial:
        @staticmethod
        def fileno():
            return 42

    def fake_tcgetattr(fd):
        calls["fd_get"] = fd
        return [0, 0, 0x4000 | 0x0008, 0, 0, 0, []]

    def fake_tcsetattr(fd, when, attrs):
        calls["fd_set"] = fd
        calls["when"] = when
        calls["attrs"] = attrs

    monkeypatch.setattr(driver_module.os, "name", "posix")

    monkeypatch.setattr(
        driver_module,
        "termios",
        SimpleNamespace(HUPCL=0x4000, TCSANOW=0, tcgetattr=fake_tcgetattr, tcsetattr=fake_tcsetattr),
    )

    driver = PySerial(url="/dev/ttyUSB0", check_present=False, disable_hupcl=True)
    driver._maybe_disable_hupcl(FakeSerial())

    assert calls["fd_get"] == 42
    assert calls["fd_set"] == 42
    assert calls["when"] == 0
    assert calls["attrs"][2] & 0x4000 == 0


def test_disable_hupcl_noop_when_disabled(monkeypatch):
    called = {"tcgetattr": False}

    def fake_tcgetattr(_fd):
        called["tcgetattr"] = True
        return [0, 0, 0x4000, 0, 0, 0, []]

    monkeypatch.setattr(
        driver_module,
        "termios",
        SimpleNamespace(HUPCL=0x4000, TCSANOW=0, tcgetattr=fake_tcgetattr, tcsetattr=lambda *_: None),
    )

    driver = PySerial(url="/dev/ttyUSB0", check_present=False, disable_hupcl=False)
    driver._maybe_disable_hupcl(None)

    assert called["tcgetattr"] is False


def test_close_noop_when_no_stream():
    """close() should be safe to call when no stream is active."""
    with serve(PySerial(url="loop://")) as client:
        client.call("close")


@contextlib.contextmanager
def _pty_serial(monkeypatch):
    """Open the driver's serial port on a pseudo-terminal; yields its device end and a transport spy."""
    device, port = os.openpty()
    opened = SimpleNamespace(transport=None, close=None)

    async def open_pty(**kwargs):
        reader, writer = await serial_asyncio.open_serial_connection(url=os.ttyname(port))
        opened.transport = writer.transport
        opened.close = MagicMock(wraps=writer.transport.close)
        writer.transport.close = opened.close
        return reader, writer

    monkeypatch.setattr(driver_module, "open_serial_connection", open_pty)
    try:
        yield device, opened
    finally:
        os.close(device)
        os.close(port)


def test_last_detach_closes_transport(monkeypatch):
    """The default lazy reader releases the transport after the last detach."""

    async def _run():
        with _pty_serial(monkeypatch) as (device, opened):
            driver = PySerial(url="/dev/ttyMOCK", check_present=False)

            async with driver._get_fanout().running():
                async with driver.connect() as stream:
                    assert driver._transport is opened.transport
                    os.write(device, b"test-data")
                    with anyio.fail_after(5):
                        assert await stream.receive() == b"test-data"

                assert driver._transport is None
                opened.close.assert_called_once_with()

    anyio.run(_run)


def test_shutdown_releases_port_but_close_keeps_it(monkeypatch):
    """With always_on, close() kicks clients and shutdown() releases the port.

    This checks the configured always-on behavior; with the default lazy
    reader, detaching the last client closes the port instead.
    """

    async def _run():
        with _pty_serial(monkeypatch) as (device, opened):
            driver = PySerial(url="/dev/ttyMOCK", check_present=False, always_on=True)

            async with driver._get_fanout().running():
                async with driver.connect() as stream:
                    os.write(device, b"hello")
                    with anyio.fail_after(5):
                        assert await stream.receive() == b"hello"
                    # With always_on, a kick does not drop the physical port.
                    driver.close()
                    opened.close.assert_not_called()
                    # shutdown() is session-end teardown and releases the port.
                    driver.shutdown()

                opened.close.assert_called()

            status = driver._get_fanout().status()
            assert status["total_clients"] == 0
            assert not status["write_token_held"]
            assert driver._get_fanout()._reader is None

    anyio.run(_run)


class VanishingDevice:
    """Loopback serial source that disappears and returns, like a power-cycled USB serial."""

    def __init__(self):
        self.present = True
        self._tx: MemoryObjectSendStream[bytes] | None = None

    @contextlib.asynccontextmanager
    async def open(self) -> AsyncGenerator[ObjectStream[bytes]]:
        if not self.present:
            raise OSError("device not present")
        tx, rx = create_memory_object_stream[bytes](32)
        self._tx = tx
        async with StapledObjectStream(tx, rx) as stream:
            yield stream

    def unplug(self):
        assert self._tx is not None, "unplug() before the device was opened"
        self.present = False
        self._tx.close()  # reader sees EOF; reopen attempts fail until present again


async def _read_until(stream, marker: bytes, timeout: float = 5) -> bytes:
    seen = b""
    with anyio.fail_after(timeout):
        while marker not in seen:
            seen += await stream.receive()
    return seen


@pytest.fixture
def processed_input(monkeypatch):
    """Observe exporter-side processing without adding acknowledgements to the wire."""
    events = {}
    original_send = ExclusiveStream.send

    async def send(stream, data):
        await original_send(stream, data)
        if event := events.get(data):
            event.set()

    def track(data):
        events[data] = anyio.Event()
        return events[data]

    monkeypatch.setattr(ExclusiveStream, "send", send)
    return track


@pytest.mark.parametrize(
    "type_while_gone",
    [False, True],
    ids=["idle", "typing"],
)
def test_console_stream_survives_device_power_cycle(type_while_gone, processed_input):
    """An attached console stays connected while the device vanishes and returns."""
    device = VanishingDevice()
    driver = PySerial(url="loop://")
    driver._get_fanout()._source_factory = device.open

    async def scenario(client):
        async with client.stream_async(method="connect") as stream:
            await stream.send(b"before")
            await _read_until(stream, b"before")
            device.unplug()
            await _read_until(stream, b"[disconnected")
            if type_while_gone:
                processed = processed_input(b"x")
                await stream.send(b"x")
                # Wait for exporter-side processing before restoring the device.
                with anyio.fail_after(5):
                    await processed.wait()
            device.present = True
            await _read_until(stream, b"[reconnected]")
            await stream.send(b"after")
            await _read_until(stream, b"after")

    with serve(driver) as client:
        client.portal.call(scenario, client)


@pytest.mark.parametrize("error_type", [anyio.BrokenResourceError, anyio.ClosedResourceError])
@pytest.mark.parametrize("always_on", [False, True])
def test_write_failure_before_reader_disconnect_preserves_console(
    monkeypatch, processed_input, caplog, error_type, always_on
):
    caplog.set_level(logging.WARNING, logger="jumpstarter.streams.fanout")
    device = VanishingDevice()
    driver = PySerial(url="loop://", always_on=always_on)
    fanout = driver._get_fanout()

    @contextlib.asynccontextmanager
    async def factory():
        async with device.open() as source:
            original_send = source.send

            async def send(data):
                if data == b"racing input":
                    assert fanout._source is source
                    device.unplug()
                    raise error_type("source disconnected during write")
                await original_send(data)

            monkeypatch.setattr(source, "send", send)
            yield source

    fanout._source_factory = factory

    async def scenario(client):
        with anyio.fail_after(5):
            async with client.stream_async("connect") as stream:
                rpc = stream.stream.context
                holder = fanout._write_token_holder
                await stream.send(b"before")
                await _read_until(stream, b"before")

                processed = processed_input(b"racing input")
                await stream.send(b"racing input")
                await processed.wait()
                await _read_until(stream, b"[disconnected")
                assert not rpc.done()
                assert fanout._write_token_holder == holder
                assert sum(
                    record.getMessage() == "Discarding input until source reconnects"
                    for record in caplog.records
                ) == 1

                device.present = True
                output = await _read_until(stream, b"[reconnected]")
                assert b"racing input" not in output
                await stream.send(b"after")
                assert await _read_until(stream, b"after") == b"after"
                assert not rpc.done()
                assert fanout._write_token_holder == holder

    with serve(driver) as client:
        client.portal.call(scenario, client)


@pytest.mark.parametrize("always_on", [False, True])
def test_offline_input_preserves_console_rpc_and_observer(always_on, processed_input, caplog):
    """An outage discards input, not the logical RPC, token, or observer."""
    caplog.set_level(logging.WARNING, logger="jumpstarter.streams.fanout")
    device = VanishingDevice()
    driver = PySerial(url="loop://", always_on=always_on)
    fanout = driver._get_fanout()
    fanout._source_factory = device.open

    async def scenario(client):
        with anyio.fail_after(5):
            async with (
                client.stream_async("connect") as stream,
                client.stream_async("observe") as observer,
            ):
                rpc = stream.stream.context
                holder = fanout._write_token_holder
                reader = fanout._reader
                for outage in range(2):
                    await stream.send(b"before outage")
                    await _read_until(stream, b"before outage")
                    await _read_until(observer, b"before outage")
                    device.unplug()
                    await _read_until(stream, b"[disconnected")
                    assert fanout._source is None

                    processed = processed_input(b"offline keystrokes")
                    await stream.send(b"offline keystrokes")
                    # Wait for exporter-side processing before restoring the device.
                    await processed.wait()
                    assert sum(
                        record.getMessage() == "Discarding input until source reconnects"
                        for record in caplog.records
                    ) == outage + 1
                    assert not rpc.done()
                    assert fanout._write_token_holder == holder
                    assert fanout._reader is reader

                    device.present = True
                    output = await _read_until(stream, b"[reconnected]")
                    assert b"offline keystrokes" not in output
                    assert b"input discarded" not in output
                    observed = await _read_until(observer, b"[reconnected]")
                    assert b"input discarded" not in observed
                    await stream.send(b"after outage")
                    assert await _read_until(stream, b"after outage") == b"after outage"
                    assert await _read_until(observer, b"after outage") == b"after outage"
                    assert not rpc.done()
                    assert fanout._write_token_holder == holder

    with serve(driver) as client:
        client.portal.call(scenario, client)


@pytest.mark.parametrize("always_on", [False, True])
@pytest.mark.parametrize("termination", ["close", "shutdown", "revoke"])
def test_offline_console_still_terminates(always_on, termination, processed_input):
    device = VanishingDevice()
    driver = PySerial(url="loop://", always_on=always_on)
    driver._get_fanout()._source_factory = device.open

    async def scenario(client):
        with anyio.fail_after(5):
            async with client.stream_async("connect") as stream:
                device.unplug()
                await _read_until(stream, b"[disconnected")
                processed = processed_input(b"offline input")
                await stream.send(b"offline input")
                await processed.wait()

                if termination == "close":
                    await client.call_async("close")
                elif termination == "shutdown":
                    driver.shutdown()
                else:
                    await client.call_async("release_console")
                    await _read_until(stream, b"[write token revoked]")
                    await stream.send(b"revoked input")

                with pytest.raises((anyio.EndOfStream, anyio.BrokenResourceError)):
                    # Retry notifications may already be queued; only a stream
                    # error counts as termination, not an in-band message.
                    while True:
                        await stream.receive()

    with serve(driver) as client:
        client.portal.call(scenario, client)
