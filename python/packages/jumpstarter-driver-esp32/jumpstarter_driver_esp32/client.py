from dataclasses import dataclass

import click
from jumpstarter_driver_opendal.client import FlasherClient

from jumpstarter.streams.encoding import Compression


@dataclass(kw_only=True)
class Esp32FlasherClient(FlasherClient):
    """Client interface for ESP32 flasher driver."""

    def get_chip_info(self) -> dict[str, str]:
        """Get chip information including name, features, and MAC address."""
        return self.call("get_chip_info")

    def erase(self):
        """Erase the entire flash memory."""
        return self.call("erase")

    def hard_reset(self):
        """Hard reset the ESP32 chip."""
        return self.call("hard_reset")

    def enter_bootloader(self):
        """Enter ESP32 download mode via DTR/RTS toggle."""
        return self.call("enter_bootloader")

    def cli(self):
        base = super().cli()

        # Override the inherited flash command to add --address
        base.commands.pop("flash", None)

        @base.command()
        @click.argument("file", type=click.Path(exists=True))
        @click.option("--address", "-a", default=None, help="Flash address (e.g. 0x1000)")
        @click.option("--compression", type=click.Choice(Compression, case_sensitive=False))
        def flash(file, address, compression):
            """Flash firmware to ESP32"""
            self.flash(file, target=address, compression=compression)
            click.echo("done")

        @base.command()
        def chip_info():
            """Get chip info (name, features, MAC)"""
            info = self.get_chip_info()
            for key, value in info.items():
                click.echo(f"{key}: {value}")

        @base.command()
        def erase():
            """Erase entire flash"""
            self.erase()
            click.echo("done")

        @base.command()
        def reset():
            """Hard reset the chip"""
            self.hard_reset()
            click.echo("done")

        @base.command()
        def bootloader():
            """Enter download mode"""
            self.enter_bootloader()
            click.echo("done")

        return base
