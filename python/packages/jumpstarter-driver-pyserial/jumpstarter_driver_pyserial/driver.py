import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from anyio import (
    BrokenResourceError,
    Event,
    create_memory_object_stream,
    move_on_after,
    sleep,
)

# pyserial-asyncio returns asyncio streams, and anyio adapts those only in its private backend.
from anyio._backends._asyncio import StreamReaderWrapper, StreamWriterWrapper  # noqa: TID251
from anyio.abc import ObjectSendStream, ObjectStream
from anyio.streams.stapled import StapledObjectStream
from serial import SerialException, serial_for_url
from serial_asyncio import open_serial_connection

from jumpstarter.driver import Driver, export
from jumpstarter.streams.fanout import FanOutStreamMixin

try:
    import termios
except ImportError:  # pragma: no cover - non-POSIX platforms
    termios = None  # type: ignore[assignment]

LOOP = "loop://"
# How long release() waits for the serial fd to close before giving up.
_CLOSE_TIMEOUT = 5.0


@dataclass(kw_only=True)
class ThrottledStream(ObjectStream):
    """Wrapper stream that adds CPS throttling to any ObjectStream."""
    stream: ObjectSendStream[bytes] | ObjectStream[bytes]
    cps: float | None = None

    async def send(self, item: bytes):
        if self.cps is not None and self.cps > 0:
            await self._send_throttled(item)
        else:
            await self.stream.send(item)

    async def _send_throttled(self, item: bytes):
        """Send data with throttling based on characters per second."""
        if not item:
            return

        assert self.cps is not None
        delay_per_char = 1.0 / self.cps

        # Send data character by character with delay
        for i in range(len(item)):
            char = item[i:i+1]
            await self.stream.send(char)

            # Add delay between characters (except for the last one)
            if i < len(item) - 1:
                await sleep(delay_per_char)

    async def receive(self):
        if hasattr(self.stream, "receive"):
            return await self.stream.receive()  # ty: ignore[not-callable]
        raise RuntimeError("receive() called on send-only ThrottledStream")

    async def send_eof(self):
        if hasattr(self.stream, "send_eof"):
            await self.stream.send_eof()  # ty: ignore[not-callable]

    async def aclose(self):
        await self.stream.aclose()


@dataclass(kw_only=True)
class AsyncSerial(ObjectStream):
    reader: StreamReaderWrapper
    writer: StreamWriterWrapper | ThrottledStream
    cps: float | None = None  # characters per second throttling

    def __post_init__(self):
        # Replace writer with throttled version if chars-per-second throttling is set
        if self.cps is not None and self.cps > 0:
            self.writer = ThrottledStream(stream=self.writer, cps=self.cps)  # ty: ignore[invalid-argument-type]

    async def send(self, item: bytes):
        try:
            await self.writer.send(item)
        except (SerialException, ConnectionError) as exc:
            raise BrokenResourceError from exc

    async def receive(self):
        return await self.reader.receive()

    async def send_eof(self):
        if hasattr(self.writer, "send_eof"):
            await self.writer.send_eof()  # ty: ignore[not-callable]

    async def aclose(self):
        try:
            await self.writer.aclose()
        finally:
            await self.reader.aclose()


@dataclass(kw_only=True)
class PySerial(FanOutStreamMixin, Driver):
    driver_type = "serial"

    url: str
    baudrate: int = field(default=115200)
    check_present: bool = field(default=True)
    cps: float | None = field(default=None)  # characters per second throttling
    disable_hupcl: bool = field(default=False)
    always_on: bool = field(default=False)
    _transport: Any = field(default=None, init=False, repr=False)
    # release()/acquire() let an exporter-side process (typically a flasher
    # driver) take exclusive access to the tty while clients stay attached.
    _released: bool = field(default=False, init=False, repr=False)
    _acquire_waiters: list[Event] = field(default_factory=list, init=False, repr=False)
    # Async callable closing the currently-open source, if any; returns once
    # the underlying fd is really closed (transport for a real port, memory
    # streams for loop://).
    _source_closer: Any = field(default=None, init=False, repr=False)

    def _get_fanout_always_on(self) -> bool:
        return self.always_on

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if self.check_present and self.url != LOOP:
            serial_for_url(self.url, baudrate=self.baudrate)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_pyserial.client.PySerialClient"

    def _maybe_disable_hupcl(self, serial_port: Any):
        """Disable HUPCL to avoid MCU reset on serial port close when supported."""
        if not self.disable_hupcl or os.name != "posix" or termios is None:
            return

        if serial_port is None:
            self.logger.warning("disable_hupcl is enabled but serial transport has no serial handle")
            return

        try:
            fd = serial_port.fileno()
            attrs = termios.tcgetattr(fd)
            attrs[2] &= ~termios.HUPCL
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            self.logger.info("Disabled HUPCL on %s", self.url)
        except (AttributeError, OSError, TypeError):
            self.logger.warning("Failed to disable HUPCL on %s", self.url)

    async def _wait_acquired(self):
        """Park until acquire() is called if the port is currently released."""
        while self._released:
            evt = Event()
            self._acquire_waiters.append(evt)
            try:
                await evt.wait()
            finally:
                # Drop the event if cancelled while parked (session teardown)
                # so waiters don't accumulate across release cycles.
                if evt in self._acquire_waiters:
                    self._acquire_waiters.remove(evt)

    @asynccontextmanager
    async def _open_source(self):
        # The fan-out reader loop reopens the source after release() closes it;
        # hold it here so the tty stays free until acquire().
        await self._wait_acquired()

        cps_info = f", cps: {self.cps}" if self.cps is not None else ""
        self.logger.info("Connecting to %s, baudrate: %d%s", self.url, self.baudrate, cps_info)

        if self.url == LOOP:
            tx, rx = create_memory_object_stream[bytes](32)  # type: ignore[call-overload]
            stapled_stream = StapledObjectStream(tx, rx)

            async def close_loop():
                tx.close()
                rx.close()

            self._source_closer = close_loop
            try:
                async with ThrottledStream(stream=stapled_stream, cps=self.cps) as stream:
                    yield stream
            finally:
                self._source_closer = None
            return

        while True:
            reader, writer = await open_serial_connection(url=self.url, baudrate=self.baudrate)
            if not self._released:
                break
            # release() ran while the port was opening: hand it back right away.
            await self._close_writer(writer)
            await self._wait_acquired()
        writer.transport.set_write_buffer_limits(high=4096, low=0)
        self._maybe_disable_hupcl(getattr(writer.transport, "serial", None))
        self._transport = writer.transport

        async def close_serial():
            await self._close_writer(writer)

        self._source_closer = close_serial

        try:
            async with AsyncSerial(
                reader=StreamReaderWrapper(reader),
                writer=StreamWriterWrapper(writer),
                cps=self.cps,
            ) as stream:
                yield stream
        finally:
            self._transport = None
            self._source_closer = None
        self.logger.info("Disconnected from %s", self.url)

    async def _close_writer(self, writer):
        """Close a serial connection and wait until its fd is really closed.

        transport.close() only schedules the close: pyserial-asyncio closes the
        fd in a later loop callback, right after connection_lost(), which is
        what wait_closed() waits for. Returning earlier lets another process
        (e.g. esptool) open the tty while we still read from it.
        """
        writer.close()
        with move_on_after(_CLOSE_TIMEOUT) as scope:
            try:
                await writer.wait_closed()
            except Exception:
                # The close itself failed (e.g. device unplugged): the fd is gone.
                self.logger.debug("wait_closed failed for %s", self.url, exc_info=True)
        if scope.cancelled_caught:
            self.logger.warning("Timed out waiting for %s to close", self.url)

    @export
    async def release(self):
        """Close the underlying tty so another process on the exporter
        (typically a flasher driver) can open it.

        Attached clients (connect/observe) stay attached: the fan-out reader
        loop sees the source drop and parks until acquire() is called, so
        clients see a pause rather than EOF. Input sent meanwhile is discarded.
        """
        if self._released:
            return
        self._released = True
        self.logger.info("Releasing %s", self.url)
        closer = self._source_closer
        if closer is not None:
            try:
                await closer()
            except Exception:
                self.logger.warning("release: closing source failed", exc_info=True)

    @export
    async def acquire(self):
        """Reopen the tty previously released with release()."""
        if not self._released:
            return
        self._released = False
        self.logger.info("Acquiring %s", self.url)
        waiters, self._acquire_waiters = self._acquire_waiters, []
        for w in waiters:
            w.set()

    @export
    def close(self):
        """Boot every attached console client and release the write token.

        Client-callable recovery hook: use it to reclaim a console wedged or
        abandoned by another client. If this was the last client and
        ``always_on`` is false, the reader stops and the serial port closes.
        Closing a port can toggle DTR and reset some boards unless
        ``disable_hupcl`` is set. Full teardown happens at session end via
        shutdown().
        """
        # FanOutStreamMixin.close() kicks clients + releases the token, then
        # chains to Driver.close() for any children.
        super().close()

    def shutdown(self):
        """Session-end teardown: drop the serial link and stop the reader.

        Runs on the exporter's event-loop thread from Session teardown. In a
        session the reader has already closed the transport when the session
        stopped serving; outside one, shutdown_sync() can only cancel the reader,
        not await it, so close the transport explicitly first. pyserial-asyncio
        still releases the handle on a later loop iteration (after flushing any
        buffered writes), not before this returns.
        """
        transport = self._transport
        if transport is None:
            self.logger.debug("shutdown() called but no active connection")
        else:
            self.logger.debug("shutdown() closing transport for %s", self.url)
            transport.close()
        # FanOutStreamMixin.shutdown() tears down the reader, then Driver.shutdown()
        # recurses to children.
        super().shutdown()

    @export
    def set_dtr(self, value: bool):
        """Set the DTR control signal."""
        s = serial_for_url(self.url, baudrate=self.baudrate)
        try:
            s.dtr = value
        finally:
            s.close()

    @export
    def set_rts(self, value: bool):
        """Set the RTS control signal."""
        s = serial_for_url(self.url, baudrate=self.baudrate)
        try:
            s.rts = value
        finally:
            s.close()
