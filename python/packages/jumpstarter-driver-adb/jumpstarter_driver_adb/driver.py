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

        # Auto-start the ADB server on the configured port
        self.start_server()
        self.logger.info(f"ADB server running on {self.host}:{self.port}")


    def close(self):
        self.kill_server()

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
