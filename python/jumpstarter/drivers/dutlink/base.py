import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path

import pyudev
import usb.core
import usb.util
from anyio import fail_after, sleep
from anyio.streams.file import FileWriteStream

from jumpstarter.drivers import Driver, export
from jumpstarter.drivers.composite import CompositeInterface
from jumpstarter.drivers.power import PowerInterface, PowerReading
from jumpstarter.drivers.serial.pyserial import PySerial
from jumpstarter.drivers.storage import StorageMuxInterface


@dataclass(kw_only=True)
class DutlinkPower(PowerInterface, Driver):
    parent: "Dutlink"

    def control(self, action):
        return self.parent.control(
            usb.ENDPOINT_OUT,
            0x01,
            ["off", "on", "force-off", "force-on", "rescue"],
            action,
            None,
        )

    @export
    def on(self):
        return self.control("on")

    @export
    def off(self):
        return self.control("off")

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        prev = None

        while True:
            [v, a, _] = self.parent.control(
                usb.ENDPOINT_IN,
                0x04,
                ["version", "power", "voltage", "current"],
                "power",
                None,
            ).split()

            curr = PowerReading(voltage=float(v[:-1]), current=float(a[:-1]))

            if prev != curr:
                prev = curr
                yield curr

            await sleep(1)


@dataclass(kw_only=True)
class DutlinkStorageMux(StorageMuxInterface, Driver):
    parent: "Dutlink"
    storage_device: str

    def control(self, action):
        return self.parent.control(
            usb.ENDPOINT_OUT,
            0x02,
            ["off", "host", "dut"],
            action,
            None,
        )

    @export
    def host(self):
        return self.control("host")

    @export
    def dut(self):
        return self.control("dut")

    @export
    def off(self):
        return self.control("off")

    @export
    async def write(self, src: str):
        self.control("host")

        with fail_after(20):
            while True:
                if os.path.exists(self.storage_device):
                    try:
                        Path(self.storage_device).write_bytes(b"\0")
                    except OSError:
                        pass  # wait for device ready
                    else:
                        break

                await sleep(1)

        async with await FileWriteStream.from_path(self.storage_device) as stream:
            async for chunk in self.resource(src):
                await stream.send(chunk)


@dataclass(kw_only=True)
class Dutlink(CompositeInterface, Driver):
    serial: str | None = field(default=None)
    dev: usb.core.Device = field(init=False)
    itf: usb.core.Interface = field(init=False)

    storage_device: str

    power: DutlinkPower = field(init=False)
    storage: DutlinkStorageMux = field(init=False)
    console: PySerial = field(init=False, default=None)

    def items(self, parent=None):
        return super().items(parent) + self.power.items(self) + self.storage.items(self) + self.console.items(self)

    def __post_init__(self, *args):
        super().__post_init__(*args)
        for dev in usb.core.find(idVendor=0x2B23, idProduct=0x1012, find_all=True):
            serial = usb.util.get_string(dev, dev.iSerialNumber)
            if serial == self.serial or self.serial is None:
                self.dev = dev
                self.itf = usb.util.find_descriptor(
                    dev.get_active_configuration(),
                    bInterfaceClass=0xFF,
                    bInterfaceSubClass=0x1,
                    bInterfaceProtocol=0x1,
                )

                self.power = DutlinkPower(name="power", parent=self)
                self.storage = DutlinkStorageMux(name="storage", parent=self, storage_device=self.storage_device)

                for tty in pyudev.Context().list_devices(subsystem="tty", ID_SERIAL_SHORT=serial):
                    if self.console is None:
                        self.console = PySerial(name="console", url=tty.device_node)
                    else:
                        raise RuntimeError(f"multiple console found for the dutlink board with serial {serial}")

                if self.console is None:
                    raise RuntimeError(f"no console found for the dutlink board with serial {serial}")

                return

        raise FileNotFoundError("failed to find dutlink device")

    def control(self, direction, ty, actions, action, value):
        if direction == usb.ENDPOINT_IN:
            self.dev.ctrl_transfer(
                bmRequestType=usb.ENDPOINT_OUT | usb.TYPE_VENDOR | usb.RECIP_INTERFACE,
                wIndex=self.itf.bInterfaceNumber,
                bRequest=0x00,
            )

        op = actions.index(action)
        res = self.dev.ctrl_transfer(
            bmRequestType=direction | usb.TYPE_VENDOR | usb.RECIP_INTERFACE,
            wIndex=self.itf.bInterfaceNumber,
            bRequest=ty,
            wValue=op,
            data_or_wLength=(value if direction == usb.ENDPOINT_OUT else 512),
        )

        if direction == usb.ENDPOINT_IN:
            return bytes(res).decode("utf-8")
