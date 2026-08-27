import math
import os
import shutil
import subprocess
from dataclasses import dataclass

from jumpstarter_driver_network.driver import TcpNetwork

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver.decorators import export


@dataclass(kw_only=True)
class AdbServer(TcpNetwork):
    """ADB server driver that tunnels ADB connections over Jumpstarter.

    Manages an ADB daemon on the exporter and exposes it via TCP tunnel.
    Client-side tools (adb, Android Studio, tradefed) connect through
    the tunnel as if the ADB server were local.
    """

    adb_path: str = "adb"
    host: str = "127.0.0.1"
    port: int = 15037
    connect_timeout: float = 30.0

    # Forward slots for attaching devices into a *client-owned* ADB server.
    #
    # Addressing this server (forward_adb) is exclusive: the client must own its
    # ADB server. That fails the common case where Android Studio already owns
    # 5037 — it respawns its server there within ~3s of being killed, so the port
    # cannot be won. `adb connect` is additive instead, so we publish each device's
    # adbd on a slot and let the client's existing server connect to it.
    #
    # A fixed pool, because Jumpstarter children are resolved at lease start and
    # @exportstream methods take no arguments — a stream cannot be parameterised by
    # device. The pool is static to satisfy the transport; the device→slot mapping
    # is assigned on demand to satisfy ADB, which is dynamic.
    #
    # An earlier revision declared devices in the exporter config, with stable ids
    # and client ports derived from them. It was withdrawn: a per-device child
    # freezes the device list at lease establishment, so a hotplugged device could
    # never be reached, and the rest re-implemented what `adb devices` already does.
    # Don't reintroduce a device inventory here.
    attach_slots: int = 8
    attach_base_port: int = 16000

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_adb.client.AdbClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if not isinstance(self.port, int):
            raise ConfigurationError(f"Port must be an integer: {self.port}")
        if self.port < 1 or self.port > 65535:
            raise ConfigurationError(f"Invalid port number: {self.port}")

        if (
            isinstance(self.connect_timeout, bool)
            or not isinstance(self.connect_timeout, (int, float))
            or not math.isfinite(self.connect_timeout)
            or self.connect_timeout <= 0
        ):
            raise ConfigurationError(f"connect_timeout must be a positive number: {self.connect_timeout}")

        # Resolve adb binary
        if self.adb_path == "adb":
            resolved = shutil.which("adb")
            if not resolved:
                raise ConfigurationError("ADB executable not found in PATH")
            self.adb_path = resolved

        # Verify adb works
        try:
            result = subprocess.run(
                [self.adb_path, "version"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.logger.debug(result.stdout.strip())
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise ConfigurationError(f"ADB executable not functional: {e}") from e

        # Slot children. Declared up front (the transport requires it) but empty:
        # nothing is forwarded until a client attaches a device. TcpNetwork connects
        # lazily, so an unused slot costs nothing.
        self._slots: dict[int, str | None] = {}
        for index in range(self.attach_slots):
            slot_port = self.attach_base_port + index
            self._slots[slot_port] = None
            self.children[f"slot{index}"] = TcpNetwork(host="127.0.0.1", port=slot_port)

        # Auto-start the ADB server on the configured port
        self.start_server()
        self.logger.info(f"ADB server running on {self.host}:{self.port}")

    def close(self):
        for slot_port, device in list(self._slots.items()):
            if device is not None:
                self._remove_forward(device, slot_port)
        self.kill_server()

    def _remove_forward(self, device: str, slot_port: int) -> None:
        """Drop an `adb forward`, best-effort."""
        subprocess.run(
            [self.adb_path, "-s", device, "forward", "--remove", f"tcp:{slot_port}"],
            check=False,  # already-gone is fine; teardown must not raise
            capture_output=True,
            text=True,
            env=self.adb_env(),
        )
        self._slots[slot_port] = None

    @export
    def attach_device(self, device: str, adbd_port: int = 5555) -> str:
        """Publish *device*'s adbd on a forward slot; return the slot's child name.

        The client forwards that slot and runs ``adb connect`` against it, which
        adds the device to whatever ADB server the client already uses — including
        one it does not own, such as Android Studio's. Attaching is additive, so
        several devices (and several exporters) coexist in one server.

        *device* is an ordinary ADB serial as reported by ``adb devices``: a USB
        serial, ``emulator-5554``, or a ``host:port``. Nothing has to be declared
        in advance, so a device that appeared after the exporter started — a
        hotplugged emulator, a phone just connected — works the same as one that
        was there all along.

        Idempotent: attaching an already-attached device returns its existing slot,
        so a client may call this on every attach without tracking state.

        Args:
            device: ADB serial to attach.
            adbd_port: adbd's TCP port on the device (``persist.adb.tcp.port``).

        Returns:
            The name of the slot child now carrying this device's adbd (e.g.
            ``"slot0"``), for the client to port-forward. A name rather than a port
            so the client needs no knowledge of the exporter's port configuration.

        Raises:
            RuntimeError: no free slot, or the forward could not be created (the
                device is gone, powered off, or adbd is not listening on TCP).
        """
        # gRPC carries numbers as doubles, so an int argument arrives as 5555.0 and
        # `adb forward tcp:5555.0` is rejected. Coerce rather than trust the wire.
        adbd_port = int(adbd_port)

        # Reconcile against ADB before trusting our own bookkeeping. `adb forward`
        # state lives in the ADB server, not here, so anything that restarts the
        # server or runs `forward --remove-all` / `adb usb` silently invalidates
        # `_slots`. Trusting memory made attach return "already attached" and skip
        # creating the forward, so the client tunnelled to a dead port and the
        # device sat `offline` — with no error anywhere. Observed on hardware.
        live = self._live_forwards()
        for slot_port, occupant in list(self._slots.items()):
            if occupant is None:
                continue
            if live.get(slot_port) != occupant:
                self.logger.info("slot tcp:%d claimed %s but ADB has no such forward; releasing", slot_port, occupant)
                self._slots[slot_port] = None

        for slot_port, occupant in self._slots.items():
            if occupant == device:
                return self._slot_name(slot_port)

        slot_port = next((p for p, occupant in self._slots.items() if occupant is None), None)
        if slot_port is None:
            raise RuntimeError(
                f"no free attach slot ({self.attach_slots} in use). Detach a device, "
                "or raise 'attach_slots' in the exporter config."
            )

        try:
            subprocess.run(
                [self.adb_path, "-s", device, "forward", f"tcp:{slot_port}", f"tcp:{adbd_port}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.connect_timeout,
                env=self.adb_env(),
            )
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            raise RuntimeError(
                f"could not attach {device}: {stderr or e}. The device may be offline, "
                f"or adbd may not be listening on tcp:{adbd_port} (try `adb tcpip {adbd_port}`)."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"attaching {device} timed out after {self.connect_timeout}s") from e

        self._slots[slot_port] = device
        self.logger.info("attached %s on slot tcp:%d (device tcp:%d)", device, slot_port, adbd_port)
        return self._slot_name(slot_port)

    def _slot_name(self, slot_port: int) -> str:
        """Child name for a slot port."""
        return f"slot{slot_port - self.attach_base_port}"

    @export
    def detach_device(self, device: str) -> None:
        """Release *device*'s forward slot. Idempotent."""
        for slot_port, occupant in list(self._slots.items()):
            if occupant == device:
                self._remove_forward(device, slot_port)
                self.logger.info("detached %s from slot tcp:%d", device, slot_port)
                return

    def _live_forwards(self) -> dict[int, str]:
        """Return ``{local_port: device}`` for forwards the ADB server actually has.

        The single source of truth for what is published. ``adb forward --list``
        prints ``<serial> tcp:<local> tcp:<remote>`` per line.
        """
        try:
            result = subprocess.run(
                [self.adb_path, "forward", "--list"],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.connect_timeout,
                env=self.adb_env(),
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning("could not list adb forwards (%s); assuming none", e)
            return {}

        forwards: dict[int, str] = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 2 or not fields[1].startswith("tcp:"):
                continue
            try:
                forwards[int(fields[1].removeprefix("tcp:"))] = fields[0]
            except ValueError:
                continue
        return forwards

    @export
    def list_attached(self) -> dict[str, str]:
        """Return ``{slot_port: device}`` for currently attached devices.

        Reconciled against ``adb forward --list``, so a forward destroyed outside
        this driver is not reported as attached. Keys are strings because they
        cross gRPC, which has no integer map keys.
        """
        live = self._live_forwards()
        return {
            str(port): device for port, device in self._slots.items() if device is not None and live.get(port) == device
        }

    def adb_env(self) -> dict[str, str]:
        """Environment with ANDROID_ADB_SERVER_PORT set."""
        return {**os.environ, "ANDROID_ADB_SERVER_PORT": str(self.port)}

    @export
    def start_server(self) -> int:
        """Start the ADB server on the exporter. Returns the port number."""
        self.logger.info(f"Starting ADB server on port {self.port}")
        try:
            result = subprocess.run(
                [self.adb_path, "start-server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self.adb_env(),
            )
            if result.stdout.strip():
                self.logger.info(result.stdout.strip())
            if result.stderr.strip():
                self.logger.debug(result.stderr.strip())
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to start ADB server: {e}")
        return self.port

    @export
    def kill_server(self) -> int:
        """Kill the ADB server on the exporter. Returns the port number."""
        self.logger.info(f"Killing ADB server on port {self.port}")
        try:
            result = subprocess.run(
                [self.adb_path, "kill-server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self.adb_env(),
            )
            if result.stdout.strip():
                self.logger.info(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to kill ADB server: {e}")
        return self.port

    def _connect_device(self, device: str) -> str:
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
    def connect_device(self, device: str) -> str:
        """Connect to an ADB device by address (host:port).

        Raises on failure or timeout so callers can react instead of
        silently receiving an error string.
        """
        return self._connect_device(device)

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
        """List devices visible to the exporter's ADB server."""
        try:
            result = subprocess.run(
                [self.adb_path, "devices", "-l"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self.adb_env(),
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to list devices: {e}")
            return f"Error: {e}"
