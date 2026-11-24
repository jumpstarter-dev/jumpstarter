"""Export of a lease with members as a Mobly testbed file."""

import os
import socket

import click
import yaml

from jumpstarter.client.base import DriverClient
from jumpstarter.client.lease import LostMember
from jumpstarter.config.env import JMP_LEASE, JMP_LEASE_MEMBERS
from jumpstarter.utils.env import env_members

# The client class that an exporter reports for one Android device with ADB.
# It is matched by name: the driver package is not a dependency of the CLI.
ADB_DEVICE_CLIENT = "jumpstarter_driver_adb.client.AdbDeviceClient"
_CLIENT_LABEL = "jumpstarter.dev/client"


def find_adb_devices(client: DriverClient, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """The paths of the ADB device drivers in a client tree, in the order of the tree."""
    found = []
    if client.labels.get(_CLIENT_LABEL) == ADB_DEVICE_CLIENT:
        found.append(path)
    for name, child in client.children.items():
        found.extend(find_adb_devices(child, (*path, name)))
    return found


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_testbed(lease_name: str, devices: list[tuple[str, str]]) -> dict:
    """A Mobly config with one testbed, named after the lease.

    devices is a list of (role, serial). The role is the `label` of its
    device, so a test selects a device with `get_devices(ads, label=ROLE)`.
    """
    return {
        "TestBeds": [
            {
                "Name": lease_name,
                "Controllers": {
                    "AndroidDevice": [{"serial": serial, "label": role} for role, serial in devices],
                },
            }
        ]
    }


def export_mobly_testbed(lease_name: str) -> str:
    """Write the Mobly testbed of the lease of the current shell.

    It has one Android device for each member that has an ADB device driver.
    The serial of a device is a local address: the ADB driver gives a device a
    local endpoint only while `j ROLE ... connect` runs, so each device gets a
    free local port here, and the command that connects it there goes to stderr.
    """
    if not os.environ.get(JMP_LEASE_MEMBERS) or os.environ.get(JMP_LEASE) != lease_name:
        raise click.UsageError(
            f"-o mobly needs the connections of the lease: run it in 'jmp shell --lease {lease_name}'"
        )

    devices: list[tuple[str, str]] = []
    commands: list[str] = []
    with env_members() as members:
        for role, client in members.by_role.items():
            if client is None:
                continue
            if isinstance(client, LostMember):
                # A degraded lease keeps its other members: the testbed has the devices that are left.
                click.echo(f"Warning: the member '{role}' lost its exporter; the testbed does not have it", err=True)
                continue
            paths = find_adb_devices(client)
            if not paths:
                continue
            if len(paths) > 1:
                click.echo(
                    f"Warning: the member '{role}' has {len(paths)} ADB devices; the testbed has the first, "
                    f"'{'.'.join(paths[0])}'",
                    err=True,
                )
            port = _free_local_port()
            devices.append((role, f"127.0.0.1:{port}"))
            commands.append(" ".join(["j", role, *paths[0], "connect", "-P", str(port)]))

    if not devices:
        raise click.ClickException(f"no member of lease {lease_name} has an ADB device driver")

    click.echo("Connect each device to your ADB server in this shell before you start the Mobly suite:", err=True)
    for command in commands:
        click.echo(f"  {command} &", err=True)
    return yaml.safe_dump(build_testbed(lease_name, devices), sort_keys=False)
