from collections.abc import Generator

import asyncclick as click

from .common import PowerReading
from jumpstarter.client import DriverClient


class PowerClient(DriverClient):
    def on(self) -> None:
        self.call("on")

    def off(self) -> None:
        self.call("off")

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

        return base
