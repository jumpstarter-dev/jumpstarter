from collections.abc import Generator

import click

from jumpstarter.client import DriverClient

from .common import PowerReading


class PowerClient(DriverClient):
    def on(self) -> str:
        return self.call("on")

    def off(self) -> str:
        return self.call("off")

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
            click.echo(self.on())

        @base.command()
        def off():
            """Power off"""
            click.echo(self.off())

        return base
