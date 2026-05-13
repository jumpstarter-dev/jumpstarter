import time
from types import SimpleNamespace
from typing import cast

import anyio
from anyio import create_memory_object_stream
from anyio.streams.stapled import StapledObjectStream

from . import driver as driver_module
from .client import PySerialClient
from .driver import PySerial, ThrottledStream
from jumpstarter.common.utils import serve


def test_bare_pyserial():
    with serve(PySerial(url="loop://")) as client:
        with client.stream() as stream:
            stream.send(b"hello")
            assert "hello".startswith(stream.receive().decode("utf-8"))


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

    with serve(PySerial(url="loop://", cps=cps)) as client:
        with client.stream() as stream:
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

    with serve(PySerial(url="loop://")) as client:  # No CPS specified
        with client.stream() as stream:
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

    with serve(PySerial(url="loop://", cps=0)) as client:
        with client.stream() as stream:
            start_time = time.perf_counter()
            stream.send(test_data)
            end_time = time.perf_counter()

            elapsed_time = end_time - start_time
            # With CPS=0, should be fast (no throttling) – allow headroom
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
        tx, rx = create_memory_object_stream[bytes](32)  # ty: ignore[call-non-callable]
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


def test_close_closes_transport(monkeypatch):
    """close() should close the underlying transport.

    These tests intentionally use asyncio.StreamReader/StreamWriter because the
    serial_asyncio library is built on asyncio transports and protocols. This is
    a known exception to the anyio migration -- serial_asyncio has no anyio
    equivalent, so the bridge layer between serial I/O and anyio streams relies
    on asyncio internals.
    """
    import asyncio
    from unittest.mock import MagicMock

    async def _run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"test-data")

        protocol = asyncio.StreamReaderProtocol(reader)
        transport = MagicMock()
        transport.serial = None
        transport.is_closing.return_value = False
        transport.get_write_buffer_size.return_value = 0
        orig_close = transport.close

        def fake_close():
            orig_close()
            try:
                protocol.connection_lost(None)
            except Exception:
                pass

        transport.close = fake_close

        loop = asyncio.get_running_loop()
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)

        async def fake_open(**kw):
            return reader, writer

        monkeypatch.setattr(driver_module, "open_serial_connection", fake_open)

        driver = PySerial(url="/dev/ttyMOCK", check_present=False)

        async with driver.connect() as stream:
            assert driver._transport is transport
            data = await stream.receive()
            assert data == b"test-data"

        assert driver._transport is None

    anyio.run(_run)


def test_close_from_outside_releases_port(monkeypatch):
    """close() closes the transport, causing the stream to tear down."""
    import asyncio
    from unittest.mock import MagicMock

    async def _run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"hello")

        protocol = asyncio.StreamReaderProtocol(reader)
        transport = MagicMock()
        mock_serial = MagicMock()
        transport.serial = mock_serial
        transport.is_closing.return_value = False
        transport.get_write_buffer_size.return_value = 0

        closed = {"called": False}
        orig_close = transport.close

        def fake_close():
            closed["called"] = True
            orig_close()
            try:
                protocol.connection_lost(None)
            except Exception:
                pass

        transport.close = fake_close

        loop = asyncio.get_running_loop()
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)

        async def fake_open(**kw):
            return reader, writer

        monkeypatch.setattr(driver_module, "open_serial_connection", fake_open)

        driver = PySerial(url="/dev/ttyMOCK", check_present=False)

        try:
            async with driver.connect() as stream:
                data = await stream.receive()
                assert data == b"hello"
                driver.close()
        except Exception:
            pass

        assert closed["called"]

    anyio.run(_run)
