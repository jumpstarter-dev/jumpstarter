import json
import time

import click
from jumpstarter_driver_composite.client import CompositeClient

from .common import BluetoothIdentity


class RadioClient(CompositeClient):
    """Client for one of a device's radios.

    A radio's ports are joined by the forwards of the lease, not from here: this starts and stops the radio's
    local end and says how it is doing.
    """

    def start(self) -> None:
        self.call("start")

    def stop(self) -> None:
        self.call("stop")

    def status(self) -> dict:  # ty: ignore[invalid-property-type-override]
        return self.call("status")

    def ports(self) -> list[dict]:
        """The ports the radio declares: name, direction, protocol, attachment and transport."""
        return self.call("declared_ports")

    def _wait_until(self, what: str, timeout: float, problem: str) -> dict:
        self.start()
        deadline = time.monotonic() + timeout
        while True:
            status = self.status()
            if status.get(what):
                return status
            if not status.get("running"):
                raise RuntimeError(f"the radio's local end stopped: {status.get('error') or status}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"{problem}: {status.get('error') or status}")
            time.sleep(1)

    def wait_until_attached(self, timeout: float = 120) -> dict:
        """Start the local end and wait until it has the radio (the device is powered on)."""
        return self._wait_until("attached", timeout, "the radio was not found (is the device powered on?)")

    def wait_until_joined(self, timeout: float = 120) -> dict:
        """Start the local end and wait until a far end is joined to it (the forward's path is open)."""
        return self._wait_until("peer", timeout, "no far end joined (does the lease have a forward for this port?)")

    def cli(self):
        base = super().cli()
        base.help = "One of the device's radios, as ports that the forwards of a lease join"

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
            """The ports this radio declares"""
            click.echo(json.dumps(self.ports(), indent=2))

        return base


class MockRadioClient(RadioClient):
    """Client for a radio with nothing on the air."""

    def exchange(self, data: str, timeout: float = 5.0) -> str:
        """Say something into the port, and give back what comes back in answer."""
        return self.call("exchange", data, timeout)

    def cli(self):
        base = super().cli()

        @base.command()
        @click.argument("data")
        @click.option("--timeout", type=float, default=5.0, show_default=True)
        def exchange(data: str, timeout: float):
            """Say DATA into the port and print what comes back"""
            click.echo(self.exchange(data, timeout))

        return base


class BluetoothRadioClient(RadioClient):
    """Client for a Bluetooth radio that is one half of a bridge to another radio."""

    def identity(self) -> BluetoothIdentity:
        """What the device of this radio is on the air, as the far half is told."""
        return BluetoothIdentity.model_validate(self.call("identity"))

    def set_identity(
        self, name: str | None = None, address: str | None = None, class_of_device: int | None = None
    ) -> BluetoothIdentity:
        """Say what the device of this radio is on the air: its name, its own address, its class of device.
        What is left out stays as it is."""
        return BluetoothIdentity.model_validate(
            self.call("set_identity", name or "", address or "", -1 if class_of_device is None else class_of_device)
        )

    def peer(self) -> BluetoothIdentity | None:
        """The far device, whose name this half carries in the radio. None until the far half has said."""
        said = self.call("peer")
        return BluetoothIdentity.model_validate(said) if said else None

    def cli(self):
        base = super().cli()
        base.help = "The device's Bluetooth radio, as one half of a bridge to another radio"

        @base.command()
        @click.option("--name", help="The device's Bluetooth name")
        @click.option("--address", help="The device's own Bluetooth address, AA:BB:CC:DD:EE:FF")
        @click.option(
            "--class-of-device", "class_of_device", type=click.IntRange(0, 0xFFFFFF), help="Its class of device"
        )
        def identity(name: str | None, address: str | None, class_of_device: int | None):
            """What the device of this radio is on the air (and set it, with options)"""
            if name or address or class_of_device is not None:
                self.set_identity(name, address, class_of_device)
            click.echo(self.identity().model_dump_json(indent=2))

        @base.command()
        def peer():
            """The far device, whose name this half carries"""
            far = self.peer()
            click.echo(far.model_dump_json(indent=2) if far else "{}")

        return base
