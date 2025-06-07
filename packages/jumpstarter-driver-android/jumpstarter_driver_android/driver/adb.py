import os
import shutil
import subprocess
from dataclasses import dataclass

from jumpstarter_driver_network.driver import TcpNetwork

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver.decorators import export


@dataclass(kw_only=True)
class AdbServer(TcpNetwork):
    adb_path: str = "adb"
    host: str = "127.0.0.1"
    port: int = 5037

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_android.client.AdbClient"

    def _print_output(self, output: str, error=False, debug=False):
        if output:
            for line in output.strip().split("\n"):
                if error:
                    self.logger.error(line)
                elif debug:
                    self.logger.debug(line)
                else:
                    self.logger.info(line)

    @export
    def start_server(self):
        """
        Start the ADB server.
        """
        self.logger.debug(f"Starting ADB server on port {self.port}")
        try:
            result = subprocess.run(
                [self.adb_path, "start-server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={"ANDROID_ADB_SERVER_PORT": str(self.port), **dict(os.environ)},
            )
            self._print_output(result.stdout)
            self._print_output(result.stderr, debug=True)
            self.logger.info(f"ADB server started on port {self.port}")
        except subprocess.CalledProcessError as e:
            self._print_output(e.stdout)
            self._print_output(e.stderr, debug=True)
            self.logger.error(f"Failed to start ADB server: {e}")
        return self.port

    @export
    def kill_server(self):
        """
        Kill the ADB server.
        """
        self.logger.debug(f"Killing ADB server on port {self.port}")
        try:
            result = subprocess.run(
                [self.adb_path, "kill-server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={"ANDROID_ADB_SERVER_PORT": str(self.port), **dict(os.environ)},
            )
            self._print_output(result.stdout)
            self._print_output(result.stderr, error=True)
            self.logger.info(f"ADB server stopped on port {self.port}")
        except subprocess.CalledProcessError as e:
            self._print_output(e.stdout)
            self._print_output(e.stderr, error=True)
            self.logger.error(f"Failed to stop ADB server: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error while stopping ADB server: {e}")
        return self.port

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if not isinstance(self.port, int):
            raise ConfigurationError(f"Port must be an integer: {self.port}")

        if self.port < 0 or self.port > 65535:
            raise ConfigurationError(f"Invalid port number: {self.port}")

        self.logger.info(f"ADB server will run on port {self.port}")

        if self.adb_path == "adb":
            self.adb_path = shutil.which("adb")
            if not self.adb_path:
                raise ConfigurationError(f"ADB executable '{self.adb_path}' not found in PATH.")

        try:
            result = subprocess.run(
                [self.adb_path, "version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            self._print_output(result.stdout, debug=True)
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to execute adb: {e}")
