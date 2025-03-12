from __future__ import annotations

import os
from dataclasses import dataclass, field

import pyudev
import usb.core
import usb.util
from anyio import fail_after, sleep
from anyio.streams.file import FileReadStream, FileWriteStream
from jumpstarter_driver_opendal.driver import StorageMuxFlasherInterface

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class SDWire(StorageMuxFlasherInterface, Driver):
    serial: str | None = field(default=None)
    dev: usb.core.Device = field(init=False)
    itf: usb.core.Interface = field(init=False)

    storage_device: str | None = field(default=None)

    def effective_storage_device(self):
        if self.storage_device is None:
            context = pyudev.Context()
            for udevice in (
                context.list_devices(subsystem="usb")
                .match_attribute("busnum", self.dev.bus)
                .match_attribute("devnum", self.dev.address)
            ):
                # find siblings block device
                for block in filter(lambda d: d.subsystem == "block", udevice.parent.children):
                    # find stable device link under by-diskseq
                    for storage_device in filter(
                        lambda link: link.startswith("/dev/disk/by-diskseq/"), block.device_links
                    ):
                        return storage_device
            return None
        else:
            return self.storage_device

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
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

            if self.effective_storage_device() is None:
                raise FileNotFoundError("failed to find sdcard driver on sd-wire device")

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

    async def wait_for_storage_device(self):
        with fail_after(10):
            storage_device = self.effective_storage_device()

            while True:
                # https://stackoverflow.com/a/2774125
                try:
                    fd = os.open(storage_device, os.O_WRONLY)
                    if os.lseek(fd, 0, os.SEEK_END) > 0:
                        break
                except OSError as e:
                    match e.errno:
                        case 123:  # No medium found
                            pass
                        case 5:  # Input/output error
                            pass
                        case _:
                            raise
                finally:
                    if "fd" in locals():
                        os.close(fd)
                await sleep(1)

            return storage_device

    @export
    async def write(self, src: str):
        self.host()
        storage_device = await self.wait_for_storage_device()
        async with await FileWriteStream.from_path(storage_device) as stream:
            async with self.resource(src) as res:
                async for chunk in res:
                    await stream.send(chunk)

    @export
    async def read(self, dst: str):
        self.host()
        storage_device = await self.wait_for_storage_device()
        async with await FileReadStream.from_path(storage_device) as stream:
            async with self.resource(dst) as res:
                async for chunk in stream:
                    await res.send(chunk)
