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


def _adb_env(port: int) -> dict[str, str]:
    """Environment pointing adb at the ADB server on *port*."""
    return {**os.environ, "ANDROID_ADB_SERVER_PORT": str(port)}


def _resolve_adb_path(adb_path: str) -> str:
    """Resolve ``"adb"`` against PATH, and fail early if it is missing or broken."""
    if adb_path == "adb":
        resolved = shutil.which("adb")
        if not resolved:
            raise ConfigurationError("ADB executable not found in PATH")
        adb_path = resolved

    try:
        subprocess.run(
            [adb_path, "version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
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
    therefore a correctness requirement, not an optimisation.

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
        """Start the ADB server, bounded so a wedged port cannot hang startup."""
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
            if result.stdout.strip():
                logger.info(result.stdout.strip())
            if result.stderr.strip():
                logger.debug(result.stderr.strip())
        except subprocess.CalledProcessError as e:
            logger.error("Failed to start ADB server: %s", e)
        except subprocess.TimeoutExpired:
            logger.error(
                "`adb start-server` timed out after %ss on port %d. Something that is not "
                "an ADB server may hold that port; free it or configure a different port.",
                connect_timeout,
                self.port,
            )

    def kill(self, connect_timeout: float, logger) -> None:
        """Kill the ADB server, bounded because this runs from teardown."""
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
            if result.stdout.strip():
                logger.info(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            logger.error("Failed to kill ADB server: %s", e)
        except subprocess.TimeoutExpired:
            logger.error("`adb kill-server` timed out after %ss", connect_timeout)


# One ADB server per (adb_path, port) per exporter process. See `_SharedServer` for
# why sharing is mandatory rather than merely tidy.
_SERVERS: dict[tuple[str, int], _SharedServer] = {}
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

    The probe and start happen while holding the lock. That serialises concurrent
    first-acquirers, which is the point: without it two callers could both decide no
    server was running and both start one.
    """
    key = (adb_path, port)
    with _SERVERS_LOCK:
        entry = _SERVERS.get(key)
        if entry is None:
            entry = _SharedServer(adb_path, port)
            if adopt_existing_server and entry._is_listening(connect_timeout, logger):
                logger.info(
                    "adopting the ADB server already listening on port %d; it owns the "
                    "connected devices, and this driver will leave it running",
                    port,
                )
            else:
                entry.start(connect_timeout, logger)
                entry.owns = True
                logger.info("ADB server running on port %d", port)
            _SERVERS[key] = entry
        entry.refs += 1
        return entry


def _release_server(adb_path: str, port: int, *, connect_timeout: float, logger) -> None:
    """Drop a reference, killing the server only when it is ours and unused."""
    key = (adb_path, port)
    with _SERVERS_LOCK:
        entry = _SERVERS.get(key)
        if entry is None:
            return
        entry.refs -= 1
        if entry.refs > 0:
            return
        del _SERVERS[key]
        if entry.owns:
            entry.kill(connect_timeout, logger)
        else:
            logger.debug("leaving the adopted ADB server on port %d running", port)


@dataclass(kw_only=True)
class AdbServer(TcpNetwork):
    """An ADB server on the exporter, tunnelled to the client.

    Point client tooling at *this* server with ``forward_adb``/``j adb tunnel``: the
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
        self.adb_path = _resolve_adb_path(self.adb_path)

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
            _release_server(
                self.adb_path,
                self.port,
                connect_timeout=self.connect_timeout,
                logger=self.logger,
            )
            self._server = None
        super().close()

    def adb_env(self) -> dict[str, str]:
        """Environment with ANDROID_ADB_SERVER_PORT set."""
        return _adb_env(self.port)

    @export
    def start_server(self) -> int:
        """Start the ADB server on the exporter. Returns the port number.

        Note this is silent and succeeds when a server is already listening, so the
        result does not tell you whether the server is ours — see
        `adopt_existing_server`.
        """
        _SharedServer(self.adb_path, self.port).start(self.connect_timeout, self.logger)
        return self.port

    @export
    def kill_server(self) -> int:
        """Kill the ADB server on the exporter. Returns the port number."""
        _SharedServer(self.adb_path, self.port).kill(self.connect_timeout, self.logger)
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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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
        self.adb_path = _resolve_adb_path(self.adb_path)
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
        (``usb:1-4.2``), on the macOS native backend an IOKit location ID in hex
        (``usb:1A320000``). Config may write it with or without the prefix; matching
        is exact string equality, so it is normalized once here.
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

        for serial, state, devpath in self._visible_devices():
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

        raise RuntimeError(
            f"no device on USB port {self.usb_port}. It may be powered off — "
            f"turn on its power relay — or plugged into a different port."
        )

    def _live_forward_port(self, serial: str) -> int | None:
        """The local port ADB currently forwards for *serial*, if any.

        ``adb forward --list`` prints ``<serial> tcp:<local> tcp:<remote>`` and is the
        single source of truth: forwards live in the ADB server, and they vanish with
        the device. That is what makes a stale memoized port detectable.
        """
        try:
            result = self._run_adb(["forward", "--list"])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning("could not list adb forwards (%s)", e)
            return None

        want_remote = f"tcp:{self.adbd_port}"
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 3 or fields[0] != serial:
                continue
            if not fields[1].startswith("tcp:") or fields[2] != want_remote:
                continue
            try:
                return int(fields[1].removeprefix("tcp:"))
            except ValueError:
                continue
        return None

    def _create_forward(self, serial: str) -> int:
        """Forward this device's adbd to a free exporter port; return the chosen port.

        ``tcp:0`` asks the ADB server to pick the port, so the exporter needs no
        configured port range kept clear of whatever else runs there.
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
        # resolved TCP port number"). A silent server still created the forward, so
        # ask what it bound rather than treating this as a failure.
        port = self._live_forward_port(serial)
        if port is None:
            raise RuntimeError(
                f"could not forward {serial}: adb reported no forwarded port "
                f"(stdout {(result.stdout or '').strip()!r}) and `forward --list` does not show one"
            )
        return port

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
        self._connected = target

        host, _, port = target.rpartition(":")
        return host, int(port)

    def _ensure_forward(self) -> int:
        """The local port forwarding this device's adbd, creating it if needed."""
        serial = self._resolve_serial()

        if self._forward_port is not None:
            if self._live_forward_port(serial) == self._forward_port:
                return self._forward_port
            self.logger.info(
                "forward tcp:%d for %s is gone (device re-enumerated?); recreating",
                self._forward_port,
                serial,
            )
            self._forward_port = None

        # An earlier forward for this serial from a previous stream is reusable.
        existing = self._live_forward_port(serial)
        self._forward_port = existing if existing is not None else self._create_forward(serial)
        self.logger.info("%s forwarded on tcp:%d", serial, self._forward_port)
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
        """Describe this device: its transport, selector, and whether it is present."""
        result = {
            "transport": self.transport,
            "adbd_port": str(self.adbd_port),
        }
        if self.transport == TRANSPORT_TCP:
            result["address"] = str(self.address)
            return result

        result["selector"] = self.serial or str(self.usb_port)
        try:
            result["serial"] = self._resolve_serial()
            result["present"] = "yes"
        except RuntimeError as e:
            result["present"] = "no"
            result["reason"] = str(e)
        return result

    def close(self):
        """Drop the forward, disconnect a TCP device, and release the shared server."""
        with self._lock:
            forward_port, connected = self._forward_port, self._connected
            self._forward_port = self._connected = None

        if forward_port is not None:
            try:
                self._run_adb(["forward", "--remove", f"tcp:{forward_port}"], check=False)
            except (subprocess.SubprocessError, OSError) as e:
                self.logger.warning("could not remove forward tcp:%d (%s)", forward_port, e)

        if connected is not None:
            try:
                self._run_adb(["disconnect", connected], check=False)
            except (subprocess.SubprocessError, OSError) as e:
                self.logger.debug("could not disconnect %s (%s)", connected, e)

        if self._server is not None:
            _release_server(
                self.adb_path,
                self.server_port,
                connect_timeout=self.connect_timeout,
                logger=self.logger,
            )
            self._server = None
        super().close()
