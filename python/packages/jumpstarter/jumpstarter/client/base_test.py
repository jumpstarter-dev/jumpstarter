"""Tests for StubDriverClient."""

import logging
import signal
import time
from contextlib import ExitStack
from itertools import islice
from unittest.mock import MagicMock, create_autospec
from uuid import uuid4

import anyio
import anyio.lowlevel
import pytest
from anyio.from_thread import BlockingPortal

from .base import DriverClient, StubDriverClient, resolve_root_child
from .core import DriverError
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export


class MissingClientDriver(Driver):
    """Test driver that returns a non-existent client class path."""

    @classmethod
    def client(cls) -> str:
        return "nonexistent_driver_package.client.NonExistentClient"


def create_stub_client(class_path: str) -> StubDriverClient:
    """Create a StubDriverClient with minimal mocking for testing."""
    return StubDriverClient(
        uuid=uuid4(),
        labels={"jumpstarter.dev/client": class_path},
        stub=MagicMock(),
        portal=create_autospec(BlockingPortal, instance=True),
        stack=ExitStack(),
    )


def test_missing_driver_logs_warning_and_creates_stub(caplog):
    """Test that a missing driver logs a warning and creates a StubDriverClient."""
    expected_class_path = "nonexistent_driver_package.client.NonExistentClient"
    with caplog.at_level(logging.WARNING), serve(MissingClientDriver()) as client:
        # Should have logged a warning with the exact class path from MissingDriverError
        assert f"Driver client '{expected_class_path}' is not available" in caplog.text
        # ...and say why, so the warning is actionable on its own
        assert "is not installed" in caplog.text

        # Should have created a StubDriverClient
        assert isinstance(client, StubDriverClient)

        # Using the stub should raise an error
        with pytest.raises(ImportError):
            client.call("some_method")


def test_stub_driver_client_streamingcall_raises():
    """Test that streamingcall() raises ImportError with driver info."""
    stub = create_stub_client("missing_driver.client.Client")
    with pytest.raises(ImportError) as exc_info:
        # Need to consume the generator to trigger the error
        list(stub.streamingcall("some_method"))
    assert "missing_driver" in str(exc_info.value)


def test_stub_driver_client_stream_raises():
    """Test that stream() raises ImportError with driver info."""
    stub = create_stub_client("missing_driver.client.Client")
    with pytest.raises(ImportError) as exc_info, stub.stream():
        pass
    assert "missing_driver" in str(exc_info.value)


def test_stub_driver_client_log_stream_raises():
    """Test that log_stream() raises ImportError with driver info."""
    stub = create_stub_client("missing_driver.client.Client")
    with pytest.raises(ImportError) as exc_info, stub.log_stream():
        pass
    assert "missing_driver" in str(exc_info.value)


def test_stub_driver_client_error_message_jumpstarter_driver():
    """Test that error message mentions version mismatch for Jumpstarter drivers."""
    stub = create_stub_client("jumpstarter_driver_xyz.client.XyzClient")
    with pytest.raises(ImportError) as exc_info:
        stub.call("some_method")
    assert "version mismatch" in str(exc_info.value)


def test_stub_driver_client_error_message_third_party():
    """Test that error message includes install instructions for third-party drivers."""
    stub = create_stub_client("custom_driver.client.CustomClient")
    with pytest.raises(ImportError) as exc_info:
        stub.call("some_method")
    assert "pip install custom_driver" in str(exc_info.value)


def test_stub_driver_client_arbitrary_method_raises():
    """Test that accessing arbitrary methods like .on() raises ImportError, not AttributeError."""
    stub = create_stub_client("jumpstarter_driver_power.client.PowerClient")
    # Accessing .on() should raise ImportError with helpful message, not AttributeError
    with pytest.raises(ImportError) as exc_info:
        stub.on()
    assert "jumpstarter_driver_power" in str(exc_info.value)
    assert "version mismatch" in str(exc_info.value)


def test_stub_reports_the_supplied_reason():
    """A stub built with a reason raises that reason, not the generic text."""
    stub = StubDriverClient(
        uuid=uuid4(),
        labels={"jumpstarter.dev/client": "some_package.client.Client"},
        stub=MagicMock(),
        portal=create_autospec(BlockingPortal, instance=True),
        stack=ExitStack(),
        missing_reason="Driver 'some_package.client.Client' is not allowed by the client configuration.",
    )
    with pytest.raises(ImportError) as exc_info:
        stub.call("anything")
    assert "not allowed by the client configuration" in str(exc_info.value)
    # The installation advice would be actively misleading here.
    assert "is not installed" not in str(exc_info.value)


def test_stub_without_reason_keeps_the_install_hint():
    """Without a reason the stub still explains the driver is not installed."""
    stub = create_stub_client("missing_driver.client.Client")
    with pytest.raises(ImportError) as exc_info:
        stub.call("anything")
    assert "is not installed" in str(exc_info.value)



def _bare_client(name=None, children=None):
    labels = {"jumpstarter.dev/name": name} if name else {}
    return DriverClient(
        uuid=uuid4(),
        labels=labels,
        stub=MagicMock(),
        portal=create_autospec(BlockingPortal, instance=True),
        stack=ExitStack(),
        children=children or {},
    )


def test_resolve_root_child():
    power = _bare_client("power")
    composite = _bare_client(children={"power": power})
    assert resolve_root_child(composite, "power") is power

    named = _bare_client("dut")
    assert resolve_root_child(named, "dut") is named

    bare = _bare_client()
    assert resolve_root_child(bare, "anything") is bare

    with pytest.raises(KeyError):
        resolve_root_child(composite, "missing")


class EndlessDriver(Driver):
    """Streams forever and records when the stream is cancelled."""

    cancelled: bool = False

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @export
    async def ticks(self):
        try:
            n = 0
            while True:
                yield n
                n += 1
                await anyio.lowlevel.checkpoint()
        finally:
            type(self).cancelled = True


def test_streamingcall_closed_early_cancels_driver_stream():
    EndlessDriver.cancelled = False
    with serve(EndlessDriver()) as client:
        assert list(islice(client.streamingcall("ticks"), 3)) == [0, 1, 2]
        for _ in range(200):
            if EndlessDriver.cancelled:
                break
            time.sleep(0.01)
        # checked while still served: the session teardown would cancel it anyway
        assert EndlessDriver.cancelled


class StalledDriver(Driver):
    """Streams a few items, then raises or stalls, recording when the stream ends."""

    ended: bool = False

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @export
    async def items(self, count):
        try:
            # numbers cross the wire as protobuf doubles
            for n in range(int(count)):
                yield n
        finally:
            type(self).ended = True

    @export
    async def failing(self):
        yield 0
        raise ValueError("sensor unplugged")

    @export
    async def stall(self):
        try:
            await anyio.sleep_forever()
            yield None
        finally:
            type(self).ended = True


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_streamingcall_runs_to_completion():
    StalledDriver.ended = False
    with serve(StalledDriver()) as client:
        assert list(client.streamingcall("items", 3)) == [0, 1, 2]
        assert StalledDriver.ended


def test_streamingcall_propagates_driver_errors():
    with serve(StalledDriver()) as client:
        stream = client.streamingcall("failing")
        assert next(stream) == 0
        with pytest.raises(DriverError, match="sensor unplugged"):
            next(stream)


class _Interrupted(Exception):
    pass


def _interrupt(signum, frame):
    raise _Interrupted


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="needs POSIX interval timers")
def test_streamingcall_interrupted_while_waiting_cancels_driver_stream():
    # Robot Framework timeouts raise from a signal handler in the waiting thread
    StalledDriver.ended = False
    previous = signal.signal(signal.SIGALRM, _interrupt)
    try:
        with serve(StalledDriver()) as client:
            signal.setitimer(signal.ITIMER_REAL, 0.2)
            with pytest.raises(_Interrupted):
                next(client.streamingcall("stall"))
            # checked while still served: the session teardown would cancel it anyway
            assert _wait_for(lambda: StalledDriver.ended)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_streamingcall_closed_after_portal_stops():
    with serve(StalledDriver()) as client:
        stream = client.streamingcall("items", 3)
        assert next(stream) == 0
    # the stopped portal took the stream down with it; closing must not raise
    stream.close()


@pytest.mark.parametrize(
    "error, swallowed",
    [(RuntimeError("This portal is not running"), True), (RuntimeError("something else broke"), False)],
)
def test_streamingcall_close_only_tolerates_a_stopped_portal(error, swallowed):
    client = _bare_client()
    finished = MagicMock()
    finished.result.side_effect = StopAsyncIteration
    client.portal.start_task_soon.return_value = finished
    # the first call starts the stream, the second closes it
    client.portal.call.side_effect = [MagicMock(), error]

    stream = client.streamingcall("ticks")
    if swallowed:
        assert list(stream) == []
    else:
        with pytest.raises(RuntimeError, match="something else broke"):
            list(stream)
