from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from anyio.abc import ObjectStream
from anyio.to_thread import run_sync
from serial import Serial, serial_for_url

from jumpstarter.driver import Driver, exportstream


@dataclass(kw_only=True)
class AsyncSerial(ObjectStream):
    device: Serial

    async def send(self, item):
        await run_sync(self.device.write, item)

    async def receive(self):
        size = max(self.device.in_waiting, 1)
        return await run_sync(self.device.read, size)

    async def send_eof(self):
        await run_sync(self.device.close)

    async def aclose(self):
        await run_sync(self.device.close)


@dataclass(kw_only=True)
class PySerial(Driver):
    url: str
    device: Serial = field(init=False)
    baudrate: int = field(default=115200)

    def __post_init__(self):
        super().__post_init__()
        self.device = serial_for_url(self.url, baudrate=self.baudrate)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_pyserial.client.PySerialClient"

    @exportstream
    @asynccontextmanager
    async def connect(self):
        self.logger.info("Connecting to %s, baudrate: %d", self.url, self.baudrate)
        device = await run_sync(serial_for_url, self.url, self.baudrate)
        async with AsyncSerial(device=device) as stream:
            yield stream
        self.logger.info("Disconnected from %s", self.url)
