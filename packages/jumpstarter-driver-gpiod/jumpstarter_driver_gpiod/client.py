from dataclasses import dataclass
from enum import Enum

import click
from jumpstarter_driver_power.client import PowerClient

from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group


class PinState(Enum):
    ACTIVE = 1
    INACTIVE = 0

    def __str__(self):
        return self.name.lower()


@dataclass(kw_only=True)
class DigitalOutputClient(PowerClient):
    """
    A client for handling digital output operations on GPIO pins.
    """

    def on(self):
        """Turn gpio on."""
        self.call("on")

    def off(self):
        """Turn gpio off."""
        self.call("off")

    def read(self):
        """Read gpio state."""
        return PinState(int(self.call("read_pin")))

    def cli(self):
        @driver_click_group(self)
        def gpio():
            """GPIO power control commands."""
            pass

        for cmd in super().cli().commands.values():
            gpio.add_command(cmd)

        @gpio.command()
        def on():
            """Turn gpio on."""
            self.on()

        @gpio.command()
        def off():
            """Turn gpio off."""
            self.off()

        @gpio.command()
        def read():
            """read pin."""
            print(self.read())

        return gpio


@dataclass(kw_only=True)
class DigitalInputClient(DriverClient):
    """
    A client for handling digital input operations on GPIO pins.
    """

    def wait_for_active(self, timeout: float | None = None):
        self.call("wait_for_active", timeout)

    def wait_for_inactive(self, timeout: float | None = None):
        self.call("wait_for_inactive", timeout)

    def wait_for_edge(self, edge_type: str, timeout: float | None = None):
        self.call("wait_for_edge", edge_type, timeout)

    def read(self):
        return PinState(int(self.call("read_pin")))

    def cli(self):
        @driver_click_group(self)
        def gpio():
            """GPIO input commands."""
            pass

        @gpio.command()
        def read():
            """Read input."""
            print(self.read())

        @gpio.command()
        @click.argument("edge_type", type=click.Choice(["rising", "falling"]))
        @click.option("--timeout", "-t", default="3600", help="Timeout in seconds")
        def wait_for_edge(edge_type: str, timeout: str | None = None):
            """Wait for edge"""
            self.wait_for_edge(edge_type, float(timeout))

        @gpio.command()
        @click.option("--timeout", "-t", default="3600", help="Timeout in seconds")
        def wait_for_active(timeout: str | None = None):
            """Wait for active"""
            self.wait_for_active(float(timeout))

        @gpio.command()
        @click.option("--timeout", "-t", default="3600", help="Timeout in seconds")
        def wait_for_inactive(timeout: str | None = None):
            """Wait for inactive"""
            self.wait_for_inactive(float(timeout))

        return gpio
