from dataclasses import dataclass, field

import usb.core
import usb.util

from jumpstarter.drivers import Driver


@dataclass(kw_only=True)
class Dutlink(Driver):
    serial: str | None = field(default=None)
    dev: usb.core.Device = field(init=False)
    itf: usb.core.Interface = field(init=False)

    def items(self, parent=None):
        # return super().items(parent) + list(chain(*[child.items(self) for child in self.children]))
        return super().items(parent)

    def __post_init__(self, name):
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

                return

        raise FileNotFoundError("failed to find dutlink device")

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.dutlink.client.DutlinkClient"
