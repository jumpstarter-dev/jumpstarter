from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from anyio import (
    create_memory_object_stream,
)
from anyio._backends._asyncio import StreamReaderWrapper, StreamWriterWrapper
from anyio.abc import ObjectStream
from anyio.streams.stapled import StapledObjectStream
from serial import serial_for_url
from serial_asyncio import open_serial_connection

from jumpstarter.driver import Driver, exportstream

LOOP = "loop://"


@dataclass(kw_only=True)
class AsyncSerial(ObjectStream):
    reader: StreamReaderWrapper
    writer: StreamWriterWrapper

    async def send(self, item):
        await self.writer.send(item)

    async def receive(self):
        return await self.reader.receive()

    async def send_eof(self):
        pass

    async def aclose(self):
        await self.writer.aclose()
        await self.reader.aclose()


@dataclass(kw_only=True)
class PySerial(Driver):
    url: str
    baudrate: int = field(default=115200)
    check_present: bool = field(default=True)

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
        self.logger.info("Connecting to %s, baudrate: %d", self.url, self.baudrate)
        if self.url != LOOP:
            reader, writer = await open_serial_connection(url=self.url, baudrate=self.baudrate, limit=1)
            writer.transport.set_write_buffer_limits(high=4096, low=0)
            async with AsyncSerial(
                reader=StreamReaderWrapper(reader),
                writer=StreamWriterWrapper(writer),
            ) as stream:
                yield stream
            self.logger.info("Disconnected from %s", self.url)
        else:
            tx, rx = create_memory_object_stream[bytes](32) # ty: ignore[call-non-callable]
            async with StapledObjectStream(tx, rx) as stream:
                yield stream
