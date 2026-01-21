from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional, Union

from anyio import (
    create_memory_object_stream,
    sleep,
)
from anyio._backends._asyncio import StreamReaderWrapper, StreamWriterWrapper
from anyio.abc import ObjectSendStream, ObjectStream
from anyio.streams.stapled import StapledObjectStream
from serial import serial_for_url
from serial_asyncio import open_serial_connection

from jumpstarter.driver import Driver, exportstream

LOOP = "loop://"


@dataclass(kw_only=True)
class ThrottledStream(ObjectStream):
    """Wrapper stream that adds CPS throttling to any ObjectStream."""
    stream: Union[ObjectSendStream[bytes], ObjectStream[bytes]]
    cps: Optional[float] = None

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
    writer: Union[StreamWriterWrapper, ThrottledStream]
    cps: Optional[float] = None  # characters per second throttling

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
class PySerial(Driver):
    url: str
    baudrate: int = field(default=115200)
    check_present: bool = field(default=True)
    cps: Optional[float] = field(default=None)  # characters per second throttling

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if self.check_present and self.url != LOOP:
            serial_for_url(self.url, baudrate=self.baudrate)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_pyserial.client.PySerialClient"

    @exportstream
    @asynccontextmanager
    async def connect(self):
        cps_info = f", cps: {self.cps}" if self.cps is not None else ""
        self.logger.info("Connecting to %s, baudrate: %d%s", self.url, self.baudrate, cps_info)
        if self.url != LOOP:
            reader, writer = await open_serial_connection(url=self.url, baudrate=self.baudrate, limit=1)
            writer.transport.set_write_buffer_limits(high=4096, low=0)
            async with AsyncSerial(
                reader=StreamReaderWrapper(reader),
                writer=StreamWriterWrapper(writer),
                cps=self.cps,
            ) as stream:
                yield stream
            self.logger.info("Disconnected from %s", self.url)
        else:
            tx, rx = create_memory_object_stream[bytes](32)  # type: ignore[call-overload]
            stapled_stream = StapledObjectStream(tx, rx)
            async with ThrottledStream(stream=stapled_stream, cps=self.cps) as stream:
                yield stream
