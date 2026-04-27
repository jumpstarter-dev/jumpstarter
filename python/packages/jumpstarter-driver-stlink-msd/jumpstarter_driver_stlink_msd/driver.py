"""ST-LINK mass storage flasher for STM32 Nucleo and Discovery boards."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass

from anyio import to_thread
from anyio.streams.file import FileWriteStream
from jumpstarter_driver_opendal.driver import FlasherInterface

from .stlink_mount import find_all_stlink_mounts, find_stlink_mount
from jumpstarter.driver import Driver, export

_SUPPORTED_EXTENSIONS = frozenset({".bin", ".hex"})


def _validate_firmware_name(name: str) -> None:
    """Raise if the firmware file extension is not supported by ST-LINK MSD."""
    _, ext = os.path.splitext(name.lower())
    if ext not in _SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported firmware format '{ext or name}'. "
            f"ST-LINK mass storage only accepts .bin or .hex files. "
            f"Convert ELF files with: arm-none-eabi-objcopy -O binary input.elf output.bin"
        )


@dataclass(kw_only=True)
class StlinkMsdFlasher(FlasherInterface, Driver):
    """Flash STM32 boards by copying firmware to the ST-LINK USB mass storage volume.

    Supports .bin and .hex files. ELF files must be converted to .bin
    externally (e.g. via ``arm-none-eabi-objcopy -O binary``).
    """

    volume_name: str | None = None

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_stlink_msd.client.StlinkMsdFlasherClient"

    def _resolve_mount(self) -> str:
        mount = find_stlink_mount(self.volume_name)
        if mount is None:
            all_mounts = find_all_stlink_mounts()
            if len(all_mounts) == 0:
                raise FileNotFoundError(
                    "No ST-LINK mass storage volume found. "
                    "Check that the board is connected and the ST-LINK USB drive is mounted."
                )
            dirs = ", ".join(str(p) for p in all_mounts)
            if self.volume_name:
                raise FileNotFoundError(
                    f"ST-LINK volume '{self.volume_name}' not found. Available: {dirs}"
                )
            raise FileNotFoundError(
                f"Multiple ST-LINK volumes found: {dirs}. "
                "Set 'volume_name' in the config to select one."
            )
        return str(mount)

    @export
    def info(self) -> dict[str, str]:
        """Read DETAILS.TXT from the ST-LINK volume and return board metadata."""
        mount = self._resolve_mount()
        result: dict[str, str] = {}

        details_path = os.path.join(mount, "DETAILS.TXT")
        if os.path.isfile(details_path):
            with open(details_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if ":" in line:
                        key, _, value = line.partition(":")
                        result[key.strip()] = value.strip()

        result["mount_point"] = mount
        return result

    @export
    async def flash(self, source, target: str | None = None):
        """Flash firmware to the STM32 board via ST-LINK mass storage.

        Accepts .bin or .hex files only. ELF files are rejected — convert
        them externally before flashing.

        :param source: Firmware resource (local path or storage handle).
        :param target: Destination filename on the volume (default: ``firmware.bin``).
        """
        mount = self._resolve_mount()
        dest_name = target or "firmware.bin"
        _validate_firmware_name(dest_name)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = os.path.join(tmpdir, dest_name)

            async with await FileWriteStream.from_path(tmp_path) as stream:
                async with self.resource(source) as res:
                    async for chunk in res:
                        await stream.send(chunk)

            dest_path = os.path.join(mount, dest_name)
            self.logger.info("Copying firmware to %s", dest_path)

            def _copy() -> None:
                shutil.copy2(tmp_path, dest_path)
                fd = os.open(dest_path, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)

            await to_thread.run_sync(_copy)

        self.logger.info("Flash complete — ST-LINK will program the target MCU")

    @export
    async def dump(self, target, partition: str | None = None):
        """Not supported: ST-LINK mass storage is write-only for firmware."""
        raise NotImplementedError(
            "Reading flash via ST-LINK mass storage is not supported. "
            "Use probe-rs or STM32CubeProgrammer for flash readback."
        )
