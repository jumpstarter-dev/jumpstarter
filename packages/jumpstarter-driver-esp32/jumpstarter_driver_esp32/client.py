from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import click
from jumpstarter_driver_opendal.adapter import OpendalAdapter
from opendal import Operator

from jumpstarter.client import DriverClient
from jumpstarter.common.exceptions import ArgumentError


@dataclass(kw_only=True)
class ESP32Client(DriverClient):
    """
    Client interface for ESP32 driver
    """

    def chip_info(self) -> Dict[str, Any]:
        """Get ESP32 chip information"""
        return self.call("chip_info")

    def reset(self) -> str:
        """Reset the ESP32 device"""
        return self.call("reset_device")

    def erase_flash(self) -> str:
        """Erase the entire flash memory"""
        self.logger.info("Erasing flash... this may take a while")
        return self.call("erase_flash")

    def flash_firmware(self, operator: Operator, path: str, address: int = 0x1000) -> str:
        """Flash firmware to the ESP32

        Args:
            operator: OpenDAL operator for file access
            path: Path to firmware file
            address: Flash address
        """
        if address < 0:
            raise ArgumentError("Flash address must be non-negative")

        with OpendalAdapter(client=self, operator=operator, path=path) as handle:
            return self.call("flash_firmware", handle, address)

    def flash_firmware_file(self, filepath: str, address: int = 0x1000) -> str:
        """Flash a local firmware file to the ESP32"""
        absolute = Path(filepath).resolve()
        if not absolute.exists():
            raise ArgumentError(f"File not found: {filepath}")
        return self.flash_firmware(operator=Operator("fs", root="/"), path=str(absolute), address=address)

    def read_flash(self, address: int, size: int) -> bytes:
        """Read flash contents from specified address

        Args:
            address: Flash address to read from
            size: Number of bytes to read
        """
        if address < 0:
            raise ArgumentError("Flash address must be non-negative")
        if size <= 0:
            raise ArgumentError("Size must be positive")

        return self.call("read_flash", address, size)

    def enter_bootloader(self) -> str:
        """Enter bootloader mode"""
        return self.call("enter_bootloader")

    def _info_command(self):
        """Get device information"""
        chip_info = self.chip_info()
        for key, value in chip_info.items():
            print(f"{key}: {value}")

    def _chip_id_command(self):
        """Get chip ID information"""
        info = self.chip_info()
        print(f"Chip Type: {info.get('chip_type', 'Unknown')}")
        if "mac_address" in info:
            print(f"MAC Address: {info['mac_address']}")
        if "chip_revision" in info:
            print(f"Chip Revision: {info['chip_revision']}")

    def _reset_command(self):
        """Reset the device"""
        result = self.reset()
        print(result)

    def _erase_command(self):
        """Erase the entire flash"""
        print("Erasing flash...")
        result = self.erase_flash()
        print(result)

    def _parse_address(self, address):
        """Parse address string to integer"""
        try:
            if isinstance(address, str) and address.startswith("0x"):
                return int(address, 16)
            else:
                return int(float(address))
        except (ValueError, TypeError):
            return 0x10000  # Default fallback

    def _parse_size(self, size):
        """Parse size string to integer"""
        try:
            if size.startswith("0x"):
                return int(size, 16)
            else:
                return int(float(size))
        except (ValueError, TypeError):
            return 1024  # Default fallback

    def _flash_command(self, firmware_file, address):
        """Flash firmware to the device"""
        address = self._parse_address(address)
        print(f"Flashing {firmware_file} to address 0x{address:x}...")
        result = self.flash_firmware_file(firmware_file, address)
        print(result)

    def _read_flash_command(self, address, size, output):
        """Read flash contents"""
        address = self._parse_address(address)
        size = self._parse_size(size)

        print(f"Reading {size} bytes from address 0x{address:x}...")
        data = self.read_flash(address, size)

        if output:
            with open(output, "wb") as f:
                f.write(data)
            print(f"Data written to {output}")
        else:
            # Print as hex
            hex_data = data.hex()
            for i in range(0, len(hex_data), 32):
                addr_offset = address + i // 2
                line = hex_data[i : i + 32]
                print(f"0x{addr_offset:08x}: {line}")

    def _bootloader_command(self):
        """Enter bootloader mode"""
        result = self.enter_bootloader()
        print(result)

    def cli(self):
        @click.group()
        def base():
            """ESP32 client"""
            pass

        @base.command()
        def info():
            """Get device information"""
            self._info_command()

        @base.command("chip-id")
        def chip_id():
            """Get chip ID information"""
            self._chip_id_command()

        @base.command()
        def reset():
            """Reset the device"""
            self._reset_command()

        @base.command()
        def erase():
            """Erase the entire flash"""
            self._erase_command()

        @base.command()
        @click.argument("firmware_file", type=click.Path(exists=True))
        @click.option("--address", "-a", default="0x10000", type=str, help="Flash address (hex or decimal)")
        def flash(firmware_file, address):
            """Flash firmware to the device"""
            self._flash_command(firmware_file, address)

        @base.command("read-flash")
        @click.argument("address", type=str)
        @click.argument("size", type=str)
        @click.option("--output", "-o", type=click.Path(), help="Output file (default: print hex)")
        def read_flash_cmd(address, size, output):
            """Read flash contents"""
            self._read_flash_command(address, size, output)

        @base.command()
        def bootloader():
            """Enter bootloader mode"""
            self._bootloader_command()

        return base
