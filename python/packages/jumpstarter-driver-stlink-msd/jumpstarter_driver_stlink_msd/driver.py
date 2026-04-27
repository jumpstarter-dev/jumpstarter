"""ST-LINK mass storage flasher for STM32 Nucleo and Discovery boards."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from anyio import to_thread
from anyio.streams.file import FileWriteStream
from jumpstarter_driver_opendal.driver import FlasherInterface

from .stlink_mount import find_all_stlink_mounts, find_stlink_mount
from jumpstarter.driver import Driver, export


def _find_objcopy() -> str | None:
    """Find an objcopy binary that can handle ARM ELF files."""
    for name in (
        "arm-none-eabi-objcopy",
        "llvm-objcopy",
        "arm-zephyr-eabi-objcopy",
        "objcopy",
    ):
        path = shutil.which(name)
        if path:
            return path
    return None


def _elf_to_bin(elf_path: str, bin_path: str, objcopy_path: str | None = None) -> None:
    """Convert an ELF file to raw binary using objcopy."""
    objcopy = objcopy_path or _find_objcopy()
    if objcopy is None:
        raise FileNotFoundError(
            "No objcopy found. Install arm-none-eabi-gcc, llvm, or the Zephyr SDK."
        )

    result = subprocess.run(
        [objcopy, "-O", "binary", elf_path, bin_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"objcopy failed: {result.stderr.strip()}")


def _detect_format(filename: str) -> str:
    """Detect firmware format from filename extension."""
    lower = filename.lower()
    if lower.endswith(".elf"):
        return "elf"
    if lower.endswith(".bin"):
        return "bin"
    if lower.endswith(".hex"):
        return "hex"
    return "unknown"


@dataclass(kw_only=True)
class StlinkMsdFlasher(FlasherInterface, Driver):
    """Flash STM32 boards by copying firmware to the ST-LINK USB mass storage volume.

    Supports .elf files (converted to .bin via objcopy), .bin files (copied directly),
    and .hex files (copied directly). This allows using the same .elf build artifact
    for both virtual targets (Renode) and physical targets (Nucleo/Discovery boards).
    """

    volume_name: str | None = None
    objcopy_path: str | None = None

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
        """Read DETAILS.TXT and MBED.HTM from the ST-LINK volume."""
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

        Accepts .elf (auto-converted to .bin), .bin, or .hex files.
        The ``target`` parameter is the destination filename on the volume
        (default: ``firmware.bin``).
        """
        mount = self._resolve_mount()
        src_name = target or "firmware.bin"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = os.path.join(tmpdir, "input_firmware")

            async with await FileWriteStream.from_path(tmp_path) as stream:
                async with self.resource(source) as res:
                    async for chunk in res:
                        await stream.send(chunk)

            fmt = _detect_format(src_name)
            if fmt == "unknown":
                fmt = _detect_format(tmp_path)

            if fmt == "elf":
                bin_path = os.path.join(tmpdir, "firmware.bin")
                self.logger.info("Converting ELF to BIN using objcopy")
                await to_thread.run_sync(
                    lambda: _elf_to_bin(tmp_path, bin_path, self.objcopy_path)
                )
                flash_src = bin_path
                dest_name = src_name.rsplit(".", 1)[0] + ".bin" if src_name.lower().endswith(".elf") else src_name
            else:
                flash_src = tmp_path
                dest_name = src_name

            dest_path = os.path.join(mount, dest_name)
            self.logger.info("Copying firmware to %s", dest_path)

            def _copy() -> None:
                shutil.copy2(flash_src, dest_path)
                with open(dest_path, "rb") as f:
                    os.fsync(f.fileno())

            await to_thread.run_sync(_copy)

        self.logger.info("Flash complete — ST-LINK will program the target MCU")

    @export
    async def dump(self, target, partition: str | None = None):
        """Not supported: ST-LINK mass storage is write-only for firmware."""
        raise NotImplementedError(
            "Reading flash via ST-LINK mass storage is not supported. "
            "Use probe-rs or STM32CubeProgrammer for flash readback."
        )
