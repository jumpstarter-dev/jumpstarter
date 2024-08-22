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

    def __post_init__(self, *args):
        super().__post_init__(*args)

        self.device = serial_for_url(self.url)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.pyserial.client.PySerialClient"

    @exportstream
    @asynccontextmanager
    async def connect(self):
        device = await run_sync(serial_for_url, self.url)
        async with AsyncSerial(device=device) as stream:
            yield stream
