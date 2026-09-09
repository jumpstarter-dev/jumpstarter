import asyncio
import subprocess
from contextlib import contextmanager
from typing import Generator

import anyio
import click
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client import DriverClient

#: Seconds to allow the **local** ``adb connect`` when attaching.
#:
#: Deliberately separate from the driver's ``connect_timeout``, which bounds adb calls
#: on the *exporter*. This one runs on the developer's machine against a local
#: port-forward, so it is not the exporter's business to configure. It is generous
#: because it also covers `adb connect` starting a local ADB server from cold, which
#: is slow on a first run; a healthy connect to a local port returns in milliseconds.
#: Override per call with ``attach(timeout=...)``.
ADB_CONNECT_TIMEOUT = 60.0

#: Seconds to allow the local ``adb disconnect`` during teardown. Shorter than the
#: connect: nothing has to be started, and teardown must not hang on a wedged adb.
ADB_DISCONNECT_TIMEOUT = 30.0


def _is_cancelled(exc: BaseException) -> bool:
    """Whether *exc* is a task cancellation.

    Checked without ``anyio.get_cancelled_exc_class()``, which resolves the *running*
    backend and raises ``NoEventLoopError`` when there is none. These waits run in a
    worker thread, off the loop, so asking there would raise from the except arm and
    mask the very cancellation being handled -- reproduced as a test failure.

    Both backends' cancellations are matched directly: asyncio's ``CancelledError``
    (which trio's also subclasses on recent versions) and trio's ``Cancelled`` by
    name, so trio need not be installed.
    """
    if isinstance(exc, asyncio.CancelledError):
        return True
    return type(exc).__name__ == "Cancelled" and type(exc).__module__.startswith("trio")


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
        # Cancellation derives from BaseException, not Exception, so it needs its
        # own arm.
        if _is_cancelled(e):
            return
        raise


def _adb_connect(adb: str, target: str, *, timeout: float = ADB_CONNECT_TIMEOUT) -> str:
    """Run ``adb connect target``, raising if it did not actually connect.

    The exit status cannot be used: ``adb connect`` returns 0 even when it fails,
    reporting the failure on **stdout** instead ("failed to connect to ...", "failed
    to resolve host: ...", "bad port number ..."). Verified against adb 1.0.41, for
    a refused port, an unresolvable host and an out-of-range port — all rc=0. So a
    `check=True` here would silently accept a device that never attached, leaving the
    caller to believe it had one.

    A local ADB server is *not* required: if none is running, ``adb connect`` starts
    one on 5037 first (verified — it does so even when the connect itself then fails).
    That is the whole point of attach — the developer does not have to own, configure,
    or even have an ADB server.

    Args:
        adb: path to the local adb binary.
        target: the local ``host:port`` to connect to.
        timeout: seconds to allow. Defaults to ``ADB_CONNECT_TIMEOUT``, a client-side
        setting rather than the driver's ``connect_timeout``.

    Returns:
        adb's own message, for logging.

    Raises:
        RuntimeError: adb reported a failure, or timed out.
    """
    try:
        result = subprocess.run([adb, "connect", target], check=False, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise RuntimeError(f"`adb connect {target}` failed: {e}") from e

    message = (result.stdout or "").strip() or (result.stderr or "").strip()
    # Matched against adb's own format strings, which are the only two successes:
    # "connected to %s" and "already connected to %s". The failures are
    # "failed to connect to ...", "bad port number ...", "cannot connect to daemon ...".
    if result.returncode != 0 or not message.startswith(("connected to", "already connected to")):
        raise RuntimeError(f"`adb connect {target}` did not connect: {message or 'no output'}")
    return message


class AdbClient(DriverClient):
    """Client for the exporter's ADB server.

    Use this to point your own tooling *at* the exporter's ADB server, which is
    exclusive — you see the exporter's devices instead of your own. To add a single
    remote device to an ADB server you already run, use an ``AdbDevice`` and its
    ``attach`` instead.
    """

    @contextmanager
    def forward_adb(self, host: str = "127.0.0.1", port: int = 0) -> Generator[tuple[str, int], None, None]:
        """Forward the exporter's ADB server to a local TCP port.

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
        """Connect the exporter's ADB server to a device by address (host:port)."""
        return self.call("connect_device", device)

    def disconnect_device(self, device: str) -> str:
        """Disconnect an ADB device by address (host:port)."""
        return self.call("disconnect_device", device)

    def list_devices(self) -> str:
        """List devices visible to the exporter's ADB server."""
        return self.call("list_devices")

    def devices(self) -> list[str]:
        """Return the serials of usable devices on the exporter."""
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

    def cli(self):
        """Build the `j adb` command group."""

        @click.group()
        def adb():
            """The exporter's ADB server.

            Jumpstarter does not wrap the adb CLI: use your own adb against the
            endpoint these commands give you.
            """

        @adb.command()
        def devices():
            """List devices visible to the exporter's ADB server."""
            click.echo(self.list_devices().rstrip())

        @adb.command()
        @click.option("-H", "host", default="127.0.0.1", show_default=True, help="Local address to bind")
        @click.option("-P", "port", type=int, default=0, show_default=True, help="Local port to bind (0=auto)")
        def tunnel(host: str, port: int):
            """Forward the exporter's ADB server to a local port, and hold.

            Point your own adb at it with the environment variables printed below.
            """
            with self.forward_adb(host, port) as addr:
                click.echo(f"ADB server tunneled to {addr[0]}:{addr[1]}")
                click.echo("")
                click.echo("To use your own adb or other tools, run:")
                click.echo(f"  export ANDROID_ADB_SERVER_ADDRESS={addr[0]}")
                click.echo(f"  export ANDROID_ADB_SERVER_PORT={addr[1]}")
                click.echo("")
                click.echo("Press Ctrl+C to stop")
                _wait_for_interrupt(self)
            return 0

        return adb


class AdbDeviceClient(DriverClient):
    """Client for one declared Android device on the exporter.

    Exposes the device's adbd as a local TCP endpoint. What you do with that endpoint
    is up to your own adb: ``attach`` runs a single ``adb connect`` for convenience,
    and ``endpoint`` just prints the address so you can drive adb yourself.
    """

    def info(self) -> dict:
        """Describe the device: transport, selector, and whether it is present."""
        return self.call("info")

    @contextmanager
    def endpoint(self, host: str = "127.0.0.1", port: int = 0) -> Generator[str, None, None]:
        """Expose the device's adbd on a local TCP port.

        No adb is involved. This is the primitive: Jumpstarter moves the ADB protocol
        between the two machines, and your own tooling does the rest.

        Args:
            host: local bind address.
            port: local port; 0 lets the OS choose.

        Yields:
            The ``host:port`` the device's adbd is reachable at.
        """
        with TcpPortforwardAdapter(client=self, local_host=host, local_port=port) as addr:
            yield f"{addr[0]}:{addr[1]}"

    @contextmanager
    def attach(
        self,
        *,
        adb: str = "adb",
        host: str = "127.0.0.1",
        port: int = 0,
        timeout: float = ADB_CONNECT_TIMEOUT,
    ) -> Generator[str, None, None]:
        """Add this device to the ADB server your machine already uses.

        Three steps, none of them clever: the exporter streams the device's adbd,
        Jumpstarter tunnels it here, and plain ``adb connect`` adds it to the local
        server. Because ``adb connect`` is additive, the device lands in the *default*
        server — the one Android Studio, tradefed, gradle and a bare ``adb`` all talk
        to — with no environment variables and no IDE restart. If you have no ADB
        server at all, ``adb connect`` starts one.

        Args:
            adb: path to your local adb binary.
            host: local bind address.
            port: local port to bind; 0 lets the OS choose. The device's address is
            whatever this resolves to — deliberately not something this driver
            invents, since ADB owns device addressing.
            timeout: seconds to allow the local ``adb connect``. A client-side
            timeout, distinct from the exporter's ``connect_timeout``, because it
            bounds a command on your machine against a local port-forward.

        Yields:
            The ``host:port`` the device was attached as.
        """
        with self.endpoint(host=host, port=port) as target:
            _adb_connect(adb, target, timeout=timeout)
            try:
                yield target
            finally:
                # Leave no stale `offline` entry in the developer's ADB server. Bounded
                # and swallowed: teardown must not hang, and must not mask the session.
                try:
                    subprocess.run(
                        [adb, "disconnect", target],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=ADB_DISCONNECT_TIMEOUT,
                    )
                except (subprocess.SubprocessError, OSError) as e:
                    self.logger.debug("disconnect %s failed: %s", target, e)

    def cli(self):
        """Build the per-device command group."""

        @click.group()
        def adb():
            """One Android device on the exporter.

            Jumpstarter does not wrap the adb CLI. Use `attach` to add this device to
            your own ADB server, then run your own `adb -s <address> ...`.
            """

        @adb.command()
        def info():
            """Show the device's transport, selector and presence."""
            for key, value in self.info().items():
                click.echo(f"{key}: {value}")

        @adb.command()
        @click.option("--adb", "adb_path", default="adb", show_default=True, help="Path to your local adb")
        @click.option("-H", "host", default="127.0.0.1", show_default=True, help="Local address to bind")
        @click.option("-P", "port", type=int, default=0, show_default=True, help="Local port to bind (0=auto)")
        def attach(adb_path: str, host: str, port: int):
            """Add this device to your own ADB server, and hold until Ctrl+C."""
            with self.attach(adb=adb_path, host=host, port=port) as target:
                click.echo(f"attached as {target}")
                click.echo("")
                click.echo(f"Your ADB server now lists it; use it with:  adb -s {target} shell")
                click.echo("Android Studio will list it too.")
                click.echo("")
                click.echo("Press Ctrl+C to detach")
                _wait_for_interrupt(self)
            click.echo("detached")
            return 0

        @adb.command()
        @click.option("-H", "host", default="127.0.0.1", show_default=True, help="Local address to bind")
        @click.option("-P", "port", type=int, default=0, show_default=True, help="Local port to bind (0=auto)")
        def endpoint(host: str, port: int):
            """Print the device's local adbd address, and hold until Ctrl+C.

            For driving adb yourself, or for tools that take a host:port.
            """
            with self.endpoint(host=host, port=port) as target:
                click.echo(target)
                click.echo("")
                click.echo(f"Add it to your ADB server with:  adb connect {target}")
                click.echo("Press Ctrl+C to stop")
                _wait_for_interrupt(self)
            return 0

        return adb
