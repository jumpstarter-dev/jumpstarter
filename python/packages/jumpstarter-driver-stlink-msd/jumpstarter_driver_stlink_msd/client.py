from dataclasses import dataclass
from pathlib import Path

import click
from jumpstarter_driver_opendal.client import FlasherClient

from jumpstarter.streams.encoding import Compression


@dataclass(kw_only=True)
class StlinkMsdFlasherClient(FlasherClient):
    """Client interface for ST-LINK mass storage flasher.

    Flashes STM32 boards by copying firmware to the ST-LINK's USB
    mass storage volume. Supports .elf, .bin, and .hex files.
    """

    def info(self) -> dict[str, str]:
        """Read board info from the ST-LINK volume."""
        return self.call("info")

    def flash_file(self, filepath) -> str:
        """Flash a local file to the STM32 board."""
        absolute = Path(filepath).resolve()
        return self.flash(absolute)

    def cli(self):
        base = super().cli()
        base.commands.pop("flash", None)
        base.commands.pop("dump", None)

        @base.command()
        def info():
            """Show ST-LINK volume information."""
            data = self.info()
            if not data:
                raise click.ClickException("No info available from ST-LINK volume.")
            for key, value in sorted(data.items()):
                click.echo(f"{key}: {value}")

        @base.command()
        @click.argument("file", type=click.Path(exists=True))
        @click.option("--compression", type=click.Choice(Compression, case_sensitive=False))
        def flash(file, compression):
            """Flash firmware (.elf, .bin, or .hex) to the STM32 board."""
            name = Path(file).name
            click.echo(f"Flashing {name}...")
            self.flash(file, target=name, compression=compression)
            click.echo("Flash complete — ST-LINK will program the target MCU.")

        return base
