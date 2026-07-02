"""Example **custom client** — subclasses the codegen-generated typed ``PowerClient`` to add
client-side convenience methods the interface itself doesn't have ("custom interfaces on the
client side") plus a ``j`` CLI. The Python sibling of Rust's ``CyclingPowerClient`` +
``#[client_cli] PowerCli`` and the JVM's ``CyclingPowerClient`` picocli commands.

The inherited ``on``/``off``/``read`` drive the driver over the native proto-bytes seam; the
additions just compose them. The example driver advertises this client via
``@driver(client="jumpstarter_driver_power_example.client.CyclingPowerClient")``.
"""

import time

import click

from ._generated.power_client import PowerClient
from jumpstarter.client.decorators import driver_click_group


class CyclingPowerClient(PowerClient):
    """The generated typed client, extended with power-cycling conveniences."""

    def cycle(self, wait: float = 2.0) -> None:
        """Power cycle — off, wait, on. A client-side method, NOT an interface RPC."""
        self.off()
        time.sleep(wait)
        self.on()

    def read_voltages(self) -> list[float]:
        """Convenience: just the voltages from a :meth:`read`."""
        return [reading.voltage for reading in self.read()]

    def cli(self):
        """The ``j <driver> {on | off | read | cycle}`` surface, driving the typed client."""

        @driver_click_group(self)
        def base():
            """Example power driver (proto-first)"""

        @base.command()
        def on():
            """Power on"""
            self.on()

        @base.command()
        def off():
            """Power off"""
            self.off()

        @base.command()
        def read():
            """Read the power state (prints each voltage/current reading)"""
            for reading in self.read():
                click.echo(f"voltage={reading.voltage} current={reading.current}")

        @base.command()
        @click.option("--wait", "-w", default=2.0, help="Seconds to wait between off and on")
        def cycle(wait: float):
            """Power cycle: off, wait, on"""
            click.echo(f"Power cycling with {wait} seconds wait time...")
            self.cycle(wait)

        return base
