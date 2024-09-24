from __future__ import annotations

import os
from dataclasses import dataclass, field

import usb.core
import usb.util
from anyio import fail_after, sleep
from anyio.streams.file import FileWriteStream

from jumpstarter.driver import Driver, export
from jumpstarter.drivers.storage.driver import StorageMuxInterface


@dataclass(kw_only=True)
class SDWire(StorageMuxInterface, Driver):
    serial: str | None = field(default=None)
    dev: usb.core.Device = field(init=False)
    itf: usb.core.Interface = field(init=False)

    storage_device: str

    def __post_init__(self):
        super().__post_init__()
        for dev in usb.core.find(idVendor=0x04E8, idProduct=0x6001, find_all=True):
            product = usb.util.get_string(dev, dev.iProduct)
            serial = usb.util.get_string(dev, dev.iSerialNumber)

            if product != "sd-wire":
                continue

            if serial == self.serial or self.serial is None:
                self.dev = dev
                self.itf = usb.util.find_descriptor(
                    dev.get_active_configuration(),
                    bInterfaceClass=0xFF,
                    bInterfaceSubClass=0xFF,
                    bInterfaceProtocol=0xFF,
                )

            return

        raise FileNotFoundError("failed to find sd-wire device")

    def select(self, target):
        self.dev.ctrl_transfer(
            bmRequestType=usb.ENDPOINT_OUT | usb.TYPE_VENDOR | usb.RECIP_DEVICE,
            bRequest=0xB,
            wIndex=0,
            wValue=(0x20 << 8) | target,
        )

    def query(self):
        return (
            "host"
            if self.dev.ctrl_transfer(
                bmRequestType=usb.ENDPOINT_IN | usb.TYPE_VENDOR | usb.RECIP_DEVICE,
                bRequest=0xC,
                wIndex=0,
                wValue=0,
                data_or_wLength=1,
            )[0]
            & 0x01
            else "dut"
        )

    @export
    def host(self):
        self.select(0xF1)

    @export
    def dut(self):
        self.select(0xF0)

    @export
    def off(self):
        self.host()

    @export
    async def write(self, src: str):
        self.host()

        with fail_after(10):
            while True:
                # https://stackoverflow.com/a/2774125
                fd = os.open(self.storage_device, os.O_WRONLY)
                try:
                    if os.lseek(fd, 0, os.SEEK_END) > 0:
                        break
                finally:
                    os.close(fd)
                await sleep(1)

        async with await FileWriteStream.from_path(self.storage_device) as stream:
            async with self.resource(src) as res:
                async for chunk in res:
                    await stream.send(chunk)
