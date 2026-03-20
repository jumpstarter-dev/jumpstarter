from dataclasses import dataclass

import click
from jumpstarter_driver_opendal.client import FlasherClient

from jumpstarter.streams.encoding import Compression


@dataclass(kw_only=True)
class PiPicoClient(FlasherClient):
    """Client for Raspberry Pi Pico UF2 flashing via BOOTSEL mass storage."""

    def enter_bootloader(self):
        """Request BOOTSEL over the serial child when supported by firmware."""
        return self.call("enter_bootloader")

    def bootloader_info(self) -> dict[str, str]:
        """Parse ``INFO_UF2.TXT`` from the BOOTSEL volume."""
        return self.call("bootloader_info")

    def cli(self):
        base = super().cli()
        base.commands.pop("flash", None)

        @base.command("bootloader-info")
        def bootloader_info_cmd():
            """Show INFO_UF2.TXT key/value pairs from the BOOTSEL volume."""
            info = self.bootloader_info()
            if not info:
                raise click.ClickException("No INFO_UF2.TXT on the BOOTSEL volume (is the Pico in BOOTSEL mode?)")
            for key, value in sorted(info.items()):
                click.echo(f"{key}: {value}")

        @base.command("bootloader")
        def bootloader_cmd():
            """Request BOOTSEL over serial when supported by firmware."""
            self.enter_bootloader()
            click.echo("Pico is in BOOTSEL mode. Serial will be unavailable until firmware is flashed.")

        @base.command()
        @click.argument("file", type=click.Path(exists=True))
        @click.option(
            "--name",
            "-n",
            "dest_name",
            default=None,
            help="Destination filename on the BOOTSEL volume (default: Firmware.uf2)",
        )
        @click.option("--compression", type=click.Choice(Compression, case_sensitive=False))
        def flash(file, dest_name, compression):
            """Flash a UF2 firmware file to the Pico BOOTSEL drive."""
            try:
                click.echo("Entering BOOTSEL mode...")
                self.enter_bootloader()
            except Exception:
                click.echo("Could not enter BOOTSEL automatically. "
                           "Ensure the Pico is in BOOTSEL mode (hold BOOTSEL while plugging USB).")
            click.echo("Flashing firmware...")
            self.flash(file, target=dest_name, compression=compression)
            click.echo("Flash complete, Pico will reboot")

        return base
