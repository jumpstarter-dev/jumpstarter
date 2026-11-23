import json
import time

import click
from jumpstarter_driver_composite.client import CompositeClient

from .common import RadioPort


class RadioClient(CompositeClient):
    """Client for one of a device's radios. Its ports are reached through its children."""

    def start(self) -> None:
        self.call("start")

    def stop(self) -> None:
        self.call("stop")

    def status(self) -> dict:
        return self.call("status")

    def ports(self) -> list[RadioPort]:
        return [RadioPort.model_validate(port) for port in self.call("ports")]

    def wait_until_attached(self, timeout: float = 120) -> dict:
        """Start the local end and wait until it has the radio (the device is powered on)."""
        self.start()
        deadline = time.monotonic() + timeout
        while True:
            status = self.status()
            if status.get("attached"):
                return status
            if not status.get("running"):
                raise RuntimeError(f"the radio's local end stopped: {status.get('error') or status}")
            if time.monotonic() > deadline:
                raise TimeoutError("the radio was not found: is the device powered on?")
            time.sleep(1)

    def cli(self):
        base = super().cli()
        base.help = "One of the device's radios, as ports that can be joined"

        @base.command()
        def start():
            """Bring the local end up"""
            self.start()

        @base.command()
        def stop():
            """Take the local end down"""
            self.stop()

        @base.command()
        def status():
            """Whether it has the radio and is joined to a far end"""
            click.echo(json.dumps(self.status(), indent=2))

        @base.command()
        def ports():
            """The ports this end offers"""
            click.echo(json.dumps([port.model_dump() for port in self.ports()], indent=2))

        return base
