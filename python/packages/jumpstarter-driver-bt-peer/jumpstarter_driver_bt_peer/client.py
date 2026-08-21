import json
from typing import Any

import click

from jumpstarter.client import DriverClient


def _parse(raw: str) -> dict[str, Any] | list[Any] | str:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _parse_dict(raw: str) -> dict[str, Any]:
    result = _parse(raw)
    if not isinstance(result, dict):
        raise ValueError(f"expected dict, got {type(result).__name__}: {raw!r}")
    return result


def _parse_list(raw: str) -> list[Any]:
    result = _parse(raw)
    if not isinstance(result, list):
        raise ValueError(f"expected list, got {type(result).__name__}: {raw!r}")
    return result


def _echo(obj: object) -> None:
    if isinstance(obj, (dict, list)):
        click.echo(json.dumps(obj, indent=2))
    else:
        click.echo(obj)


class BtPeerClient(DriverClient):
    """Client for the Bluetooth peer driver."""

    def start_peer(self, config_json: str = "{}") -> dict[str, Any]:
        return _parse_dict(self.call("start_peer", config_json))

    def stop_peer(self) -> dict[str, Any]:
        return _parse_dict(self.call("stop_peer"))

    def wait_connection(self, timeout: int = 30) -> dict[str, Any]:
        return _parse_dict(self.call("wait_connection", timeout))

    def wait_disconnection(self, timeout: int = 30) -> dict[str, Any]:
        return _parse_dict(self.call("wait_disconnection", timeout))

    def get_events(self, since: str = "0") -> list[Any]:
        return _parse_list(self.call("get_events", since))

    def get_address(self) -> str:
        return self.call("get_address")

    def pair(self, handle: int = 0) -> dict[str, Any]:
        return _parse_dict(self.call("pair", handle))

    def connect_to(self, address: str, timeout: int = 30) -> dict[str, Any]:
        return _parse_dict(self.call("connect_to", address, timeout))

    def get_connections(self) -> list[Any]:
        return _parse_list(self.call("get_connections"))

    def cli(self):  # noqa: C901
        @click.group()
        def bt_peer():
            """Bluetooth peer device (bumble)."""

        @bt_peer.command("start")
        @click.argument("config", default="{}")
        def start_cmd(config: str):
            """Start the BT peer device.

            CONFIG is JSON: {"name": "...", "classic_enabled": true, "class_of_device": 123}
            """
            try:
                parsed = json.loads(config)
            except json.JSONDecodeError as exc:
                raise click.BadParameter(
                    f"CONFIG must be valid JSON: {exc.msg}",
                    param_hint="CONFIG",
                ) from exc
            if not isinstance(parsed, dict):
                raise click.BadParameter(
                    "CONFIG must be a JSON object",
                    param_hint="CONFIG",
                )
            _echo(self.start_peer(config))

        @bt_peer.command("stop")
        def stop_cmd():
            """Stop the BT peer device."""
            _echo(self.stop_peer())

        @bt_peer.command("wait-connection")
        @click.option("--timeout", "-t", default=30, help="Timeout in seconds")
        def wait_connection_cmd(timeout: int):
            """Wait for an incoming connection."""
            _echo(self.wait_connection(timeout))

        @bt_peer.command("wait-disconnection")
        @click.option("--timeout", "-t", default=30, help="Timeout in seconds")
        def wait_disconnection_cmd(timeout: int):
            """Wait for a disconnection."""
            _echo(self.wait_disconnection(timeout))

        @bt_peer.command("events")
        @click.option("--since", default="0", help="Timestamp filter")
        def events_cmd(since: str):
            """Show events since timestamp."""
            _echo(self.get_events(since))

        @bt_peer.command("address")
        def address_cmd():
            """Show the peer's Bluetooth address."""
            click.echo(self.get_address())

        @bt_peer.command("pair")
        @click.option("--handle", "-h", default=0, help="Connection handle")
        def pair_cmd(handle: int):
            """Authenticate and encrypt a connection."""
            _echo(self.pair(handle))

        @bt_peer.command("connect")
        @click.argument("address")
        @click.option("--timeout", "-t", default=30, help="Timeout in seconds")
        def connect_cmd(address: str, timeout: int):
            """Connect to a remote device (e.g. the CVD)."""
            _echo(self.connect_to(address, timeout))

        @bt_peer.command("connections")
        def connections_cmd():
            """Show active connections."""
            _echo(self.get_connections())

        return bt_peer
