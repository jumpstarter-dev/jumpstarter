import json
from base64 import b64decode

import click

from jumpstarter.client import DriverClient


def _parse(raw: str) -> dict | list | str:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _echo(obj) -> None:
    if isinstance(obj, (dict, list)):
        click.echo(json.dumps(obj, indent=2))
    else:
        click.echo(obj)


class NetsimClient(DriverClient):
    """Client for controlling Android netsim virtual radios."""

    def list_devices(self) -> dict | list | str:
        return _parse(self.call("list_devices"))

    def get_device(self, name_or_id: str) -> dict | list | str:
        return _parse(self.call("get_device", name_or_id))

    def patch_device(self, name_or_id: str, patch_json: str) -> dict | list | str:
        return _parse(self.call("patch_device", name_or_id, patch_json))

    def set_radio(self, name_or_id: str, radio: str, enabled: str) -> dict | list | str:
        return _parse(self.call("set_radio", name_or_id, radio, enabled))

    def reset_devices(self) -> str:
        return self.call("reset_devices")

    def list_captures(self) -> dict | list | str:
        return _parse(self.call("list_captures"))

    def get_capture(self, capture_id: str) -> bytes:
        return b64decode(self.call("get_capture", capture_id))

    def set_capture(self, capture_id: str, enabled: str) -> dict | list | str:
        return _parse(self.call("set_capture", capture_id, enabled))

    def start_capture(self, name_or_id: str, chip_kind: str = "BLUETOOTH") -> str:
        return self.call("start_capture", name_or_id, chip_kind)

    def stop_capture(self, capture_id: str) -> str:
        return self.call("stop_capture", capture_id)

    def status(self) -> str:
        return self.call("status")

    def cli(self):  # noqa: C901
        @click.group()
        def netsim():
            """Android netsim virtual radio control.

            Manage virtual Bluetooth, WiFi, and UWB radios:
            toggle state, capture packets.
            """

        @netsim.command("devices")
        def devices_cmd():
            """List all devices and radio states."""
            _echo(self.list_devices())

        @netsim.command("device")
        @click.argument("name")
        def device_cmd(name: str):
            """Show a single device by name or ID."""
            _echo(self.get_device(name))

        @netsim.command("radio")
        @click.argument("device")
        @click.argument("radio_type", type=click.Choice(["bt_classic", "ble", "wifi", "uwb"]))
        @click.argument("state", type=click.Choice(["on", "off"]))
        def radio_cmd(device: str, radio_type: str, state: str):
            """Toggle a radio on or off."""
            _echo(self.set_radio(device, radio_type, state))

        @netsim.command("patch")
        @click.argument("device")
        @click.argument("patch_json")
        def patch_cmd(device: str, patch_json: str):
            """Send raw JSON patch to a device."""
            _echo(self.patch_device(device, patch_json))

        @netsim.command("reset")
        def reset_cmd():
            """Reset all devices to initial state."""
            click.echo(self.reset_devices())

        @netsim.group("capture")
        def capture_group():
            """Manage packet captures."""

        @capture_group.command("list")
        def capture_list():
            """List all captures."""
            _echo(self.list_captures())

        @capture_group.command("start")
        @click.argument("device")
        @click.option("--chip", default="BLUETOOTH", help="Chip kind (BLUETOOTH, UWB)")
        def capture_start(device: str, chip: str):
            """Start packet capture for a device."""
            cap_id = self.start_capture(device, chip)
            click.echo(f"Capture started (id={cap_id})")

        @capture_group.command("stop")
        @click.argument("capture_id")
        def capture_stop(capture_id: str):
            """Stop a packet capture."""
            click.echo(self.stop_capture(capture_id))

        @capture_group.command("get")
        @click.argument("capture_id")
        @click.option("-o", "--output", required=True, help="Output file path")
        def capture_get(capture_id: str, output: str):
            """Download a packet capture to a file."""
            data = self.get_capture(capture_id)
            with open(output, "wb") as f:
                f.write(data)
            click.echo(f"Wrote {len(data)} bytes to {output}")

        @netsim.command("status")
        def status_cmd():
            """Health check shows device count."""
            click.echo(self.status())

        return netsim
