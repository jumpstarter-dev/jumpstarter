from . import Composite
from .. import DriverBase
from ..power import Power, PowerReading
from ..serial import PySerial
from ..storage import StorageMux, StorageMuxLocalWriterMixin
from dataclasses import dataclass, field
from collections.abc import Generator
from typing import List, Optional
from serial import Serial
import usb.core
import usb.util
import pyudev


@dataclass(kw_only=True)
class Dutlink(Composite):
    serial: Optional[str]
    devices: List[DriverBase] = field(init=False)
    dev: usb.core.Device = field(init=False)

    def __post_init__(self):
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

                self.devices = [
                    DutlinkPower(
                        labels={"jumpstarter.dev/name": "power"},
                        parent=self,
                    ),
                    DutlinkStorageMux(
                        labels={"jumpstarter.dev/name": "storage"},
                        parent=self,
                    ),
                ]

                udev = pyudev.Context()
                for tty in udev.list_devices(subsystem="tty", ID_SERIAL_SHORT=serial):
                    self.devices.append(
                        PySerial(
                            labels={"jumpstarter.dev/name": "serial"},
                            device=Serial(tty.device_node, baudrate=9600),
                        )
                    )

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


@dataclass(kw_only=True)
class DutlinkPower(Power):
    parent: Dutlink

    def control(self, action):
        return self.parent.control(
            usb.ENDPOINT_OUT,
            0x01,
            ["off", "on", "force-off", "force-on", "rescue"],
            action,
            None,
        )

    def on(self) -> str:
        return self.control("on")

    def off(self) -> str:
        return self.control("off")

    def read(self) -> Generator[PowerReading, None, None]:
        yield None


@dataclass(kw_only=True)
class DutlinkStorageMux(StorageMuxLocalWriterMixin, StorageMux):
    parent: Dutlink

    def control(self, action):
        return self.parent.control(
            usb.ENDPOINT_OUT,
            0x02,
            ["off", "host", "dut"],
            action,
            None,
        )

    def host(self) -> str:
        udev = pyudev.Context()

        monitor = pyudev.Monitor.from_netlink(udev)
        monitor.filter_by("block", "disk")

        self.control("host")

        disk = monitor.poll(timeout=10)

        return disk.device_node

    def dut(self):
        return self.control("dut")

    def off(self):
        return self.control("off")
