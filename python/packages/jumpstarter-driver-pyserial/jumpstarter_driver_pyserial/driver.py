import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from inspect import iscoroutinefunction
from typing import Any

from anyio import (
    create_memory_object_stream,
    sleep,
    to_thread,
)
from anyio._backends._asyncio import StreamReaderWrapper, StreamWriterWrapper
from anyio.abc import ObjectSendStream, ObjectStream
from anyio.streams.stapled import StapledObjectStream
from serial import serial_for_url
from serial_asyncio import open_serial_connection

from jumpstarter.driver import Driver, export
from jumpstarter.streams.fanout import FanOutStreamMixin

try:
    import termios
except ImportError:  # pragma: no cover - non-POSIX platforms
    termios = None  # ty: ignore[invalid-assignment]

LOOP = "loop://"


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
            return await self.stream.receive()  # type: ignore[no-any-return]
        raise RuntimeError("receive() called on send-only ThrottledStream")

    async def send_eof(self):
        if hasattr(self.stream, "send_eof"):
            await self.stream.send_eof()

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
            self.writer = ThrottledStream(stream=self.writer, cps=self.cps)

    async def send(self, item: bytes):
        await self.writer.send(item)

    async def receive(self):
        return await self.reader.receive()

    async def send_eof(self):
        if hasattr(self.writer, "send_eof"):
            await self.writer.send_eof()

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
    power_control_ref: str = field(
        default="power",
        metadata={"description": "Name of the power driver sibling for the Ctrl-] hotkey"},
    )
    power_control_method: list[str] | str | None = field(
        default_factory=lambda: ["off", "sleep:2", "on"],
        metadata={
            "description": "Power cycle method sequence for the Ctrl-] hotkey, "
            "executed on the power_control_ref driver. "
            "Supports method names (cycle, reset, on, off) and sleep:N. "
            "Set to [] or null to disable hotkey. Default: ['off', 'sleep:2', 'on']"
        },
    )
    always_on: bool = field(default=False)
    _transport: Any = field(default=None, init=False, repr=False)
    _parent: Driver | None = field(default=None, init=False, repr=False)

    def _get_fanout_always_on(self) -> bool:
        return self.always_on

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if self.check_present and self.url != LOOP:
            serial_for_url(self.url, baudrate=self.baudrate)

        # Normalize power_control_method: None stays None, string becomes list
        if isinstance(self.power_control_method, str):
            object.__setattr__(self, "power_control_method", [self.power_control_method])

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_pyserial.client.PySerialClient"

    def enumerate(self, *, root=None, parent=None, name=None):
        """Capture the tree parent so the power hotkey can resolve sibling drivers."""
        self._parent = parent
        return super().enumerate(root=root, parent=parent, name=name)

    def _power_plan(self):
        """Resolve the power driver and validate the method sequence.

        Returns (power, steps) with steps as ("sleep", seconds) / ("call",
        method name) tuples, or None (with a warning for misconfigurations)
        when the hotkey is unavailable.
        """
        if not self.power_control_method:
            return None  # disabled via config; not a warning-worthy condition

        ref = self.power_control_ref
        power = None
        if self._parent is not None:
            power = self._parent.children.get(ref)
        if power is None:
            # Standalone serial device (no parent): fall back to own children
            power = self.children.get(ref)
        if power is None:
            self.logger.warning(
                "power_control_ref '%s' matches no sibling or child of the serial device — power cycle hotkey disabled",
                ref,
            )
            return None

        steps = []
        for method in self.power_control_method:
            if method.startswith("sleep:"):
                try:
                    steps.append(("sleep", float(method.split(":", 1)[1])))
                except ValueError:
                    self.logger.warning("Invalid sleep step '%s' — power cycle hotkey disabled", method)
                    return None
            elif callable(getattr(power, method, None)):
                steps.append(("call", method))
            else:
                self.logger.warning(
                    "Power driver '%s' has no callable method '%s' — power cycle hotkey disabled", ref, method
                )
                return None
        return power, steps

    @export
    async def power_control_config(self) -> dict:
        """Power-cycle hotkey availability for the console client.

        Validates the configured method sequence against the power driver
        (duck-typed on the real driver object) so the client can decide
        whether to arm the Ctrl-] hotkey.
        """
        if not self.power_control_method:
            return {"available": False, "reason": "power control disabled"}
        if self._power_plan() is None:
            return {"available": False, "reason": "power driver or methods unavailable"}
        return {"available": True}

    @export
    async def dut_power_cycle(self):
        """Power-cycle the DUT attached to this console.

        Executes the configured power_control_method sequence on the
        power_control_ref driver. Triggered by the console Ctrl-] x3 hotkey
        and callable remotely like any other driver method.
        """
        plan = self._power_plan()
        if plan is None:
            raise RuntimeError("power cycle unavailable: power control disabled or misconfigured")
        power, steps = plan
        for kind, value in steps:
            if kind == "sleep":
                await sleep(value)
            else:
                method = getattr(power, value)
                # Match DriverCall dispatch: await coroutines, run sync methods
                # in a worker thread so blocking PDUs don't stall the exporter.
                if iscoroutinefunction(method):
                    await method()
                else:
                    await to_thread.run_sync(method)

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

    @asynccontextmanager
    async def _open_source(self):
        cps_info = f", cps: {self.cps}" if self.cps is not None else ""
        self.logger.info("Connecting to %s, baudrate: %d%s", self.url, self.baudrate, cps_info)

        if self.url == LOOP:
            tx, rx = create_memory_object_stream[bytes](32)  # type: ignore[call-overload]
            stapled_stream = StapledObjectStream(tx, rx)
            async with ThrottledStream(stream=stapled_stream, cps=self.cps) as stream:
                yield stream
            return

        reader, writer = await open_serial_connection(url=self.url, baudrate=self.baudrate)
        writer.transport.set_write_buffer_limits(high=4096, low=0)
        self._maybe_disable_hupcl(getattr(writer.transport, "serial", None))
        self._transport = writer.transport

        try:
            async with AsyncSerial(
                reader=StreamReaderWrapper(reader),
                writer=StreamWriterWrapper(writer),
                cps=self.cps,
            ) as stream:
                yield stream
        finally:
            self._transport = None
        self.logger.info("Disconnected from %s", self.url)

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

        Runs on the exporter's event-loop thread from Session teardown. Close
        the transport explicitly first rather than relying on the reader's
        cleanup, which shutdown_sync() cancels but cannot await from this
        synchronous path. pyserial-asyncio still releases the handle on a later
        loop iteration (after flushing any buffered writes), not before this
        returns.
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
