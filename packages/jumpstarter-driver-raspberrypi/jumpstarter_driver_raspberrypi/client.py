from dataclasses import dataclass

import click
from jumpstarter_driver_power.client import PowerClient

from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class DigitalOutputClient(PowerClient):
    def on(self):
        """Turn power on"""
        self.call("on")

    def off(self):
        """Turn power off"""
        self.call("off")

    def cli(self):
        @click.group()
        def gpio():
            """GPIO power control commands"""
            pass

        for cmd in super().cli().commands.values():
            gpio.add_command(cmd)

        return gpio


@dataclass(kw_only=True)
class DigitalInputClient(DriverClient):
    def wait_for_active(self, timeout: float | None = None):
        self.call("wait_for_active", timeout)

    def wait_for_inactive(self, timeout: float | None = None):
        self.call("wait_for_inactive", timeout)
