import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial

from anyio.abc import ObjectStream
from bleak import BleakClient, BleakGATTCharacteristic
from bleak.exc import BleakError

from jumpstarter.driver import Driver, export, exportstream


def _ble_notify_handler(_sender: BleakGATTCharacteristic, data: bytearray, data_queue: asyncio.Queue):
    """Notification handler that puts received data into a queue."""
    try:
        data_queue.put_nowait(data)
    except asyncio.QueueFull:
        print("Warning: Data queue is full, dropping message")


class AsyncBleConfig():
    def __init__(
        self,
        address: str,
        service_uuid: str,
        write_char_uuid: str,
        notify_char_uuid: str,
    ):
        self.address = address
        self.service_uuid = service_uuid
        self.write_char_uuid = write_char_uuid
        self.notify_char_uuid = notify_char_uuid


@dataclass(kw_only=True)
class AsyncBleWrapper(ObjectStream):
    client: BleakClient
    config: AsyncBleConfig
    notify_queue: asyncio.Queue

    async def send(self, data: bytes):
        await self.client.write_gatt_char(self.config.write_char_uuid, data)

    async def receive(self):
        return bytes(await self.notify_queue.get())

    async def send_eof(self):
        # BLE characteristics don't have an explicit EOF mechanism
        pass

    async def aclose(self):
        await self.client.disconnect()


@dataclass(kw_only=True)
class BleWriteNotifyStream(Driver):
    """
    Bluetooth Low Energy (BLE) driver for Jumpstarter
    This driver connects to the specified BLE device and
    provides a write (write_char_uuid) and a read (notify_char_uuid) stream
    for data transfer.
    """

    address: str
    service_uuid: str
    write_char_uuid: str
    notify_char_uuid: str

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ble.client.BleWriteNotifyStreamClient"

    @export
    async def info(self) -> str:
        return f"""BleWriteNotifyStream Driver connected to
- Address:          {self.address}
- Service UUID:     {self.service_uuid}
- Write Char UUID:  {self.write_char_uuid}
- Notify Char UUID: {self.notify_char_uuid}"""

    async def _check_ble_characteristics(self, client: BleakClient):
        """Check if the required BLE service and characteristics are available."""
        svcs = list(client.services)
        for svc in svcs:
            if svc.uuid == self.service_uuid:
                chars_uuid = [char.uuid for char in svc.characteristics]
                if self.write_char_uuid not in chars_uuid:
                    raise BleakError(
                        f"Write characteristic UUID {self.write_char_uuid} not found on device.")
                if self.notify_char_uuid not in chars_uuid:
                    raise BleakError(
                        f"Notify characteristic UUID {self.notify_char_uuid} not found on device.")
                return

        raise BleakError(
            f"Service UUID {self.service_uuid} not found on device.")

    @exportstream
    @asynccontextmanager
    async def connect(self):
        self.logger.info(
            "Connecting to BLE device at Address: %s", self.address)
        async with BleakClient(self.address) as client:
            try:
                if client.is_connected:
                    notify_queue = asyncio.Queue(maxsize=1000)
                    self.logger.info(
                        "Connected to BLE device at Address: %s", self.address)

                    # check if required characteristics are available
                    await self._check_ble_characteristics(client)

                    # register notification handler if notify_char_uuid is provided
                    notify_handler = partial(
                        _ble_notify_handler, data_queue=notify_queue)
                    await client.start_notify(self.notify_char_uuid, notify_handler)
                    self.logger.info(
                        "Setting up notification handler for characteristic UUID: %s", self.notify_char_uuid)

                    async with AsyncBleWrapper(
                        client=client,
                        notify_queue=notify_queue,
                        config=AsyncBleConfig(
                            address=self.address,
                            service_uuid=self.service_uuid,
                            write_char_uuid=self.write_char_uuid,
                            # read_char_uuid=self.read_char_uuid,
                            notify_char_uuid=self.notify_char_uuid,
                        ),
                    ) as stream:
                        yield stream
                        self.logger.info(
                            "Disconnecting from BLE device at Address: %s", self.address)

                else:
                    self.logger.error(
                        "Failed to connect to BLE device at Address: %s", self.address)
                    raise BleakError(
                        f"Failed to connect to BLE device at Address: {self.address}")

            except BleakError as e:
                self.logger.error("Failed to connect to BLE device: %s", e)
                raise
