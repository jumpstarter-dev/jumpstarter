from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import pyudev
import usb.core
import usb.util
from anyio import fail_after, sleep
from anyio.streams.file import FileReadStream, FileWriteStream
from jumpstarter_driver_composite.driver import CompositeInterface
from jumpstarter_driver_opendal.driver import StorageMuxInterface
from jumpstarter_driver_power.driver import PowerInterface, PowerReading
from jumpstarter_driver_pyserial.driver import PySerial
from serial.serialutil import SerialException

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class DutlinkConfig:
    serial: str | None = field(default=None)
    timeout_s: int = field(default=20)  # 20 seconds, power control sequences can block USB for a long time

    dev: usb.core.Device = field(init=False)
    itf: usb.core.Interface = field(init=False)
    tty: str | None = field(init=False, default=None)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        for dev in usb.core.find(idVendor=0x2B23, idProduct=0x1012, find_all=True):
            serial = usb.util.get_string(dev, dev.iSerialNumber)
            if serial == self.serial or self.serial is None:
                self.logger.debug(f"found dutlink board with serial {serial}")

                self.serial = serial
                self.dev = dev
                self.itf = usb.util.find_descriptor(
                    dev.get_active_configuration(),
                    bInterfaceClass=0xFF,
                    bInterfaceSubClass=0x1,
                    bInterfaceProtocol=0x1,
                )

                for tty in pyudev.Context().list_devices(subsystem="tty", ID_SERIAL_SHORT=serial):
                    if not self.tty:
                        self.tty = tty.device_node
                    else:
                        raise RuntimeError(f"multiple console found for the dutlink board with serial {serial}")
                if not self.tty:
                    raise RuntimeError(f"no console found for the dutlink board with serial {serial}")

                self.dev.default_timeout = self.timeout_s * 1000
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
            str_value = bytes(res).decode("utf-8")
            self.logger.debug("ctrl_transfer result: %s", str_value)
            return str_value


@dataclass(kw_only=True)
class DutlinkSerialConfig(DutlinkConfig, Driver):
    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.url = self.tty


@dataclass(kw_only=True)
class DutlinkSerial(PySerial, DutlinkSerialConfig):
    url: str | None = field(init=False, default=None)


@dataclass(kw_only=True)
class DutlinkPower(DutlinkConfig, PowerInterface, Driver):
    last_action: str | None = field(default=None)

    def control(self, action):
        self.logger.debug(f"power control: {action}")
        if self.last_action == action:
            return

        result = super().control(
            usb.ENDPOINT_OUT,
            0x01,
            ["off", "on", "force-off", "force-on", "rescue"],
            action,
            None,
        )
        self.last_action = action
        return result

    def reset(self):
        self.off()

    def close(self):
        self.off()

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
            [v, a, _] = (
                super()
                .control(
                    usb.ENDPOINT_IN,
                    0x04,
                    ["version", "power", "voltage", "current"],
                    "power",
                    None,
                )
                .split()
            )

            curr = PowerReading(voltage=float(v[:-1]), current=float(a[:-1]))

            if prev != curr:
                prev = curr
                yield curr

            await sleep(1)


@dataclass(kw_only=True)
class DutlinkStorageMux(DutlinkConfig, StorageMuxInterface, Driver):
    storage_device: str

    def control(self, action):
        self.logger.debug(f"storage control: {action}")
        return super().control(
            usb.ENDPOINT_OUT,
            0x02,
            ["off", "host", "dut"],
            action,
            None,
        )

    def reset(self):
        self.off()

    def close(self):
        self.off()

    @export
    def host(self):
        return self.control("host")

    @export
    def dut(self):
        return self.control("dut")

    @export
    def off(self):
        return self.control("off")

    async def wait_for_storage_device(self):
        with fail_after(20):
            while True:
                self.logger.debug(f"waiting for storage device {self.storage_device}")
                if os.path.exists(self.storage_device):
                    self.logger.debug(f"storage device {self.storage_device} is ready")
                    # https://stackoverflow.com/a/2774125
                    fd = os.open(self.storage_device, os.O_WRONLY)
                    try:
                        if os.lseek(fd, 0, os.SEEK_END) > 0:
                            break
                    finally:
                        os.close(fd)
                await sleep(1)

    @export
    async def write(self, src: str):
        self.host()
        await self.wait_for_storage_device()
        async with await FileWriteStream.from_path(self.storage_device) as stream:
            async with self.resource(src) as res:
                total_bytes = 0
                next_print = 0
                async for chunk in res:
                    await stream.send(chunk)
                    if total_bytes > next_print:
                        self.logger.debug(f"{self.storage_device} written {total_bytes / (1024 * 1024)} MB")
                        next_print += 50 * 1024 * 1024
                    total_bytes += len(chunk)

    @export
    async def read(self, dst: str):
        self.host()
        await self.wait_for_storage_device()
        async with await FileReadStream.from_path(self.storage_device) as stream:
            async with self.resource(dst) as res:
                async for chunk in stream:
                    await res.send(chunk)


@dataclass(kw_only=True)
class Dutlink(DutlinkConfig, CompositeInterface, Driver):
    alternate_console: str | None = field(default=None)
    storage_device: str
    baudrate: int = field(default=115200)

    """
    Parameters:
    ----------
    serial : str or None
        The serial number of the DUTLink device. Default is None.
    alternate_console : str or None
        The alternative console to be used, if a separate serial port console must be used,
        the path to the device i.e. '/dev/serial/by-id/usb-NVIDIA_Tegra_On-Platform_Operator_TOPOD83B461B-if01'.
        Default is None.
    timeout_s : int
        The timeout in seconds for USB operations. Default is set to 20 seconds.
    storage_device : str
        The path of the storage device used for data storage operations, as it will be enumerated when connected
        to the exporter host. i.e. '/dev/disk/by-id/usb-SanDisk_3.2_Gen_1_54345678AE6C-0:0', it is recommended to use
        by-id or by-path paths to avoid issues with device enumeration like 'sda,sdb,sdc...'
        which will change. Default is None.
    """

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.children["power"] = DutlinkPower(serial=self.serial, timeout_s=self.timeout_s)
        self.children["storage"] = DutlinkStorageMux(
            serial=self.serial, storage_device=self.storage_device, timeout_s=self.timeout_s
        )

        # if an alternate serial port has been requested, use it
        if self.alternate_console is not None:
            try:
                self.children["console"] = PySerial(url=self.alternate_console, baudrate=self.baudrate)
            except SerialException:
                self.logger.info(
                    f"failed to open alternate console {self.alternate_console} but trying to power on the target once"
                )
                self.children["power"].on()
                time.sleep(5)
                self.children["console"] = PySerial(url=self.alternate_console, baudrate=self.baudrate)
                self.children["power"].off()
        else:
            # otherwise look up the tty console provided by dutlink
            self.children["console"] = DutlinkSerial(serial=self.serial, baudrate=self.baudrate)
