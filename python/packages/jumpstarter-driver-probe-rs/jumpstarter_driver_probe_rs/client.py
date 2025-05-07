from dataclasses import dataclass
from pathlib import Path

import click
from jumpstarter_driver_opendal.adapter import OpendalAdapter
from opendal import Operator

from jumpstarter.client import DriverClient
from jumpstarter.common.exceptions import ArgumentError


@dataclass(kw_only=True)
class ProbeRsClient(DriverClient):
    """
    Client interface for probe-rs driver.

    This client provides methods to use probe-rs remotely.
    """

    def info(self) -> str:
        """Get probe-rs information about the target"""
        return self.call("info")

    def reset(self) -> str:
        """Reset the target, must be used after download to start the target"""
        return self.call("reset_target")

    def erase(self) -> str:
        """Erase the target memory, this is generally a slow operation."""
        # TODO: this is a very long operation, create a stream back to the client
        self.logger.info("Erasing target ..... this may take a while")
        return self.call("erase")

    def download(self, operator: Operator, path: str) -> str:
        """Download a file to the device"""
        with OpendalAdapter(client=self, operator=operator, path=path) as handle:
            return self.call("download", handle)

    def download_file(self, filepath) -> str:
        """Download a local file to the device"""
        absolute = Path(filepath).resolve()
        return self.download(operator=Operator("fs", root="/"), path=str(absolute))

    def read(self, width: int, address: int, words: int) -> list[int]:
        """Read from memory

        Args:
            - width: the width of the data to read, 8, 16, 32 or 64
            - address: the address to read from
            - words: the number of words to read
        """
        if width not in [8, 16, 32, 64]:
            raise ArgumentError("Width must be one of: 8, 16, 32, 64")
        if address < 0:
            raise ArgumentError("Address must be non-negative")
        if words <= 0:
            raise ArgumentError("Words must be positive")

        data_strs = self.call("read", f"b{int(width)}", "0x%x" % int(address), "%d" % words)
        return [int(data, 16) for data in data_strs]

    def cli(self):  # noqa: C901
        @click.group
        def base():
            """probe-rs client"""
            pass

        @base.command()
        def info():
            """Get target information"""
            print(self.info())

        @base.command()
        def reset():
            """Reset the target"""
            print(self.reset())

        @base.command()
        def erase():
            """Erase the target, this is a slow operation."""
            print(self.erase())

        @base.command()
        @click.argument("file")
        def download(file):
            """Download a file to the target"""
            print(self.download_file(file))

        @base.command()
        @click.argument("width", type=int)
        @click.argument("address", type=str)
        @click.argument("words", type=int)
        def read(width, address, words):
            """read from target memory"""
            # parse address to int, it could come in decimal format, or hex format prefixed by 0x
            if address.startswith("0x"):
                address = int(address, 16)
            else:
                address = int(address)

            data_ints = self.read(width, address, words)
            if width == 8:
                data_strs = ["%02x" % data for data in data_ints]
            elif width == 16:
                data_strs = ["%04x" % data for data in data_ints]
            elif width == 32:
                data_strs = ["%08x" % data for data in data_ints]
            elif width == 64:
                data_strs = ["%016x" % data for data in data_ints]

            print(" ".join(data_strs))

        return base
