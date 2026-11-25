import base64
import json
import time
from pathlib import Path

import click
from jumpstarter_driver_composite.client import CompositeClient

from .common import BluetoothIdentity


class BluetoothClient(CompositeClient):
    """Client for a device's Bluetooth, whatever driver stands behind it.

    The methods are the same for every Bluetooth driver. What a driver can do is in
    ``get_bluetooth_state()["capabilities"]``; a method it cannot do raises ``NotImplementedError`` and says what
    would provide it.

    A link's port is joined by the forwards of the lease, not from here: this starts and stops the local end,
    says how it is doing, and asks the device's own stack where a driver can.
    """

    def start(self) -> None:
        self.call("start")

    def stop(self) -> None:
        self.call("stop")

    def status(self) -> dict:  # ty: ignore[invalid-property-type-override]
        return self.call("status")

    def ports(self) -> list[dict]:
        """The ports the driver declares: name, direction, protocol, attachment and transport."""
        return self.call("declared_ports")

    def get_bluetooth_state(self) -> dict:
        """What is known of the device's Bluetooth: ``capabilities``, ``adapter`` (``enabled``, ``state``,
        ``name``, ``address``, ``class_of_device``), ``connections``, ``bonds`` and ``link``. What the driver
        cannot know is None."""
        return self.call("get_bluetooth_state")

    def capabilities(self) -> list[str]:
        """What the driver behind this client can do."""
        return self.get_bluetooth_state()["capabilities"]

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
                raise TimeoutError(f"{problem}: {status.get('last_error') or status.get('error') or status}")
            time.sleep(1)

    def wait_until_attached(self, timeout: float = 120) -> dict:
        """Start the local end and wait until it has the radio (the device is powered on)."""
        return self._wait_until("attached", timeout, "the radio was not found (is the device powered on?)")

    def wait_until_joined(self, timeout: float = 120) -> dict:
        """Start the local end and wait until a far end is joined to it (the forward's path is open).

        This starts only the Bluetooth it is called on. A far end that was stopped has to be started too, on
        its own member, before either is joined.
        """
        try:
            return self._wait_until("peer", timeout, "no far end joined (does the lease have a forward for this port?)")
        except TimeoutError as error:
            if (self.status().get("far") or {}).get("stopped"):
                raise TimeoutError(
                    "the far end is there, and its radio is stopped: start the radio of the other member "
                    "(the path between the two is open)"
                ) from error
            raise

    # A link that is one half of a bridge.

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

    def wait_until_paired(self, timeout: float = 120) -> dict:
        """Wait until both devices have a secure link to the bridge: the pairing is made on both sides (or
        two devices that were paired have reconnected)."""
        return self._wait_until("paired", timeout, "the devices are not paired")

    def wait_until_idle(self, timeout: float = 60) -> dict:
        """Wait until nothing is in flight: the half is not calling its device and no pairing is being made."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.status()
            if not status.get("calling") and not status.get("pairing"):
                return status
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"the radio is still busy ({status.get('state')}): {status.get('last_error') or status}"
                )
            time.sleep(0.5)

    # The device's own stack.

    def set_enabled(self, on: bool = True) -> dict:
        """Switch the device's Bluetooth on or off, and wait until it is. Gives back the state."""
        return self.call("set_enabled", on)

    def factory_reset(self) -> dict:
        """Reset the device's Bluetooth to what it was when new: every bond is forgotten. Gives back the state."""
        return self.call("factory_reset")

    # Inspection.

    def start_capture(self) -> dict:
        """Start recording what the device's Bluetooth says on its HCI. This may restart the device's
        Bluetooth (see the driver)."""
        return self.call("start_capture")

    def stop_capture(self, restore: bool = False) -> dict:
        """Stop recording: what was recorded is kept for `read_capture` and `read_packets`. ``restore`` puts
        the device back as it was before `start_capture`."""
        return self.call("stop_capture", restore)

    def capture_status(self) -> dict:
        return self.call("capture_status")

    def read_capture(self, path: str | None = None, last: bool = False) -> bytes:
        """The recording, as a btsnoop file (Wireshark opens it). It has everything in it, keys too.
        ``path``: also write it there. ``last``: the recording before the device's Bluetooth last restarted."""
        data = b"".join(base64.b64decode(chunk) for chunk in self.streamingcall("read_capture", last))
        if path is not None:
            Path(path).write_bytes(data)
        return data

    def read_packets(self, event: str = "", since: int = 0, **match) -> list[dict]:
        """The recorded packets since `start_capture`, decoded: ``index``, ``time``, ``direction``, ``type``,
        ``name`` and the packet's own fields, with link keys and the like left out. ``event`` narrows them to
        one name (``connection_complete`` will do for ``HCI_CONNECTION_COMPLETE_EVENT``), ``match`` to those
        with these fields, ``since`` to those from that index on."""
        return self.call("read_packets", event, match, since)

    def wait_for_event(self, event: str, timeout: float = 30.0, since: int = 0, **match) -> dict:
        """Wait until a packet of that name (and with those fields) is in the recording, and give it back."""
        found = self.call("wait_for_event", event, timeout, match, since)
        if not found:
            raise TimeoutError(f"no {event} {match or ''} was recorded in {timeout:g} seconds".replace("  ", " "))
        return found

    def should_not_have_event(self, event: str, within: float = 0.0, since: int = 0, **match) -> None:
        """Fail if a packet of that name (and with those fields) is in the recording, or comes ``within``
        seconds from now."""
        found = self.call("should_not_have_event", event, within, match, since)
        if found:
            raise AssertionError(f"{event} was recorded: {found}")

    def cli(self):  # noqa: C901
        base = super().cli()
        base.help = "The device's Bluetooth: its link to another device of the lease, and the device's own stack"

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
            """The ports this driver declares"""
            click.echo(json.dumps(self.ports(), indent=2))

        @base.command()
        def state():
            """What is known of the device's Bluetooth, and what this driver can do"""
            click.echo(json.dumps(self.get_bluetooth_state(), indent=2))

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

        @base.command()
        @click.argument("state", type=click.Choice(["on", "off"]))
        def power(state: str):
            """Switch the device's Bluetooth on or off"""
            click.echo(json.dumps(self.set_enabled(state == "on"), indent=2))

        @base.group()
        def capture():
            """Record what the device's Bluetooth says on its HCI"""

        @capture.command("start")
        def capture_start():
            """Start recording (this may restart the device's Bluetooth)"""
            click.echo(json.dumps(self.start_capture(), indent=2))

        @capture.command("stop")
        @click.option("--restore", is_flag=True, help="Put the device back as it was before the recording")
        def capture_stop(restore: bool):
            """Stop recording, and keep what was recorded"""
            click.echo(json.dumps(self.stop_capture(restore), indent=2))

        @capture.command("status")
        def capture_state():
            """Whether it is recording, and how"""
            click.echo(json.dumps(self.capture_status(), indent=2))

        @capture.command("save")
        @click.argument("path", type=click.Path(dir_okay=False, writable=True))
        @click.option("--last", is_flag=True, help="The recording before the device's Bluetooth last restarted")
        def capture_save(path: str, last: bool):
            """Write the recording to PATH as a btsnoop file (it has keys in it)"""
            click.echo(f"{len(self.read_capture(path, last))} bytes")

        @capture.command("packets")
        @click.option("--event", default="", help="Only packets of this name")
        def capture_packets(event: str):
            """Print the recorded packets, decoded, without keys"""
            for packet in self.read_packets(event):
                click.echo(json.dumps(packet))

        return base


class MockBluetoothClient(BluetoothClient):
    """Client for a Bluetooth with nothing behind it."""

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
