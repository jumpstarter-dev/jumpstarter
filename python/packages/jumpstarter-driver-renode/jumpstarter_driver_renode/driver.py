from __future__ import annotations

import logging
import shutil
import socket
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import DEVNULL, Popen, TimeoutExpired
from tempfile import TemporaryDirectory

from anyio import to_thread
from anyio.streams.file import FileWriteStream
from jumpstarter_driver_opendal.driver import FlasherInterface
from jumpstarter_driver_power.driver import PowerInterface, PowerReading
from jumpstarter_driver_pyserial.driver import PySerial

from .monitor import RenodeMonitor
from jumpstarter.driver import Driver, export

logger = logging.getLogger(__name__)

_ELF_MAGIC = b"\x7fELF"

_ALLOWED_LOAD_COMMANDS = frozenset(
    {
        "sysbus LoadELF",
        "sysbus LoadBinary",
        "sysbus LoadSymbolsFrom",
    }
)


def _detect_load_command(firmware_path: str) -> str:
    """Choose the appropriate Renode load command based on file contents."""
    try:
        with open(firmware_path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return "sysbus LoadELF"
    if magic == _ELF_MAGIC:
        return "sysbus LoadELF"
    return "sysbus LoadBinary"


def _find_free_port() -> int:
    # NOTE: TOCTOU race — the port is released before Renode binds it,
    # so another process could grab it first.  Switching to Unix domain
    # sockets would eliminate this, but Renode does not yet support them.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_renode() -> str:
    path = shutil.which("renode")
    if path is None:
        raise FileNotFoundError("renode executable not found in PATH. Install Renode from https://renode.io/")
    return path


@dataclass(kw_only=True)
class RenodeFlasher(FlasherInterface, Driver):
    parent: Renode

    @export
    async def flash(self, source, load_command: str | None = None):
        """Flash firmware to the simulated MCU.

        If the simulation is not yet running, stores the firmware for
        loading during power-on. If already running, loads the firmware
        and resets the machine.
        """
        if load_command is not None and load_command not in _ALLOWED_LOAD_COMMANDS:
            raise ValueError(f"unsupported load_command {load_command!r}, allowed: {sorted(_ALLOWED_LOAD_COMMANDS)}")

        firmware_path = self.parent._tmp_dir.name + "/firmware"
        async with await FileWriteStream.from_path(firmware_path) as stream:
            async with self.resource(source) as res:
                async for chunk in res:
                    await stream.send(chunk)

        if load_command is not None:
            cmd = load_command
        else:
            cmd = _detect_load_command(firmware_path)
        self.parent.set_firmware(firmware_path, cmd)

        power: RenodePower = self.parent.children["power"]  # ty: ignore[invalid-assignment]
        if power.is_running:
            await power.send_monitor_command(f'{cmd} @"{firmware_path}"')
            await power.send_monitor_command("machine Reset")
            self.logger.info("firmware hot-loaded and machine reset")

    @export
    async def dump(self, target, partition: str | None = None):
        """Not supported for Renode targets."""
        raise NotImplementedError("dump is not supported for Renode targets")


@dataclass(kw_only=True)
class RenodePower(PowerInterface, Driver):
    """Power controller that manages the Renode process lifecycle."""

    parent: Renode

    _process: Popen | None = field(init=False, default=None, repr=False)
    _monitor: RenodeMonitor | None = field(init=False, default=None, repr=False)

    @property
    def is_running(self) -> bool:
        """Whether the Renode process is running with an active monitor."""
        return self._process is not None and self._monitor is not None

    async def send_monitor_command(self, command: str) -> str:
        """Send a command to the Renode monitor.

        Provides a public interface for sibling drivers to interact with
        the monitor without accessing private attributes directly.
        """
        if self._monitor is None:
            raise RuntimeError("Renode is not running")
        return await self._monitor.execute(command)

    @export
    async def on(self) -> None:
        """Start Renode, connect monitor, configure platform, and begin simulation."""
        if self._process is not None:
            self.logger.warning("already powered on, ignoring request")
            return

        renode_bin = _find_renode()
        port = self.parent.monitor_port or _find_free_port()
        self.parent._active_monitor_port = port

        cmdline = [
            renode_bin,
            "--disable-xwt",
            "--plain",
            "--port",
            str(port),
        ]

        self.logger.info("starting Renode: %s", " ".join(cmdline))
        self._process = Popen(cmdline, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)

        self._monitor = RenodeMonitor()
        try:
            await self._monitor.connect("127.0.0.1", port)
            await self._configure_simulation()
            await self._monitor.execute("start")
            self.logger.info("Renode simulation started")
        except Exception:
            await self.off()
            raise

    async def _configure_simulation(self) -> None:
        """Set up the machine, platform, UART, and firmware in the monitor."""
        machine = self.parent.machine_name
        self._monitor.add_expected_prompt(machine)
        await self._monitor.execute(f'mach create "{machine}"')
        await self._monitor.execute(f"machine LoadPlatformDescription @{self.parent.platform}")

        pty_path = self.parent._pty
        await self._monitor.execute(f'emulation CreateUartPtyTerminal "term" "{pty_path}"')
        await self._monitor.execute(f"connector Connect {self.parent.uart} term")

        for cmd in self.parent.extra_commands:
            await self._monitor.execute(cmd)

        if self.parent._firmware_path:
            load_cmd = self.parent._load_command or "sysbus LoadELF"
            await self._monitor.execute(f'{load_cmd} "{self.parent._firmware_path}"')

    @export
    async def off(self) -> None:
        """Stop simulation, disconnect monitor, and terminate the Renode process."""
        if self._process is None:
            self.logger.warning("already powered off, ignoring request")
            return

        if self._monitor is not None:
            try:
                await self._monitor.execute("quit")
            except Exception:
                pass
            await self._monitor.disconnect()
            self._monitor = None

        try:
            self._process.terminate()
            try:
                await to_thread.run_sync(self._process.wait, 5)
            except TimeoutExpired:
                self._process.kill()
        except ProcessLookupError:
            pass
        finally:
            self._process = None

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        """Not supported — Renode does not provide power readings."""
        raise NotImplementedError

    def close(self):
        """Synchronous cleanup for use during driver teardown."""
        if self._process is not None:
            if self._monitor is not None:
                self._monitor.close_sync()
                self._monitor = None
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except TimeoutExpired:
                    self._process.kill()
            except ProcessLookupError:
                pass
            finally:
                self._process = None


@dataclass(kw_only=True)
class Renode(Driver):
    """Renode emulation framework driver for Jumpstarter.

    Provides a composite driver that manages a Renode simulation instance
    with power control, firmware flashing, and serial console access.

    Users inject their Renode target configuration via YAML without
    modifying driver code:

    - ``platform``: path to a ``.repl`` file or Renode built-in name
    - ``uart``: peripheral path in the Renode object model
    - ``extra_commands``: list of monitor commands for target-specific setup
    """

    platform: str
    uart: str = "sysbus.uart0"
    machine_name: str = "machine-0"
    monitor_port: int = 0
    extra_commands: list[str] = field(default_factory=list)
    allow_raw_monitor: bool = False

    _tmp_dir: TemporaryDirectory = field(init=False, default_factory=TemporaryDirectory)
    _firmware_path: str | None = field(init=False, default=None)
    _load_command: str | None = field(init=False, default=None)
    _active_monitor_port: int = field(init=False, default=0)

    @classmethod
    def client(cls) -> str:
        """Return the fully-qualified client class name."""
        return "jumpstarter_driver_renode.client.RenodeClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.children["power"] = RenodePower(parent=self)
        self.children["flasher"] = RenodeFlasher(parent=self)
        self.children["console"] = PySerial(url=self._pty, check_present=False)

    def set_firmware(self, path: str, load_command: str) -> None:
        """Set the firmware path and load command for the next power-on."""
        self._firmware_path = path
        self._load_command = load_command

    @property
    def _pty(self) -> str:
        return str(Path(self._tmp_dir.name) / "pty")

    @export
    def get_platform(self) -> str:
        """Return the Renode platform description path."""
        return self.platform

    @export
    def get_uart(self) -> str:
        """Return the UART peripheral path in the Renode object model."""
        return self.uart

    @export
    def get_machine_name(self) -> str:
        """Return the Renode machine name."""
        return self.machine_name

    @export
    async def monitor_cmd(self, command: str) -> str:
        """Send a command to the Renode monitor.

        Requires ``allow_raw_monitor: true`` in the exporter configuration.
        """
        if not self.allow_raw_monitor:
            raise RuntimeError(
                "raw monitor access is disabled; set allow_raw_monitor: true in exporter config to enable"
            )
        power: RenodePower = self.children["power"]  # ty: ignore[invalid-assignment]
        return await power.send_monitor_command(command)
