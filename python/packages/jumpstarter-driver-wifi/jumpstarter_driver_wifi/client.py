import base64
import json
import time
from pathlib import Path

import click
from jumpstarter_driver_composite.client import CompositeClient


class WifiClient(CompositeClient):
    """Client for a device's Wi-Fi, whatever driver stands behind it.

    The methods are the same for every Wi-Fi driver. What a driver can do is in
    ``get_wifi_state()["capabilities"]``; a method it cannot do raises ``NotImplementedError`` and says what
    would provide it.

    A link's port is joined by the forwards of the lease, not from here: this starts and stops the local end
    and says how it is doing.
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

    def get_wifi_state(self) -> dict:
        """What is known of the device's Wi-Fi: ``capabilities``, ``adapter``, ``connections`` and ``link``.
        What the driver cannot know is None."""
        return self.call("get_wifi_state")

    def capabilities(self) -> list[str]:
        """What the driver behind this client can do."""
        return self.get_wifi_state()["capabilities"]

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

        This starts only the Wi-Fi it is called on. A far end that was stopped has to be started too, on its
        own member, before either is joined.
        """
        return self._wait_until("peer", timeout, "no far end joined (does the lease have a forward for this port?)")

    # Inspection. No Wi-Fi driver records yet: these are here so that a suite written now stays as it is.

    def start_capture(self) -> dict:
        """Start recording what is said on the device's Wi-Fi."""
        return self.call("start_capture")

    def stop_capture(self, restore: bool = False) -> dict:
        """Stop recording: what was recorded is kept for `read_capture` and `read_packets`."""
        return self.call("stop_capture", restore)

    def capture_status(self) -> dict:
        return self.call("capture_status")

    def read_capture(self, path: str | None = None, last: bool = False) -> bytes:
        """The recording, as a file a standard tool opens. ``path``: also write it there."""
        data = b"".join(base64.b64decode(chunk) for chunk in self.streamingcall("read_capture", last))
        if path is not None:
            Path(path).write_bytes(data)
        return data

    def read_packets(self, event: str = "", since: int = 0, **match) -> list[dict]:
        """The recorded packets, decoded. ``event`` narrows them to one name, ``match`` to those with these
        fields, ``since`` to those from that index on."""
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

    def cli(self):
        base = super().cli()
        base.help = "The device's Wi-Fi, as a port that the forwards of a lease join"

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
            """What is known of the device's Wi-Fi, and what this driver can do"""
            click.echo(json.dumps(self.get_wifi_state(), indent=2))

        return base
