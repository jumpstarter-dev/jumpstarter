from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from subprocess import PIPE, TimeoutExpired
from typing import IO

from jumpstarter_driver_adb.driver import AdbServer
from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.driver import PowerInterface

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class AndroidEmulator(Driver):
    """Android emulator composite driver.

    Manages an Android emulator with ADB tunneling. Children:
    - ``adb``: ADB server for device communication
    - ``power``: Emulator lifecycle (on/off)
    """

    avd_name: str
    emulator_path: str = "emulator"
    headless: bool = True
    console_port: int = 5554
    adb_server_port: int = 15037

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_androidemulator.client.AndroidEmulatorClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        # Resolve emulator binary
        if self.emulator_path == "emulator":
            resolved = shutil.which("emulator")
            if not resolved:
                raise ConfigurationError("Android emulator executable not found in PATH")
            self.emulator_path = resolved

        # Validate ports
        for name, port in [("console_port", self.console_port), ("adb_server_port", self.adb_server_port)]:
            if not isinstance(port, int) or port < 1 or port > 65535:
                raise ConfigurationError(f"Invalid {name}: {port}")

        self.children["adb"] = AdbServer(host="127.0.0.1", port=self.adb_server_port)
        self.children["power"] = AndroidEmulatorPower(parent=self)

        self.logger.info(f"Android emulator configured: AVD={self.avd_name}, port={self.console_port}")

    @export
    def set_headless(self, headless: bool) -> None:
        """Set headless mode. Must be called before power on."""
        self.headless = headless
        self.logger.info(f"Headless mode set to {headless}")


@dataclass(kw_only=True)
class AndroidEmulatorPower(PowerInterface, Driver):
    """Power driver for Android emulator lifecycle management.

    Starts the emulator with a minimal command line and shuts it down
    gracefully via ``adb emu kill``, falling back to process kill.
    """

    parent: AndroidEmulator

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._process: subprocess.Popen | None = None
        self._log_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def _process_logs(self, pipe: IO[bytes], is_stderr: bool = False) -> None:
        """Forward emulator output to the logger."""
        try:
            for line in iter(pipe.readline, b""):
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                if is_stderr:
                    self.logger.error(text)
                elif "|" in text:
                    level_str, message = text.split("|", 1)
                    level_str = level_str.strip().upper()
                    if "ERROR" in level_str or "FATAL" in level_str:
                        self.logger.error(message.strip())
                    elif "WARN" in level_str:
                        self.logger.warning(message.strip())
                    else:
                        self.logger.info(message.strip())
                else:
                    self.logger.info(text)
        except (ValueError, IOError):
            pass
        finally:
            pipe.close()

    @export
    def on(self) -> None:
        """Start the Android emulator."""
        if self._process is not None:
            self.logger.warning("Emulator already running, ignoring")
            return

        cmdline = [
            self.parent.emulator_path,
            "-avd",
            self.parent.avd_name,
            "-port",
            str(self.parent.console_port),
            "-no-audio",
            "-no-boot-anim",
            "-skip-adb-auth",
        ]
        if self.parent.headless:
            cmdline += ["-no-window"]

        env = {**os.environ, "ANDROID_ADB_SERVER_PORT": str(self.parent.adb_server_port)}

        self.logger.info(f"Starting emulator: {' '.join(cmdline)}")
        self._process = subprocess.Popen(
            cmdline,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            env=env,
        )

        self._log_thread = threading.Thread(target=self._process_logs, args=(self._process.stdout,), daemon=True)
        self._stderr_thread = threading.Thread(
            target=self._process_logs, args=(self._process.stderr, True), daemon=True
        )
        self._log_thread.start()
        self._stderr_thread.start()

    @export
    def off(self) -> None:  # noqa: C901
        """Stop the Android emulator."""
        if self._process is None or self._process.returncode is not None:
            self.logger.warning("Emulator not running, ignoring")
            return

        # Try graceful shutdown via ADB
        try:
            adb_path = shutil.which("adb") or "adb"
            subprocess.run(
                [adb_path, "-s", f"emulator-{self.parent.console_port}", "emu", "kill"],
                env={**os.environ, "ANDROID_ADB_SERVER_PORT": str(self.parent.adb_server_port)},
                timeout=5,
                capture_output=True,
            )
            self._process.wait(timeout=15)
            self.logger.info("Emulator shut down gracefully")
        except (TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            self.logger.warning("Graceful shutdown failed, killing process")
            try:
                self._process.kill()
            except ProcessLookupError:
                pass

        # Cleanup threads
        for thread in [self._log_thread, self._stderr_thread]:
            if thread is not None:
                thread.join(timeout=2)

        self._process = None
        self._log_thread = None
        self._stderr_thread = None

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        """Return dummy power readings (emulator has no real power metrics)."""
        yield PowerReading(voltage=0.0, current=0.0)

    def close(self):
        self.off()
