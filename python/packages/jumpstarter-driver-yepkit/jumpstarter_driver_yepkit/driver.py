import threading
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import usb.core
import usb.util
from jumpstarter_driver_power.driver import PowerInterface, PowerReading

from jumpstarter.driver import Driver, export

VID = 0x04D8
PID = 0xF2F7

PORT_UP_COMMANDS = {"1": 0x11, "2": 0x12, "3": 0x13, "all": 0x1A}

PORT_DOWN_COMMANDS = {"1": 0x01, "2": 0x02, "3": 0x03, "all": 0x0A}

PORT_STATUS_COMMANDS = {"1": 0x21, "2": 0x22, "3": 0x23}

VALID_DEFAULTS = ["on", "off", "keep"]

# static shared array of usb devices, interfaces on same device cannot be claimed multiple times
_USB_DEVS = {}
_USB_DEVS_LOCK = threading.Lock()  # Lock for synchronizing access, we don't do multithread, but just in case..


@dataclass(kw_only=True)
class Ykush(PowerInterface, Driver):
    """driver for Yepkit Ykush USB Hub with Power control"""

    serial: str | None = field(default=None)
    default: str = "off"
    port: str = "all"

    dev: usb.core.Device = field(init=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        keys = PORT_UP_COMMANDS.keys()
        if self.port not in keys:
            raise ValueError(f"The ykush driver port must be any of the following values: {keys}")

        if self.default not in VALID_DEFAULTS:
            raise ValueError(f"The ykush driver default must be any of the following values: {VALID_DEFAULTS}")

        with _USB_DEVS_LOCK:
            # another instance already claimed this device?
            if self.serial is None and len(_USB_DEVS.keys()) > 0:
                self.serial = list(_USB_DEVS.keys())[0]
                self.dev = _USB_DEVS[self.serial]
                return

            if self.serial in _USB_DEVS:
                self.dev = _USB_DEVS[self.serial]
                return

            for dev in usb.core.find(idVendor=VID, idProduct=PID, find_all=True):
                serial = usb.util.get_string(dev, dev.iSerialNumber, 0)
                if serial == self.serial or self.serial is None:
                    _USB_DEVS[serial] = dev
                    if self.serial is None:
                        self.logger.warning(f"No serial number provided for ykush, using the first one found: {serial}")
                    self.serial = serial
                    self.dev = dev
                    return

            raise FileNotFoundError("failed to find ykush device")

    def _send_cmd(self, cmd, report_size=64):
        out_ep, in_ep = self._get_endpoints(self.dev)
        out_buf = [0x00] * report_size
        out_buf[0] = cmd  # YKUSH command

        # Write to the OUT endpoint
        out_ep.write(out_buf)

        # Read from the IN endpoint
        in_buf = in_ep.read(report_size, timeout=2000)
        return list(in_buf)

    def _get_endpoints(self, dev):
        """
        From the active configuration, find the first IN and OUT endpoints.
        """
        cfg = self.dev.get_active_configuration()
        interface = cfg[(0, 0)]

        out_endpoint = usb.util.find_descriptor(
            interface, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )

        in_endpoint = usb.util.find_descriptor(
            interface, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )

        if not out_endpoint or not in_endpoint:
            raise RuntimeError("Could not find both IN and OUT endpoints for ykush.")

        return out_endpoint, in_endpoint

    # reset function is called by the exporter to setup the default state
    def reset(self):
        if self.default == "on":
            self.on()
        elif self.default == "off":
            self.off()

    @export
    def on(self) -> None:
        self.logger.info(f"Power ON for Ykush {self.serial} on port {self.port}")
        cmd = PORT_UP_COMMANDS.get(self.port)
        _ = self._send_cmd(cmd)

    @export
    def off(self) -> None:
        self.logger.info(f"Power OFF for Ykush {self.serial} on port {self.port}")
        cmd = PORT_DOWN_COMMANDS.get(self.port)
        _ = self._send_cmd(cmd)

    @export
    def read(self) -> AsyncGenerator[PowerReading, None]:
        raise NotImplementedError
