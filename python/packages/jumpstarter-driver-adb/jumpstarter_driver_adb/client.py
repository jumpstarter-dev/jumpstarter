import json
import os
import socket
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from typing import Generator

import anyio
import click
from anyio import get_cancelled_exc_class
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client import DriverClient

_UNSUPPORTED_ADB_COMMANDS = frozenset({"nodaemon"})

_TUNNEL_STATE_FILE = os.path.join(tempfile.gettempdir(), "jumpstarter-adb-tunnel.json")


def _validate_adb_args(args: tuple[str, ...]) -> None:
    """Validate adb command arguments, raising UsageError for unsupported commands."""
    for arg in args:
        if arg in _UNSUPPORTED_ADB_COMMANDS:
            raise click.UsageError(f"'{arg}' is not supported through the Jumpstarter ADB tunnel")


def _wait_for_interrupt(client: DriverClient) -> None:
    """Block until the CLI is interrupted, then return so teardown can run.

    The wait must happen **in the event loop**, not in this thread.

    Driver CLIs run in a worker thread driven by a ``BlockingPortal``, while
    ``jmp shell`` handles Ctrl+C with ``anyio.open_signal_receiver`` and cancels
    the enclosing task group. A thread-side wait — ``Event().wait()``,
    ``time.sleep()``, or ``signal.signal()`` — cannot observe either: Python
    delivers signals only to the main thread, and anyio cancellation only unwinds
    tasks. So the CLI printed "SIGINT pressed, terminating" while the worker
    thread kept waiting, the caller's ``finally`` never ran (leaving a stale
    ``adb connect`` entry behind), and a second Ctrl+C hung in
    ``threading._shutdown``.

    Sleeping through the portal puts the wait in a real task, so the cancel scope
    unwinds it and ``portal.call`` re-raises here, letting teardown proceed.
    """
    try:
        client.portal.call(anyio.sleep_forever)
    except (KeyboardInterrupt, SystemExit, GeneratorExit, RuntimeError):
        # RuntimeError covers the portal already being shut down when we ask.
        return
    except BaseException as e:
        # anyio's cancelled exception derives from BaseException, not Exception.
        if type(e) is get_cancelled_exc_class():
            return
        raise


def _read_tunnel_state() -> dict | None:
    """Return the recorded tunnel, or None if it is not actually usable.

    Liveness is decided by **connecting to the port**, not by checking the
    process. A `j adb tunnel` orphaned by its parent shell keeps running and stays
    reparented to init, so ``os.kill(pid, 0)`` succeeds long after the lease that
    carried the tunnel is gone — and then every later ``j adb`` command reuses a
    port with nothing behind it and fails with "cannot connect to daemon at
    tcp:127.0.0.1:<port>". Observed on macOS with a tunnel orphaned hours earlier.

    The process check is kept as a cheap first filter, and a stale file is removed
    so the next invocation falls straight through to an ephemeral tunnel.
    """
    try:
        with open(_TUNNEL_STATE_FILE) as f:
            state = json.load(f)
        os.kill(state["pid"], 0)
        host, port = state["host"], int(state["port"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError, OSError):
        _remove_tunnel_state()
        return None

    # The authoritative check: is anything accepting connections there?
    try:
        with socket.create_connection((host, port), timeout=2):
            return state
    except OSError:
        _remove_tunnel_state()
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

    def connect_device(self, device: str) -> str:
        """Connect to an ADB device by address (host:port)."""
        return self.call("connect_device", device)

    def disconnect_device(self, device: str) -> str:
        """Disconnect an ADB device by address (host:port)."""
        return self.call("disconnect_device", device)

    def list_devices(self) -> str:
        """List devices visible to the exporter's ADB server."""
        return self.call("list_devices")

    def devices(self) -> list[str]:
        """Return the serials of usable devices on the exporter.

        Read live on every call, so a device plugged in — or an emulator started —
        after the lease began is included. The exporter's ADB server is the
        inventory; this driver keeps no device list of its own.
        """
        serials = []
        for line in self.list_devices().splitlines():
            line = line.strip()
            if not line or line.startswith("*") or line.startswith("List of devices"):
                continue
            fields = line.split()
            # Only `device`; offline/unauthorized cannot be forwarded.
            if len(fields) >= 2 and fields[1] == "device":
                serials.append(fields[0])
        return serials

    # Exporter-side slot plumbing. Private: `attach()` is the interface, and
    # calling these directly means managing forwards and tunnels by hand.

    def _attach_device(self, device: str, adbd_port: int = 5555) -> str:
        """Publish a device's adbd on an exporter forward slot; return the slot name."""
        return self.call("attach_device", device, adbd_port)

    def _detach_device(self, device: str) -> None:
        """Release a device's forward slot on the exporter."""
        self.call("detach_device", device)

    def _list_attached(self) -> dict:
        """Return ``{slot_port: device}`` for devices attached on the exporter."""
        return self.call("list_attached")

    @contextmanager
    def attach(
        self,
        device: str,
        *,
        adbd_port: int = 5555,
        adb: str = "adb",
        local_port: int = 0,
    ) -> Generator[str, None, None]:
        """Add a remote device to the ADB server this machine already uses.

        Three steps, none of them clever: the exporter forwards the device's adbd
        onto a slot, Jumpstarter tunnels that slot here, and plain ``adb connect``
        adds it to the local server. Because ``adb connect`` is additive, the
        device lands in the *default* server — the one Android Studio, tradefed,
        gradle, and a bare ``adb`` all talk to — with no environment variables, no
        ``adb.server.port``, and no IDE restart.

        Args:
            device: ADB serial from :meth:`devices`.
            adbd_port: adbd's TCP port on the device.
            adb: path to the local adb binary.
            local_port: local port to bind; 0 lets the OS choose. The device's
                address is whatever this resolves to — deliberately not something
                this driver invents, since ADB owns device addressing.

        Yields:
            The ``host:port`` the device was attached as.
        """
        slot = self._attach_device(device, adbd_port)
        with TcpPortforwardAdapter(client=self.children[slot], local_port=local_port) as addr:
            target = f"{addr[0]}:{addr[1]}"
            subprocess.run([adb, "connect", target], check=True, capture_output=True, text=True, timeout=60)
            try:
                yield target
            finally:
                # Leave no stale `offline` entry in the developer's ADB server.
                subprocess.run([adb, "disconnect", target], check=False, capture_output=True, text=True, timeout=30)
                try:
                    self._detach_device(device)
                except Exception as e:  # noqa: BLE001 - teardown is best-effort
                    self.logger.debug("detach %s failed: %s", device, e)

    def _cli_attach(self, targets: list[str], *, adb: str, local_port: int) -> int:
        """Body of `j adb attach`. Attaches every target, then blocks until Ctrl+C."""
        if not targets:
            # Whatever the exporter's ADB server sees right now, including anything
            # hotplugged since the lease began.
            targets = self.devices()
        if not targets:
            click.echo("No usable devices on the exporter.", err=True)
            return 1

        with ExitStack() as stack:
            for device in targets:
                try:
                    attached = stack.enter_context(self.attach(device, adb=adb, local_port=local_port))
                except (RuntimeError, subprocess.CalledProcessError) as e:
                    click.echo(f"error: could not attach {device}: {e}", err=True)
                    return 1
                click.echo(f"{device} -> {attached}")
            click.echo("\nAttached to your local ADB server; Android Studio will list them.")
            click.echo("Press Ctrl+C to detach.")
            _wait_for_interrupt(self)
        click.echo("detached")
        return 0

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
              attach    Add the exporter's devices to the ADB server this machine
                        already uses, via plain `adb connect`. Works when you do
                        NOT own that server -- Android Studio keeps 5037 and
                        respawns it in ~3s if killed, so it cannot be taken over.
                        Devices appear in Studio's chooser with no configuration.
                        Defaults to every usable device; name serials to pick.
                        Blocks until Ctrl+C, then detaches cleanly.
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

            if args[0] == "attach":
                serials = [a for a in args[1:] if not a.startswith("-")]
                return self._cli_attach(serials, adb=adb, local_port=port)

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
                        _wait_for_interrupt(self)
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

            # No persistent tunnel - create an ephemeral one
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
