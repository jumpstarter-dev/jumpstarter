import time
from typing import cast

from anyio import create_memory_object_stream
from anyio.streams.stapled import StapledObjectStream

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
