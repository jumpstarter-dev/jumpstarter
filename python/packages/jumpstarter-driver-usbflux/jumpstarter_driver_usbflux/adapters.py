"""Client-side helpers that hand a lease's usbmux endpoint to local tools."""

import plistlib
import subprocess
from contextlib import contextmanager
from xml.parsers.expat import ExpatError

from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client import DriverClient

CONNECT_TIMEOUT = 60.0
DISCONNECT_TIMEOUT = 30.0


def validate_port(port: int, *, ephemeral: bool = False) -> None:
    minimum = 0 if ephemeral else 1
    if isinstance(port, bool) or not isinstance(port, int) or not minimum <= port <= 65535:
        raise ValueError(f"Port must be an integer between {minimum} and 65535")


@contextmanager
def forward_endpoint(
    *, client: DriverClient, method: str = "connect", local_host: str = "127.0.0.1", local_port: int = 0
):
    """Yield ``host:port`` for a loopback listener forwarding to ``method``.

    A fixed port is advertised to tools, so it must not be shared silently with
    another listener; an ephemeral port keeps the adapter's rebind-friendly default.
    """
    # Raise caller errors outside the adapter's task group to preserve their type.
    caller_error = None
    with TcpPortforwardAdapter(
        client=client, method=method, local_host=local_host, local_port=local_port, reuse_port=local_port == 0
    ) as addr:
        try:
            yield f"{addr[0]}:{addr[1]}"
        except BaseException as exc:  # noqa: BLE001 - preserve cancellation and caller exceptions outside the task group
            caller_error = exc
    if caller_error is not None:
        raise caller_error


def usbflux_registered(usbfluxctl: str, target: str) -> bool:
    """Whether usbfluxd still lists ``target``; unknown counts as registered."""
    try:
        result = subprocess.run(
            [usbfluxctl, "list", "xml"], check=False, capture_output=True, timeout=DISCONNECT_TIMEOUT
        )
    except (subprocess.SubprocessError, OSError):
        return True
    # usbfluxctl's list exits nonzero even when it succeeds; read its plist instead.
    try:
        instances = plistlib.loads(result.stdout).get("Instances")
    except (plistlib.InvalidFileException, ValueError, ExpatError, AttributeError):
        return True
    if not isinstance(instances, dict):
        return True
    host, port = target.rsplit(":", 1)
    return any(
        isinstance(instance, dict)
        and not instance.get("IsUnix")
        and instance.get("Host") == host
        and instance.get("Port") == int(port)
        for instance in instances.values()
    )


@contextmanager
def UsbfluxAdapter(
    *,
    client: DriverClient,
    method: str = "connect_usbmux",
    port: int = 0,
    usbfluxctl: str = "usbfluxctl",
    timeout: float = CONNECT_TIMEOUT,
):
    """Serve the usbmux stream on loopback and register it with the user's usbfluxd.

    usbfluxd then presents the leased device on the local usbmuxd socket, so
    unmodified tools find it. The user installs and runs usbfluxd. The
    registration is removed on exit, including after a failed or timed out
    attach; a registration that already existed is left in place.
    """
    validate_port(port, ephemeral=True)
    with forward_endpoint(client=client, method=method, local_port=port) as target:
        attach = [usbfluxctl, "add", target]
        detach = [usbfluxctl, "del", target]
        cleanup = True
        try:
            try:
                result = subprocess.run(attach, check=False, capture_output=True, text=True, timeout=timeout)
            except (subprocess.SubprocessError, OSError) as exc:
                raise RuntimeError(f"Could not run {attach[0]} to attach {target}: {exc}") from exc
            # usbfluxctl's `add` returns 2 when the endpoint was registered
            # already. Do not delete a registration owned by another session.
            if result.returncode == 2:
                cleanup = False
            if result.returncode:
                message = (result.stderr or result.stdout or "no output").strip()
                raise RuntimeError(f"{attach[0]} failed to attach {target}: {message}")
            yield target
        finally:
            if cleanup:
                try:
                    result = subprocess.run(
                        detach, check=False, capture_output=True, text=True, timeout=DISCONNECT_TIMEOUT
                    )
                    # Ctrl+C closes this listener before cleanup runs, and
                    # usbfluxd then drops the dead remote by itself.
                    if result.returncode and usbflux_registered(usbfluxctl, target):
                        client.logger.warning("Could not detach %s: %s", target, result.stderr or result.stdout)
                except (subprocess.SubprocessError, OSError) as exc:
                    client.logger.warning("Could not detach %s: %s", target, exc)
