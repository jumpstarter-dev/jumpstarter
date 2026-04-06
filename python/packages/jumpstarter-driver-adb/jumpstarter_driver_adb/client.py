import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from threading import Event
from typing import Generator

import click
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client import DriverClient

_UNSUPPORTED_ADB_COMMANDS = frozenset({"nodaemon"})

_TUNNEL_STATE_FILE = os.path.join(tempfile.gettempdir(), "jumpstarter-adb-tunnel.json")


def _validate_adb_args(args: tuple[str, ...]) -> None:
    """Validate adb command arguments, raising UsageError for unsupported commands."""
    for arg in args:
        if arg in _UNSUPPORTED_ADB_COMMANDS:
            raise click.UsageError(f"'{arg}' is not supported through the Jumpstarter ADB tunnel")


def _read_tunnel_state() -> dict | None:
    """Read the tunnel state file and verify the tunnel process is still alive."""
    try:
        with open(_TUNNEL_STATE_FILE) as f:
            state = json.load(f)
        # Verify the tunnel process is still running
        os.kill(state["pid"], 0)
        return state
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        return None


def _write_tunnel_state(host: str, port: int) -> None:
    """Write the tunnel state file with current process info."""
    with open(_TUNNEL_STATE_FILE, "w") as f:
        json.dump({"host": host, "port": str(port), "pid": os.getpid()}, f)


def _remove_tunnel_state() -> None:
    """Remove the tunnel state file."""
    try:
        os.unlink(_TUNNEL_STATE_FILE)
    except FileNotFoundError:
        pass


class AdbClient(DriverClient):
    """Client for tunneling ADB connections through Jumpstarter."""

    @contextmanager
    def forward_adb(self, host: str = "127.0.0.1", port: int = 0) -> Generator[tuple[str, int], None, None]:
        """Forward remote ADB server to a local TCP port.

        Args:
            host: Local bind address (default: 127.0.0.1)
            port: Local port (default: 0 = auto-assign, use 5037 to replace local ADB)

        Yields:
            Tuple of (host, port) of the local listener.
        """
        with TcpPortforwardAdapter(
            client=self,
            local_host=host,
            local_port=port,
        ) as addr:
            yield addr

    def start_server(self) -> int:
        """Start ADB server on the exporter."""
        return self.call("start_server")

    def kill_server(self) -> int:
        """Kill ADB server on the exporter."""
        return self.call("kill_server")

    def list_devices(self) -> str:
        """List devices visible to the exporter's ADB server."""
        return self.call("list_devices")

    def cli(self):
        @click.command(context_settings={"ignore_unknown_options": True})
        @click.option(
            "-H",
            "host",
            default="127.0.0.1",
            show_default=True,
            help="Local address to tunnel ADB to",
        )
        @click.option(
            "-P",
            "port",
            type=int,
            default=0,
            show_default=True,
            help="Local port to tunnel ADB to (0=auto)",
        )
        @click.option(
            "--adb",
            default="adb",
            show_default=True,
            help="Path to local adb executable",
        )
        @click.argument("args", nargs=-1)
        def adb(host: str, port: int, adb: str, args: tuple[str, ...]):
            """ADB tunneling and device access.

            Wraps the local adb binary to work against a remote ADB server
            tunneled through Jumpstarter. The exporter's ADB server is
            automatically tunneled to a local port, and environment variables
            ANDROID_ADB_SERVER_ADDRESS and ANDROID_ADB_SERVER_PORT are set so
            the local adb binary communicates through the tunnel.

            All standard adb commands (shell, install, push, pull, logcat,
            start-server, kill-server, connect, disconnect, etc.) are passed
            through directly to the remote ADB server.

            If a persistent tunnel is already running (from a previous
            `j adb tunnel`), commands will reuse it instead of creating
            a new ephemeral tunnel.

            \b
            Jumpstarter-specific commands:
              tunnel    Create a persistent ADB tunnel to a local port
                        (auto-assigned by default, use -P to pick a specific
                        port). Other j adb commands will automatically reuse
                        the tunnel. For native adb or external tools, export
                        the env vars printed by the command.

            \b
            Unsupported commands:
              nodaemon  Not supported (would start a local server, ignoring
                        the tunnel).
            """
            if not args or (len(args) == 1 and args[0] == "help"):
                click.echo(click.get_current_context().get_help())
                click.echo("\n" + "=" * 60)
                click.echo("ADB built-in help (from local adb binary):")
                click.echo("=" * 60 + "\n")
                subprocess.run([adb, "help"], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
                return 0

            _validate_adb_args(args)

            if args[0] == "tunnel":
                state = _read_tunnel_state()
                if state:
                    # If a specific port was requested, check it matches the running tunnel
                    if port != 0 and (state["host"] != host or int(state["port"]) != port):
                        click.echo(
                            f"Error: tunnel already running (PID {state['pid']}) "
                            f"on {state['host']}:{state['port']}, "
                            f"cannot bind to {host}:{port}",
                            err=True,
                        )
                        return 1
                    click.echo(f"Tunnel already running (PID {state['pid']}) on {state['host']}:{state['port']}")
                    return 0

                with self.forward_adb(host, port) as addr:
                    _write_tunnel_state(addr[0], addr[1])
                    try:
                        click.echo(f"ADB server tunneled to {addr[0]}:{addr[1]}")
                        click.echo("")
                        click.echo("To use native adb or other tools, run:")
                        click.echo(f"  export ANDROID_ADB_SERVER_ADDRESS={addr[0]}")
                        click.echo(f"  export ANDROID_ADB_SERVER_PORT={addr[1]}")
                        click.echo("")
                        click.echo("Press Ctrl+C to stop")
                        Event().wait()
                    finally:
                        _remove_tunnel_state()
                return 0

            # Check if a persistent tunnel is already running
            state = _read_tunnel_state()
            if state:
                env = os.environ | {
                    "ANDROID_ADB_SERVER_ADDRESS": state["host"],
                    "ANDROID_ADB_SERVER_PORT": state["port"],
                }
                process = subprocess.Popen(
                    [adb, *args],
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    env=env,
                )
                return process.wait()

            # No persistent tunnel — create an ephemeral one
            with self.forward_adb(host, port) as addr:
                env = os.environ | {
                    "ANDROID_ADB_SERVER_ADDRESS": addr[0],
                    "ANDROID_ADB_SERVER_PORT": str(addr[1]),
                }
                process = subprocess.Popen(
                    [adb, *args],
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    env=env,
                )
                return process.wait()

        return adb
