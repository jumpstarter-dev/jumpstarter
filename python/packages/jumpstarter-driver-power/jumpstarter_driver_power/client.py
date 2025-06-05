import time
from collections.abc import Generator

import click

from .common import PowerReading
from jumpstarter.client import DriverClient


class PowerClient(DriverClient):
    def on(self) -> None:
        self.call("on")

    def off(self) -> None:
        self.call("off")

    def rescue(self) -> None:
        self.call("rescue")

    def cycle(self, wait: int = 2):
        """Power cycle the device"""
        self.logger.info("Starting power cycle sequence")
        self.off()
        self.logger.info(f"Waiting {wait} seconds...")
        time.sleep(wait)
        self.on()
        self.logger.info("Power cycle sequence complete")

    def read(self) -> Generator[PowerReading, None, None]:
        for v in self.streamingcall("read"):
            yield PowerReading.model_validate(v, strict=True)

    def cli(self):
        @click.group
        def base():
            """Generic power"""
            pass

        @base.command()
        def on():
            """Power on"""
            self.on()

        @base.command()
        def off():
            """Power off"""
            self.off()

        @base.command()
        @click.option("--wait", "-w", default=2, help="Wait time in seconds between off and on")
        def cycle(wait):
            """Power cycle"""
            click.echo(f"Power cycling with {wait} seconds wait time...")
            self.cycle(wait)

        return base


class VirtualPowerClient(PowerClient):
    def off(self, destroy: bool = False) -> None:
        self.call('off', destroy)

    def cli(self):
        parent = super().cli()

        @parent.command(name='off')
        @click.option('--destroy', is_flag=True, help='destroy the instance after powering it off')
        def off(destroy: bool):
            """Power off"""
            self.off(destroy)

        return parent
