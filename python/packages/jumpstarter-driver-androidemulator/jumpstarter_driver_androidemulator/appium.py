"""Appium on the exporter for the emulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jumpstarter_driver_adb.appium import AndroidAppium

if TYPE_CHECKING:
    from .driver import AndroidEmulator


@dataclass(kw_only=True)
class AndroidEmulatorAppium(AndroidAppium):
    """Appium for the emulator, which must be powered on; Appium waits for it to boot."""

    parent: AndroidEmulator = field(repr=False)

    def server_port(self) -> int:
        return self.parent.adb_server_port

    def android_serial(self) -> str:
        power = self.parent.children["power"]
        process = getattr(power, "_process", None)
        if process is None or process.poll() is not None:
            raise RuntimeError("The emulator is not running; power it on first")
        return f"emulator-{self.parent.console_port}"
