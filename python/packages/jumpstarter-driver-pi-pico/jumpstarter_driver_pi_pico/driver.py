"""Raspberry Pi Pico / Pico 2 UF2 flashing via BOOTSEL USB mass storage (no picotool)."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass

from anyio import to_thread
from anyio.streams.file import FileWriteStream
from jumpstarter_driver_opendal.driver import FlasherInterface
from serial import serial_for_url

from .bootloader_mount import find_all_bootloader_mounts
from jumpstarter.driver import Driver, export


@contextmanager
def _temporary_filename(suffix=""):
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        yield name
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


@dataclass(kw_only=True)
class PiPico(FlasherInterface, Driver):
    """Flash UF2 firmware by copying it onto the BOOTSEL USB drive.

    Works with RP2040 and RP2350. Flashing uses the mounted BOOTSEL volume.

    BOOTSEL entry methods (tried in priority order by ``enter_bootloader``):

    1. **GPIO reset** — ``bootsel`` + ``run`` children (DigitalOutput).
       Assert BOOTSEL low, pulse RUN low, release. Works regardless of
       firmware. Requires two GPIO lines wired to the Pico BOOTSEL pad and
       RUN pin.
    2. **1200-baud serial touch** — ``serial`` child. Opens the USB CDC port
       at 1200 baud and toggles DTR. Only works when the running firmware
       implements the convention (Pico SDK ``pico_stdio_usb``, CircuitPython,
       MicroPython, Arduino).
    """

    bootloader_touch_baudrate: int = 1200
    bootloader_wait_seconds: float = 5.0
    gpio_reset_hold_seconds: float = 0.1

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    @property
    def _serial(self):
        return self.children["serial"]

    def _resolve_mounts(self, allm):
        if len(allm) == 1:
            return allm[0]
        if len(allm) == 0:
            raise FileNotFoundError("No Pico BOOTSEL volume found. Hold BOOTSEL while plugging USB.")
        dirs = ", ".join(str(p) for p in allm)
        raise FileNotFoundError(f"Multiple Pico BOOTSEL volumes found: {dirs}. Unplug extras.")

    def _resolve_mount_path(self):
        return self._resolve_mounts(find_all_bootloader_mounts())

    @property
    def _has_gpio_children(self) -> bool:
        return "bootsel" in self.children and "run" in self.children

    def _gpio_bootsel_reset(self):
        """Enter BOOTSEL by driving the BOOTSEL and RUN pins via GPIO children.

        Sequence: assert BOOTSEL low, pulse RUN low (reset), release RUN.
        The Pico sees BOOTSEL held during reset and enters USB mass-storage
        bootloader. BOOTSEL is released after the pulse.
        """
        bootsel = self.children["bootsel"]
        run = self.children["run"]
        hold = self.gpio_reset_hold_seconds

        self.logger.info("Entering BOOTSEL via GPIO reset (bootsel + run pins)")
        bootsel.on()
        time.sleep(hold)
        run.on()
        time.sleep(hold)
        run.off()
        time.sleep(hold / 2)
        bootsel.off()

    def _touch_serial_for_bootloader(self):
        serial = serial_for_url(self._serial.url, baudrate=self.bootloader_touch_baudrate)
        try:
            serial.dtr = True
            time.sleep(0.05)
            serial.dtr = False
            time.sleep(0.1)
        finally:
            serial.close()

    def _wait_for_bootloader_mount(self):
        deadline = time.monotonic() + self.bootloader_wait_seconds
        while time.monotonic() < deadline:
            mounts = find_all_bootloader_mounts()
            if mounts:
                return self._resolve_mounts(mounts)
            time.sleep(0.1)
        raise FileNotFoundError("No Pico BOOTSEL volume found after bootloader request.")

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_pi_pico.client.PiPicoClient"

    @export
    def enter_bootloader(self):
        """Request BOOTSEL mode using the best available method.

        Priority: already mounted > GPIO reset (bootsel + run children) >
        1200-baud serial touch > error.
        """
        if find_all_bootloader_mounts():
            return

        if self._has_gpio_children:
            self._gpio_bootsel_reset()
            self._wait_for_bootloader_mount()
            return

        if "serial" in self.children:
            self.logger.info("Requesting Pico BOOTSEL over serial %s", self._serial.url)
            self._touch_serial_for_bootloader()
            self._wait_for_bootloader_mount()
            return

        raise NotImplementedError(
            "Programmatic BOOTSEL entry requires either GPIO children "
            "(bootsel + run) or a serial child driver."
        )

    @export
    def bootloader_info(self) -> dict[str, str]:
        """Parse ``INFO_UF2.TXT`` from the BOOTSEL volume, if present."""
        mount = self._resolve_mount_path()
        info_path = mount / "INFO_UF2.TXT"
        try:
            text = info_path.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, IsADirectoryError):
            return {}
        out: dict[str, str] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
        return out

    @export
    async def flash(self, source, target: str | None = None):
        """Copy a UF2 image onto the BOOTSEL drive.

        :param source: Image resource (URL or storage path).
        :param target: Optional destination filename on the volume (default
            ``Firmware.uf2``). Must end with ``.uf2`` or it is appended.
        """
        mounts = find_all_bootloader_mounts()
        if not mounts and (self._has_gpio_children or "serial" in self.children):
            await to_thread.run_sync(self.enter_bootloader)
            mounts = find_all_bootloader_mounts()
        mount = self._resolve_mounts(mounts)
        dest_name = target or "Firmware.uf2"
        if not dest_name.lower().endswith(".uf2"):
            dest_name = f"{dest_name}.uf2"
        dest_path = mount / dest_name

        with _temporary_filename(suffix=".uf2") as tmp_path:
            async with await FileWriteStream.from_path(tmp_path) as stream:
                async with self.resource(source) as res:
                    async for chunk in res:
                        await stream.send(chunk)

            self.logger.info("Copying UF2 to BOOTSEL volume %s", dest_path)

            def _copy() -> None:
                shutil.copy2(tmp_path, dest_path)
                with open(dest_path, "rb") as f:
                    os.fsync(f.fileno())

            await to_thread.run_sync(_copy)

        self.logger.info("Flash complete, Pico will reboot")

    @export
    async def dump(self, target, partition: str | None = None):
        """Not supported: UF2 mass storage cannot read flash back without picotool/SWD."""
        raise NotImplementedError(
            "Dumping flash is not supported by the Pi Pico UF2 driver. "
            "Use jumpstarter-driver-probe-rs (SWD), or picotool save."
        )
