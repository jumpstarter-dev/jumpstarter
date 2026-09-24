import math
import os
import shutil
import socket
import subprocess
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from anyio import connect_tcp, to_thread
from jumpstarter_driver_network.driver import TcpNetwork

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver
from jumpstarter.driver.decorators import export, exportstream

#: Transports an ``AdbDevice`` can use. adb itself has only two: USB and TCP
#: (``adb.h`` defines ``kTransportUsb`` and ``kTransportLocal``, where "local"
#: means TCP). There is deliberately no ``serial`` — adb has no UART transport,
#: and ``dev:``/``dev-raw:`` are forward targets executed inside adbd on the
#: device, not host transports. See the README.
TRANSPORT_USB = "usb"
TRANSPORT_TCP = "tcp"
_TRANSPORTS = (TRANSPORT_USB, TRANSPORT_TCP)

# Transports someone may reasonably reach for that adb cannot do, mapped to what to
# do instead. A bare "unknown transport" sends people looking for a typo.
_UNSUPPORTED_TRANSPORTS = {
    "serial": (
        "adb has no serial/UART transport. Bridge the UART to TCP (socat) or use the "
        "device's console to enable adbd over TCP, then use transport: tcp."
    ),
    "uart": (
        "adb has no serial/UART transport. Bridge the UART to TCP (socat) or use the "
        "device's console to enable adbd over TCP, then use transport: tcp."
    ),
    "vsock": "vsock is not implemented yet; use transport: tcp with the device's address.",
    "emulator": "emulators are found by the ADB server itself; use jumpstarter-driver-androidemulator.",
}

#: adb's own default server port. Deliberately *not* this driver's default (see
#: ``server_port``), but the port anything else on the host will be using — a desktop
#: ``adb``, Android Studio, a stray container. Only used to diagnose a blind server.
DEFAULT_ADB_SERVER_PORT = 5037


def _adb_env(port: int) -> dict[str, str]:
    """Environment pointing adb at the ADB server on *port*."""
    return {**os.environ, "ANDROID_ADB_SERVER_PORT": str(port)}


def _resolve_adb_path(adb_path: str, connect_timeout: float) -> str:
    """Resolve ``"adb"`` against PATH, and fail early if it is missing or broken.

    Normalized through ``realpath`` so that ``adb``, ``/usr/bin/adb`` and a symlink
    into a versioned SDK directory are one string. Two drivers naming the same binary
    differently must not look like two binaries — see ``_acquire_server``.

    Bounded like every other adb call here. This one runs from ``__post_init__`` of both
    drivers, so an adb that never returns would hang exporter startup with nothing to
    recover from. Expiry is a configuration failure: a binary that cannot answer
    ``version`` cannot serve a device either.
    """
    if adb_path == "adb":
        resolved = shutil.which("adb")
        if not resolved:
            raise ConfigurationError("ADB executable not found in PATH")
        adb_path = resolved
    adb_path = os.path.realpath(adb_path)

    try:
        subprocess.run(
            [adb_path, "version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=connect_timeout,
        )
    # FileNotFoundError is an OSError, so a missing binary still reports as before.
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        raise ConfigurationError(f"ADB executable not functional: {e}") from e
    return adb_path


def _validate_port(name: str, value) -> None:
    """Reject anything that is not a usable TCP port.

    ``bool`` is excluded explicitly: it subclasses ``int``, so ``port: true`` would
    otherwise pass as port 1.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigurationError(f"{name} must be an integer: {value}")
    if value < 1 or value > 65535:
        raise ConfigurationError(f"Invalid {name}: {value}")


def _validate_timeout(value) -> None:
    """Reject a non-positive or non-finite ``connect_timeout``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"connect_timeout must be a positive number: {value}")


class _SharedServer:
    """One ADB server on one port, shared by every driver that needs it.

    An ADB server **claims** the USB devices it finds, and only one server can hold a
    given device. So two drivers must never each start their own on the same port: the
    second would see an empty device list while `adb start-server` reported success
    (it is silent and exits 0 whether it started a server or found one). Sharing is
    therefore a correctness requirement, not an optimization.

    Reference-counted so the last user tears it down, and only if *we* started it —
    killing a server we merely adopted would drop the device claims of everything else
    on the host.
    """

    def __init__(self, adb_path: str, port: int) -> None:
        """Track a not-yet-started server for *adb_path* on *port*."""
        self.adb_path = adb_path
        self.port = port
        self.refs = 0
        self.owns = False

    def env(self) -> dict[str, str]:
        """Environment pointing adb at this server."""
        return _adb_env(self.port)

    def _is_listening(self, connect_timeout: float, logger) -> bool:
        """Whether a usable ADB server is already serving this port.

        Two checks, because a listening socket alone is not enough. Something that is
        *not* adb holding the port is the dangerous case: `adb start-server` and
        `adb devices` both block forever against such a listener rather than failing
        (verified against a plain TCP listener), which would hang exporter startup. So
        we connect first, then ask the peer something only a server can answer.

        That question has to be `devices`, not `version`: `adb version` reports the
        local client's own version without contacting the server at all (verified — it
        exits 0 with zero connections to the port), so it would accept any listener.
        `devices` does contact the server, which answers it immediately, while a
        non-ADB listener leaves it to hit the timeout below.
        """
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2):
                pass
        except OSError:
            return False

        try:
            result = subprocess.run(
                [self.adb_path, "devices"],
                check=False,
                capture_output=True,
                text=True,
                timeout=min(connect_timeout, 10),
                env=self.env(),
            )
        except (subprocess.TimeoutExpired, OSError):
            logger.warning(
                "something is listening on port %d but does not answer as an ADB server; "
                "not adopting it. Free the port, or set a different port in the exporter config.",
                self.port,
            )
            return False
        return result.returncode == 0

    def start(self, connect_timeout: float, logger) -> None:
        """Start the ADB server, or raise if it did not come up.

        Bounded so a wedged port cannot hang startup. Raises rather than logs: callers
        record the server as ours and running once this returns, so a failure swallowed
        here left the driver looking healthy while every later adb call failed.
        """
        logger.info("Starting ADB server on port %d", self.port)
        try:
            result = subprocess.run(
                [self.adb_path, "start-server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # Bounded: `start-server` blocks forever if a non-ADB process holds the
                # port, which would otherwise hang exporter startup.
                timeout=connect_timeout,
                env=self.env(),
            )
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or "").strip() or str(e)
            raise RuntimeError(f"could not start the ADB server on port {self.port}: {detail}") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"`adb start-server` timed out after {connect_timeout}s on port {self.port}. Something "
                "that is not an ADB server may hold that port; free it or configure a different port."
            ) from e
        except OSError as e:
            raise RuntimeError(f"could not run adb to start the ADB server on port {self.port}: {e}") from e

        if result.stdout.strip():
            logger.info(result.stdout.strip())
        if result.stderr.strip():
            logger.debug(result.stderr.strip())

    def kill(self, connect_timeout: float, logger) -> None:
        """Kill the ADB server, or raise if it is still running afterwards.

        Bounded, because this also runs from teardown. `adb kill-server` exits 0 even when
        there was no server to kill ("cannot connect to daemon"), so a zero exit is not
        proof of anything: what counts is whether the port is still being served.
        """
        logger.info("Killing ADB server on port %d", self.port)
        try:
            result = subprocess.run(
                [self.adb_path, "kill-server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=connect_timeout,
                env=self.env(),
            )
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or "").strip() or str(e)
            raise RuntimeError(f"could not kill the ADB server on port {self.port}: {detail}") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"`adb kill-server` timed out after {connect_timeout}s on port {self.port}") from e
        except OSError as e:
            raise RuntimeError(f"could not run adb to kill the ADB server on port {self.port}: {e}") from e

        if result.stdout.strip():
            logger.info(result.stdout.strip())
        if self._port_open():
            raise RuntimeError(f"`adb kill-server` returned, but port {self.port} is still being served")

    def _port_open(self) -> bool:
        """Whether anything is still accepting connections on this server's port."""
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                return True
        except OSError:
            return False


# One ADB server per port per exporter process. Keyed on the port alone, because that
# is what identifies the server: only one process can listen on it, and adb reaches it
# through ANDROID_ADB_SERVER_PORT with no notion of which binary started it. Keying on
# (adb_path, port) split one real server across two entries, and then either refcount
# could reach zero and kill a server the other still held. See `_SharedServer` for why
# a second server on one port is a correctness problem rather than mere waste.
_SERVERS: dict[int, _SharedServer] = {}
_SERVERS_LOCK = threading.Lock()


def _acquire_server(
    adb_path: str,
    port: int,
    *,
    connect_timeout: float,
    adopt_existing_server: bool,
    logger,
) -> _SharedServer:
    """Take a reference to the ADB server on *port*, starting or adopting it once.

    The probe and start happen while holding the lock. That serializes concurrent
    first-acquirers, which is the point: without it two callers could both decide no
    server was running and both start one.

    The first acquirer's ``adb_path`` is the one the server runs as; a later caller
    naming a different binary shares it rather than starting a second, and is warned,
    because an adb client whose version differs from the running server kills and
    restarts it on its first command — which would drop every device claim on the port.
    """
    with _SERVERS_LOCK:
        entry = _SERVERS.get(port)
        if entry is None:
            entry = _SharedServer(adb_path, port)
            already_up = entry._is_listening(connect_timeout, logger)
            if already_up and adopt_existing_server:
                logger.info(
                    "adopting the ADB server already listening on port %d; it owns the "
                    "connected devices, and this driver will leave it running",
                    port,
                )
            elif already_up:
                # `adopt_existing_server: false` asks for a server of our own, and the
                # port already has one. We cannot have both — and `adb start-server` is
                # silent and exits 0 either way, so starting would "succeed" and leave us
                # believing we own a server somebody else runs. `close()` would then kill
                # it: against Android Studio that drops the device claims and Studio
                # respawns its server ~1s later (measured), bouncing every device.
                logger.warning(
                    "adopt_existing_server is false, but an ADB server is already listening "
                    "on port %d; using it and leaving ownership with whoever started it. "
                    "Set a different server_port to get a server of your own.",
                    port,
                )
            else:
                entry.start(connect_timeout, logger)
                entry.owns = True
                logger.info("ADB server running on port %d", port)
            _SERVERS[port] = entry
        elif entry.adb_path != adb_path:
            logger.warning(
                "the ADB server on port %d runs as %s; this driver's %s will share it "
                "rather than start a second. If their versions differ the adb client "
                "will kill and restart the server, dropping the device claims of every "
                "other driver on this port — configure one adb_path, or a distinct port.",
                port,
                entry.adb_path,
                adb_path,
            )
        entry.refs += 1
        return entry


def _release_server(port: int, *, connect_timeout: float, logger) -> None:
    """Drop a reference, killing the server only when it is ours and unused."""
    with _SERVERS_LOCK:
        entry = _SERVERS.get(port)
        if entry is None:
            return
        entry.refs -= 1
        if entry.refs > 0:
            return
        del _SERVERS[port]
        if entry.owns:
            # Teardown is best-effort: a lease must finish releasing even if the server
            # will not die, so report the failure rather than abort the rest of `close()`.
            try:
                entry.kill(connect_timeout, logger)
            except RuntimeError as e:
                logger.error("%s", e)
        else:
            logger.debug("leaving the adopted ADB server on port %d running", port)


@dataclass(kw_only=True)
class AdbServer(TcpNetwork):
    """An ADB server on the exporter, tunneled to the client.

    Point client tooling at *this* server with ``forward_adb``/``j adb serve``: the
    client then sees the exporter's devices instead of its own. That is exclusive —
    the client must own its ADB server — so for adding a single remote device to an
    ADB server the client already runs (Android Studio's, say), declare an
    :class:`AdbDevice` instead.

    Declaring this driver is optional. ``AdbDevice`` ensures a server on its own, and
    both route through the same per-process registry, so an explicitly declared server
    is the one a co-located device adopts.
    """

    adb_path: str = "adb"
    host: str = "127.0.0.1"
    port: int = 15037
    connect_timeout: float = 30.0

    # Whether to use an ADB server that is already listening on `port` instead of
    # insisting on one we started ourselves. See `_SharedServer`: the running server
    # owns the USB devices, so a server started alongside it sees nothing.
    adopt_existing_server: bool = True

    _server: _SharedServer | None = field(default=None, init=False, repr=False)

    @classmethod
    def client(cls) -> str:
        """Import path of the matching client class."""
        return "jumpstarter_driver_adb.client.AdbClient"

    def __post_init__(self):
        """Validate the config and bring an ADB server up on our port."""
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        _validate_port("port", self.port)
        _validate_timeout(self.connect_timeout)
        self.adb_path = _resolve_adb_path(self.adb_path, self.connect_timeout)

        # Eager, unlike AdbDevice: this driver *is* the server, and callers such as
        # the cuttlefish and androidemulator drivers expect it up after construction.
        self._server = _acquire_server(
            self.adb_path,
            self.port,
            connect_timeout=self.connect_timeout,
            adopt_existing_server=self.adopt_existing_server,
            logger=self.logger,
        )

    @property
    def _owns_server(self) -> bool:
        """Whether this process started the server, rather than adopting one."""
        return self._server is not None and self._server.owns

    def close(self):
        """Release our reference to the shared ADB server."""
        if self._server is not None:
            _release_server(self.port, connect_timeout=self.connect_timeout, logger=self.logger)
            self._server = None
        super().close()

    def adb_env(self) -> dict[str, str]:
        """Environment with ANDROID_ADB_SERVER_PORT set."""
        return _adb_env(self.port)

    @export
    def start_server(self) -> int:
        """Ensure the ADB server on the exporter is up. Returns the port number.

        Operates on the shared registry entry rather than a throwaway one, so ownership
        stays accurate: `adb start-server` is silent and exits 0 whether it started a
        server or found one, so it cannot tell us. We probe instead, and only claim
        ownership of a server we actually started — otherwise `close()` would kill a
        server adopted from another process.
        """
        with _SERVERS_LOCK:
            entry = _SERVERS.get(self.port) or self._server
            if entry is None:  # pragma: no cover - __post_init__ always sets one
                return self.port
            if entry._is_listening(self.connect_timeout, self.logger):
                self.logger.info("ADB server already listening on port %d", self.port)
            else:
                entry.start(self.connect_timeout, self.logger)
                entry.owns = True
        return self.port

    @export
    def kill_server(self) -> int:
        """Kill the ADB server on the exporter. Returns the port number.

        Destructive and shared: the server owns the USB device claims of every driver
        pointed at this port, so any co-located `AdbDevice` loses its forwards. Killing
        it clears our ownership, so `close()` does not later kill a server that some
        other process started on the freed port.
        """
        with _SERVERS_LOCK:
            entry = _SERVERS.get(self.port) or self._server
            if entry is None:  # pragma: no cover - __post_init__ always sets one
                return self.port
            if entry.refs > 1:
                self.logger.warning(
                    "killing the ADB server on port %d, which %d drivers share; their "
                    "forwards and device claims go with it",
                    self.port,
                    entry.refs,
                )
            entry.kill(self.connect_timeout, self.logger)
            entry.owns = False
        return self.port

    @export
    def connect_device(self, device: str) -> str:
        """Connect the exporter's ADB server to a device by address (host:port).

        Raises on failure or timeout so callers can react instead of
        silently receiving an error string.
        """
        self.logger.info(f"Connecting to device {device}")
        try:
            result = subprocess.run(
                [self.adb_path, "connect", device],
                check=True,
                capture_output=True,
                text=True,
                env=self.adb_env(),
                timeout=self.connect_timeout,
            )
            output = result.stdout.strip()
            self.logger.info(output)
            return output
        except subprocess.TimeoutExpired as e:
            self.logger.error(f"Timed out connecting to device {device} after {self.connect_timeout}s")
            raise TimeoutError(f"adb connect {device} timed out after {self.connect_timeout}s") from e
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            self.logger.error(f"Failed to connect to device {device}: {stderr or e}")
            raise

    @export
    def disconnect_device(self, device: str) -> str:
        """Disconnect an ADB device by address (host:port).

        Raises on failure or timeout so callers can react instead of
        silently receiving an error string.
        """
        self.logger.info(f"Disconnecting device {device}")
        try:
            result = subprocess.run(
                [self.adb_path, "disconnect", device],
                check=True,
                capture_output=True,
                text=True,
                env=self.adb_env(),
                timeout=self.connect_timeout,
            )
            output = result.stdout.strip()
            self.logger.info(output)
            return output
        except subprocess.TimeoutExpired as e:
            self.logger.error(f"Timed out disconnecting device {device} after {self.connect_timeout}s")
            raise TimeoutError(f"adb disconnect {device} timed out after {self.connect_timeout}s") from e
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            self.logger.error(f"Failed to disconnect device {device}: {stderr or e}")
            raise

    @export
    def list_devices(self) -> str:
        """List devices visible to the exporter's ADB server.

        Read live from the ADB server on every call. Bounded, since `adb devices`
        blocks forever if a non-ADB process holds the port.
        """
        try:
            result = subprocess.run(
                [self.adb_path, "devices", "-l"],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.connect_timeout,
                env=self.adb_env(),
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to list devices: {e}")
            return f"Error: {e}"
        except subprocess.TimeoutExpired as e:
            self.logger.error(f"`adb devices` timed out after {self.connect_timeout}s")
            return f"Error: {e}"


@dataclass(kw_only=True)
class AdbDevice(Driver):
    """One declared Android device, exposed as a stream of its adbd.

    Declared rather than discovered, which is what makes it composable: a DUT is a
    composite of its power relay, its console and this, so leasing the DUT leases the
    right device. It also means a device that is currently powered off is still
    *described* — startup does not require it to be present.

    Identity for a USB device is the **bench port** (``usb_port``), not the device's
    serial, so hardware can be swapped between benches without a config change. The
    serial is looked up from the port on every call, which is exactly what changes
    when a relay power-cycles the DUT and USB re-enumerates.
    """

    driver_type = "network"

    #: ``"usb"`` for a USB-attached device, ``"tcp"`` for one whose adbd already
    #: listens on TCP (a networked or AAOS head unit, a virtual device).
    transport: str = TRANSPORT_USB

    #: For transport usb: the bench USB port (preferred) or an explicit ADB serial.
    #: Exactly one of the two.
    usb_port: str | None = None
    serial: str | None = None

    #: For transport tcp: the device's own adbd endpoint, ``host`` or ``host:port``.
    address: str | None = None

    adbd_port: int = 5555
    adb_path: str = "adb"
    connect_timeout: float = 30.0
    #: Which ADB server to use. Rarely set: the server is implicit and shared.
    server_port: int = 15037
    adopt_existing_server: bool = True

    _lock: threading.Lock = field(init=False, repr=False)
    _server: _SharedServer | None = field(default=None, init=False, repr=False)
    _forward_port: int | None = field(default=None, init=False, repr=False)
    _connected: str | None = field(default=None, init=False, repr=False)

    # Forwards and connections live in the *shared* ADB server, so one that was already
    # there may belong to another driver, another exporter process, or a person at the
    # bench. Reusing it is right — re-forwarding per stream would churn the server — but
    # removing it on close is not. These record which side of that line we are on.
    _owns_forward: bool = field(default=False, init=False, repr=False)
    _forward_serial: str | None = field(default=None, init=False, repr=False)
    _owns_connection: bool = field(default=False, init=False, repr=False)

    @classmethod
    def client(cls) -> str:
        """Import path of the matching client class."""
        return "jumpstarter_driver_adb.client.AdbDeviceClient"

    def __post_init__(self):
        """Validate the config and resolve adb. Does not touch the device or a server."""
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self._lock = threading.Lock()
        self._validate_config()
        self.adb_path = _resolve_adb_path(self.adb_path, self.connect_timeout)
        if self.usb_port is not None:
            self.usb_port = self._normalize_usb_port(self.usb_port)

    def _validate_config(self) -> None:
        """Reject a config whose fields do not match its transport."""
        if self.transport not in _TRANSPORTS:
            hint = _UNSUPPORTED_TRANSPORTS.get(str(self.transport).lower())
            supported = "/".join(_TRANSPORTS)
            if hint:
                raise ConfigurationError(f"transport {self.transport!r} is not supported: {hint}")
            raise ConfigurationError(f"transport must be one of {supported}: {self.transport!r}")

        _validate_port("server_port", self.server_port)
        _validate_port("adbd_port", self.adbd_port)
        _validate_timeout(self.connect_timeout)

        if self.transport == TRANSPORT_USB:
            if self.address is not None:
                raise ConfigurationError("'address' only applies to transport: tcp")
            if (self.usb_port is None) == (self.serial is None):
                raise ConfigurationError(
                    "transport: usb needs exactly one of 'usb_port' (the bench USB port, preferred) or 'serial'"
                )
            if self.usb_port is not None and not str(self.usb_port).strip():
                raise ConfigurationError("'usb_port' must not be empty")
            if self.serial is not None and not str(self.serial).strip():
                raise ConfigurationError("'serial' must not be empty")
        else:
            if self.usb_port is not None or self.serial is not None:
                raise ConfigurationError("'usb_port'/'serial' only apply to transport: usb")
            if not self.address:
                raise ConfigurationError("transport: tcp needs 'address' (the device's adbd endpoint)")

    @staticmethod
    def _normalize_usb_port(usb_port: str) -> str:
        """Return *usb_port* in the exact form ``adb devices -l`` reports.

        adb prints the devpath as ``usb:<path>`` — on Linux the sysfs bus-port name
        (``usb:1-4.2``), on the macOS native backend the IOKit location ID **in decimal
        with a literal ``X`` suffix** (``usb:538116096X``, verified against adb 1.0.41 on
        macOS). The ``X`` reads like a hex marker but is not one: usb_osx.cpp formats it
        ``"usb:%" PRIu32 "X"``, so the number is decimal and the ``X`` is a stray literal.
        Copy the value from ``adb devices -l`` rather than converting anything. Config may
        write it with or without the ``usb:`` prefix; matching is exact string equality,
        so it is normalized once here.
        """
        port = str(usb_port).strip()
        if not port:
            raise ConfigurationError("'usb_port' must not be empty")
        return port if port.startswith("usb:") else f"usb:{port}"

    def _ensure_server(self) -> _SharedServer:
        """Take a reference to the shared ADB server, starting it on first use.

        Lazy on purpose: an exporter whose DUTs are all powered off should not start a
        server it may never need, and startup must not depend on one.
        """
        if self._server is None:
            self._server = _acquire_server(
                self.adb_path,
                self.server_port,
                connect_timeout=self.connect_timeout,
                adopt_existing_server=self.adopt_existing_server,
                logger=self.logger,
            )
        return self._server

    def _run_adb(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        """Run adb against our server, bounded by ``connect_timeout``."""
        return subprocess.run(
            [self.adb_path, *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=self.connect_timeout,
            env=_adb_env(self.server_port),
        )

    def _visible_devices(self) -> list[tuple[str, str, str | None]]:
        """Return ``(serial, state, devpath)`` for every device the server can see.

        ``adb devices -l`` prints ``<serial> <state> [usb:<path>] [product:...] ...``;
        emulators carry no ``usb:`` field at all.
        """
        try:
            result = self._run_adb(["devices", "-l"])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"could not list ADB devices: {e}") from e

        devices: list[tuple[str, str, str | None]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("*") or line.startswith("List of devices"):
                continue
            fields = line.split()
            if len(fields) < 2:
                continue
            devpath = next((f for f in fields[2:] if f.startswith("usb:")), None)
            devices.append((fields[0], fields[1], devpath))
        return devices

    def _resolve_serial(self) -> str:
        """The ADB serial to address this device by, resolved fresh on every call.

        For a ``usb_port``-configured device the serial is looked up from
        ``adb devices -l``, so a power cycle that re-enumerates the device (and can
        change its serial) is picked up automatically — the bench port is what stays
        constant. Using the serial rather than ``-s usb:<path>`` also keeps us on
        adb's documented ``-s SERIAL`` contract.
        """
        if self.serial is not None:
            return self.serial

        visible = self._visible_devices()
        for serial, state, devpath in visible:
            if devpath != self.usb_port:
                continue
            if state != "device":
                raise RuntimeError(
                    f"device on {self.usb_port} is '{state}', not ready. "
                    f"If it is unauthorized, accept the debugging prompt; if offline, power-cycle it."
                )
            # A macOS native-backend quirk: when the IOKit location ID cannot be read
            # adb sets devpath to the *serial* instead. Matching still works, and using
            # the reported serial here is correct either way.
            return serial

        message = (
            f"no device on USB port {self.usb_port}. It may be powered off — turn on its "
            f"power relay — or plugged into a different port."
        )
        if visible:
            seen = ", ".join(f"{s} ({p or 'no usb path'})" for s, _, p in visible)
            raise RuntimeError(f"{message} This server sees: {seen}.")

        # Our server sees nothing at all. On a bench that is the ordinary case — a
        # single DUT with its relay off — so the relay stays the leading explanation.
        # The other cause, another ADB server holding the device claims, is only
        # mentioned when there is evidence for it, because sending an operator to hunt
        # a rogue server when their relay is off is worse than not mentioning it.
        elsewhere = self._devices_reported_elsewhere()
        if elsewhere:
            raise RuntimeError(
                f"{message} This server (port {self.server_port}) sees no devices at all, "
                f"but one on port {DEFAULT_ADB_SERVER_PORT} reports {', '.join(elsewhere)}. "
                f"A device is claimed by whichever ADB server finds it first and only one "
                f"can hold it, so this server is blind to those: set server_port to "
                f"{DEFAULT_ADB_SERVER_PORT}, or stop that server."
            )
        raise RuntimeError(f"{message} This server sees no devices at all.")

    def _devices_reported_elsewhere(self) -> list[str]:
        """Serials an ADB server on adb's default port reports, if one is running there.

        Diagnostic only, and evidence rather than inference: a device is claimed by
        whichever server finds it first, so devices visible *there* while we see none is
        decisive about a split claim. Empty when there is nothing to learn.

        Connects before asking, and never asks at all when nothing is listening — an
        ``adb devices`` against a free port would *start a server there*, and a
        diagnostic that creates the thing it is diagnosing is worse than silence.
        """
        if self.server_port == DEFAULT_ADB_SERVER_PORT:
            return []
        try:
            with socket.create_connection(("127.0.0.1", DEFAULT_ADB_SERVER_PORT), timeout=1):
                pass
        except OSError:
            return []

        try:
            result = subprocess.run(
                [self.adb_path, "devices"],
                check=True,
                capture_output=True,
                text=True,
                timeout=min(self.connect_timeout, 5),
                env=_adb_env(DEFAULT_ADB_SERVER_PORT),
            )
        except (subprocess.SubprocessError, OSError):
            return []

        serials = []
        for line in result.stdout.splitlines()[1:]:
            # `* daemon not running…` style banners go to stdout too; a banner read as a
            # serial would invent a device and misdiagnose a genuinely empty bench.
            if not line.strip() or line.startswith("*"):
                continue
            fields = line.split()
            if len(fields) >= 2:
                serials.append(fields[0])
        return serials

    def _live_forwards(self, serial: str) -> set[int] | None:
        """Local ports the ADB server currently forwards to *serial*'s adbd.

        ``adb forward --list`` prints one ``<serial> tcp:<local> tcp:<remote>`` line per
        listener and is the single source of truth: forwards live in the ADB server, and
        they vanish with the device. That is what makes a stale memoized port detectable.

        A **set**, because one device can hold several forwards to the same adbd port —
        listeners are keyed by their local spec, so ours and a colleague's `adb forward
        tcp:9000 tcp:5555` coexist, and adb lists both. Anything asking "is *this* port
        still forwarded" must therefore test membership; picking one port and comparing
        would depend on adb's listener ordering, which is insertion order in a vector
        that removals shift.

        ``None`` when the server could not be asked, which is **not** the same as "there
        are none": collapsing the two is what let a failed listing be read as "everything
        is gone". Each caller decides what an unknown answer means for it.
        """
        try:
            result = self._run_adb(["forward", "--list"])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning("could not list adb forwards (%s)", e)
            return None

        want_remote = f"tcp:{self.adbd_port}"
        ports = set()
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 3 or fields[0] != serial:
                continue
            if not fields[1].startswith("tcp:") or fields[2] != want_remote:
                continue
            try:
                ports.add(int(fields[1].removeprefix("tcp:")))
            except ValueError:
                continue
        return ports

    def _create_forward(self, serial: str, known: set[int] | None) -> int:
        """Forward this device's adbd to a free exporter port; return the chosen port.

        ``tcp:0`` asks the ADB server to pick the port, so the exporter needs no
        configured port range kept clear of whatever else runs there. *known* is the set
        of forwards this device already had, used to identify ours if adb does not name
        the port it chose, or ``None`` if that could not be established.
        """
        try:
            result = self._run_adb(["-s", serial, "forward", "tcp:0", f"tcp:{self.adbd_port}"])
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            raise RuntimeError(
                f"could not forward {serial}: {stderr or e}. The device may have gone away, "
                f"or adbd may not be listening on tcp:{self.adbd_port} "
                f"(try `adb -s {serial} tcpip {self.adbd_port}`)."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"forwarding {serial} timed out after {self.connect_timeout}s") from e
        except OSError as e:
            raise RuntimeError(f"could not run adb to forward {serial}: {e}") from e

        port = self._parse_forwarded_port(result.stdout)
        if port is not None:
            return port

        # Reporting the port is optional in adb's protocol — AOSP's client prints it
        # only when the server sends one ("Server or device may optionally return a
        # resolved TCP port number"). A silent server still created the forward, so ask
        # what it bound rather than treating this as a failure. The difference against
        # what was there before names ours even when the device holds other forwards.
        live = self._live_forwards(serial) if known is not None else None
        appeared = live - known if (live is not None and known is not None) else None
        if appeared is None or len(appeared) != 1:
            detail = (
                "and `forward --list` could not be read"
                if appeared is None
                else f"and `forward --list` does not identify one ({sorted(appeared) or 'no new forward'})"
            )
            raise RuntimeError(
                f"could not forward {serial}: adb reported no forwarded port "
                f"(stdout {(result.stdout or '').strip()!r}) {detail}"
            )
        return appeared.pop()

    @staticmethod
    def _parse_forwarded_port(stdout: str | None) -> int | None:
        """The port `adb forward` printed, or None if it printed no usable port."""
        for line in reversed((stdout or "").strip().splitlines()):
            try:
                port = int(line.strip())
            except ValueError:
                continue
            if 0 < port < 65536:
                return port
        return None

    def _resolve_endpoint(self) -> tuple[str, int]:
        """The exporter-side ``(host, port)`` that speaks this device's adbd.

        Resolved on every stream, which is what makes re-enumeration self-healing:
        nothing is cached across a power cycle that could go stale unnoticed.
        """
        with self._lock:
            self._ensure_server()
            if self.transport == TRANSPORT_TCP:
                return self._resolve_tcp_endpoint()
            return "127.0.0.1", self._ensure_forward()

    def _resolve_tcp_endpoint(self) -> tuple[str, int]:
        """Connect the server to a TCP device and return the device's own endpoint.

        No forward is involved: adbd is already listening, so the stream goes straight
        to it. ``adb connect`` is idempotent ("already connected to ..."), so this is
        safe to run per stream.
        """
        assert self.address is not None  # guaranteed by _validate_config
        target = self.address if ":" in self.address else f"{self.address}:{self.adbd_port}"
        try:
            result = self._run_adb(["connect", target], check=False)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"`adb connect {target}` failed: {e}") from e

        message = (result.stdout or "").strip() or (result.stderr or "").strip()
        # `adb connect` exits 0 even when it fails, reporting the reason on stdout, so
        # match adb's own success strings instead of the exit status.
        if result.returncode != 0 or not message.startswith(("connected to", "already connected to")):
            raise RuntimeError(f"could not connect to {target}: {message or 'no output'}")

        if self._connected is None:
            # Which of the two success strings adb chose is the only evidence of who
            # connected this device. "already connected to" means the shared server had
            # it before us — possibly for another lease — so disconnecting it on close
            # would drop it out from under that user. Recorded once: a later stream
            # always sees "already connected to", including for our own connection.
            self._connected = target
            self._owns_connection = message.startswith("connected to")
            if not self._owns_connection:
                self.logger.info("%s was already connected; leaving it connected on close", target)

        host, _, port = target.rpartition(":")
        return host, int(port)

    def _ensure_forward(self) -> int:
        """The local port forwarding this device's adbd, creating it if needed."""
        serial = self._resolve_serial()
        live = self._live_forwards(serial)

        if live is None and self._forward_port is not None:
            # The server could not be asked. Reading that as "no forwards exist" is the
            # failure bennyz raised against the old slot pool, reappearing here: a forward
            # we own looks gone, ownership is dropped, and a second one is created that
            # nothing will ever remove. Keep what we have — a stream to a dead port fails
            # loudly and self-heals next time, while an orphaned forward is silent.
            self.logger.warning(
                "could not confirm forward tcp:%d for %s; keeping it rather than risking a second, unowned forward",
                self._forward_port,
                serial,
            )
            return self._forward_port

        if self._forward_port is not None and live is not None:
            if self._forward_port in live:
                return self._forward_port
            self.logger.info(
                "tcp:%d no longer forwards %s (device re-enumerated, or adb rebound the "
                "port to another device); recreating",
                self._forward_port,
                serial,
            )
            self._forward_port, self._forward_serial, self._owns_forward = None, None, False

        if live:
            # Reusable, but not ours to remove: `forward --list` cannot say who created a
            # forward, and on a shared or adopted server it may be another driver's or a
            # person's. Reuse avoids churning the server across per-stream resolves.
            # Lowest port when the device holds several, so repeated resolves agree.
            self._forward_port, self._owns_forward = min(live), False
            self.logger.info("%s is already forwarded on tcp:%d; reusing it as-is", serial, self._forward_port)
        else:
            self._forward_port, self._owns_forward = self._create_forward(serial, live), True
            self.logger.info("%s forwarded on tcp:%d", serial, self._forward_port)

        self._forward_serial = serial
        return self._forward_port

    @exportstream
    @asynccontextmanager
    async def connect(self):
        """Stream this device's adbd.

        The client port-forwards this and runs ``adb connect`` against the local end,
        which adds the device to whatever ADB server the client already uses.
        """
        host, port = await to_thread.run_sync(self._resolve_endpoint)
        self.logger.debug("streaming adbd via %s:%d", host, port)
        async with await connect_tcp(remote_host=host, remote_port=port) as stream:
            yield stream

    @export
    def info(self) -> dict[str, str]:
        """Describe this device: its transport, selector, and whether it is present.

        Takes a reference to the shared server first, like every other path that runs adb.
        Skipping it does not avoid starting a server — the adb *client* silently starts one
        when nothing answers the port — it only means the driver does not know it caused
        that server, adopts it on the next acquire, and then leaves it running at teardown
        because adopted servers are never killed. Observed against real hardware: `info()`
        alone left an ADB server behind after `close()`.
        """
        result = {
            "transport": self.transport,
            "adbd_port": str(self.adbd_port),
        }
        if self.transport == TRANSPORT_TCP:
            result["address"] = str(self.address)
            return result

        result["selector"] = self.serial or str(self.usb_port)
        try:
            with self._lock:
                self._ensure_server()
                result["serial"] = self._resolve_serial()
            result["present"] = "yes"
        except RuntimeError as e:
            result["present"] = "no"
            result["reason"] = str(e)
        return result

    def close(self):
        """Drop what we created, leave what we merely reused, release the shared server.

        Teardown only undoes this driver's own side effects. A forward or connection that
        was already in the shared ADB server belongs to whoever made it, and removing it
        would break them — silently, since adb reports nothing.
        """
        with self._lock:
            forward_port, forward_serial, owns_forward = self._forward_port, self._forward_serial, self._owns_forward
            connected, owns_connection = self._connected, self._owns_connection
            self._forward_port = self._forward_serial = self._connected = None
            self._owns_forward = self._owns_connection = False

        if forward_port is not None and owns_forward and forward_serial is not None:
            self._remove_own_forward(forward_serial, forward_port)
        elif forward_port is not None:
            self.logger.debug("leaving forward tcp:%d alone; this driver did not create it", forward_port)

        self._disconnect(connected, owns_connection)

        if self._server is not None:
            _release_server(self.server_port, connect_timeout=self.connect_timeout, logger=self.logger)
            self._server = None
        super().close()

    def _disconnect(self, connected: str | None, owns_connection: bool) -> None:
        """Disconnect *connected*, if this driver is the one that connected it."""
        if connected is None:
            return
        if not owns_connection:
            self.logger.debug("leaving %s connected; this driver did not connect it", connected)
            return
        try:
            self._run_adb(["disconnect", connected], check=False)
        except (subprocess.SubprocessError, OSError) as e:
            self.logger.debug("could not disconnect %s (%s)", connected, e)

    # ------------------------------------------------------------------ driver-side API
    #
    # For a parent driver that runs adb itself rather than streaming the device — the
    # Cuttlefish driver waits for `sys.boot_completed` with its own `adb shell`. These are
    # in-process calls on the child driver object, not exported to clients.

    def adb_env(self) -> dict[str, str]:
        """Environment pointing adb at this device's server, holding a reference to it.

        Acquiring the server is the point, not a side effect: without it the caller's first
        adb call finds nothing on the port and the adb *client* silently starts a server
        this driver does not know about. The next acquire would then adopt it, and adopted
        servers are never killed, so it would outlive the lease.
        """
        with self._lock:
            self._ensure_server()
        return _adb_env(self.server_port)

    def ensure_reachable(self) -> str:
        """Make the device reachable now and return the ``host:port`` adb can address it by.

        Endpoints are otherwise resolved per stream, which is what makes re-enumeration
        self-healing. A parent driver that wants the device in the shared server *before*
        any client connects — to poll for boot, say — calls this instead.

        For ``transport: tcp`` this runs the validated ``adb connect``; for ``usb`` it
        resolves the serial and its forward. Safe to call repeatedly.
        """
        host, port = self._resolve_endpoint()
        return f"{host}:{port}"

    def disconnect(self) -> None:
        """Drop this driver's ``adb connect``, if it made one, without ending the lease.

        For a parent driver that power-cycles the device mid-lease. A connection that was
        already in the shared server when this driver found it is left alone, and a later
        `ensure_reachable` reconnects as needed.
        """
        with self._lock:
            connected, owns_connection = self._connected, self._owns_connection
            self._connected, self._owns_connection = None, False
        self._disconnect(connected, owns_connection)

    def _remove_own_forward(self, serial: str, port: int) -> None:
        """Remove the forward we created, if the ADB server still shows it as ours.

        Removal cannot be scoped to a device, so confirm before removing. `adb forward
        --remove` maps to the ``killforward`` host service, which passes its transport to
        ``remove_listener`` — and that matches on the local spec alone and ignores the
        transport entirely (AOSP ``adb_listeners.cpp``). So ``-s <serial>`` does not
        narrow the removal, and adding it makes teardown *worse*: ``killforward`` acquires
        the transport up front, and ``acquire_one_transport`` rejects any state that is not
        ``device`` — so a DUT that is merely **offline**, which is where a relay leaves it
        mid power-cycle, refuses the removal while its listener is still there to remove.
        (A device that is truly gone is moot: its listener was already erased with its
        transport.)

        A re-check does narrow it, and there are two ways our port stops being ours.
        ``install_listener`` matches on the local spec too, and on a hit it **repurposes
        the existing listener in place** — new ``connect_to``, new transport, exit 0,
        silently — unless the caller passed ``--no-rebind``. So one
        ``adb -s OTHER forward tcp:<our port> tcp:5555`` from a person or another exporter
        moves our port to a different device without freeing anything. Less likely, each
        listener also registers a transport-disconnect hook, so a device going away drops
        its own forwards and the kernel may later hand that ephemeral port to another
        ``tcp:0``. Either way, removing by local spec would delete that device's forward.

        We cannot prevent the theft: ``--no-rebind`` on our own ``tcp:0`` is a no-op,
        because the listener is renamed to its resolved port and nothing ever matches the
        literal ``tcp:0`` again. A listing we cannot get is treated as "not ours" — the
        server is unreachable, so there is nothing to remove that a blind attempt would
        not risk taking from someone else.
        """
        live = self._live_forwards(serial)
        if live is None or port not in live:
            self.logger.debug(
                "not removing forward tcp:%d: %s",
                port,
                "the server could not be asked" if live is None else "it no longer forwards this device",
            )
            return
        try:
            self._run_adb(["forward", "--remove", f"tcp:{port}"], check=False)
        except (subprocess.SubprocessError, OSError) as e:
            self.logger.warning("could not remove forward tcp:%d (%s)", port, e)
