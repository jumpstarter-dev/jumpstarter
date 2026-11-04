"""A mock board for the Robot Framework examples.

``MockDut`` behaves enough like a small Linux board to show the whole flow
without hardware: its ``power`` child switches it on and off, and its
``console`` child is a serial-like byte stream that prints a boot log and a
login prompt on power on, accepts any login, and answers a few commands.

Children are declared in the exporter config, so the client resource can be
generated offline from it. Drivers of one board share state through their
``board`` name, which also lets one exporter carry several identical boards::

    dut:
      type: jumpstarter_example_robot.mockdut.MockDut
      children:
        power: {type: jumpstarter_example_robot.mockdut.MockDutPower, config: {board: dut}}
        console: {type: jumpstarter_example_robot.mockdut.MockDutConsole, config: {board: dut}}
"""

from __future__ import annotations

import math
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from anyio import create_memory_object_stream, sleep
from anyio.abc import ObjectStream
from jumpstarter_driver_composite.driver import CompositeInterface
from jumpstarter_driver_network.driver import NetworkInterface
from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.driver import PowerInterface

from jumpstarter.driver import Driver, export, exportstream

KERNEL = "6.6.0-mock"

BOOT_LOG = (
    "\r\nU-Boot 2024.01-mock (Jan 01 2024)\r\n"
    "DRAM:  2 GiB\r\n"
    "Starting kernel ...\r\n\r\n"
    f"[    0.000000] Linux version {KERNEL} (mock@jumpstarter) #1 SMP\r\n"
    "[    1.234567] systemd[1]: Reached target Multi-User System.\r\n\r\n"
)


# A console that connects this soon after power on still sees the boot log,
# as if it had been attached already; a stream connection is set up
# asynchronously, so a test that opens the console and powers on right away
# would otherwise race it.
REPLAY_WINDOW = 10.0


@dataclass
class _Board:
    name: str
    powered: bool = False
    booted_at: float = 0.0
    shells: set[FakeShell] = field(default_factory=set)

    def power_on(self) -> None:
        if self.powered:
            return
        self.powered = True
        self.booted_at = time.monotonic()
        for shell in list(self.shells):
            shell.boot()

    def attach(self, shell: FakeShell) -> None:
        self.shells.add(shell)
        if self.powered and time.monotonic() - self.booted_at < REPLAY_WINDOW:
            shell.boot()

    def power_off(self) -> None:
        self.powered = False
        for shell in list(self.shells):
            shell.reset()


_BOARDS: dict[str, _Board] = {}


def board(name: str) -> _Board:
    return _BOARDS.setdefault(name, _Board(name))


class FakeShell(ObjectStream[bytes]):
    """One console connection: a terminal with echo, a login and a tiny shell."""

    def __init__(self, board: _Board, hostname: str):
        self._board = board
        self._hostname = hostname
        self._send, self._receive = create_memory_object_stream[bytes](math.inf)
        self._line = ""
        self._state = "login" if board.powered else "off"
        self._user = "root"

    @property
    def prompt(self) -> str:
        return f"{self._user}@{self._hostname}:~# "

    def _emit(self, text: str) -> None:
        self._send.send_nowait(text.encode())

    def boot(self) -> None:
        self._state = "login"
        self._line = ""
        self._emit(BOOT_LOG + f"{self._hostname} login: ")

    def reset(self) -> None:
        self._state = "off"
        self._line = ""

    async def receive(self) -> bytes:
        return await self._receive.receive()

    async def send(self, item: bytes) -> None:
        if self._state == "off":
            return
        for char in item.decode(errors="replace"):
            if char in "\r\n":
                self._emit("\r\n")
                line, self._line = self._line, ""
                self._handle(line)
            elif char == "\x03":
                self._line = ""
                if self._state == "shell":
                    self._emit("^C\r\n" + self.prompt)
            else:
                self._line += char
                if self._state != "password":
                    self._emit(char)

    def _handle(self, line: str) -> None:
        if self._state == "login":
            if line:
                self._user = line.strip()
                self._state = "password"
                self._emit("Password: ")
            else:
                self._emit(f"{self._hostname} login: ")
        elif self._state == "password":
            self._state = "shell"
            self._emit(f"Welcome to MockOS {KERNEL}\r\n" + self.prompt)
        elif self._state == "shell":
            output = self._run(line.strip())
            self._emit((output + "\r\n" if output else "") + self.prompt)

    def _run(self, command: str) -> str:
        if not command:
            return ""
        name, _, rest = command.partition(" ")
        if command == "uname -r":
            return KERNEL
        if command in ("uname -a", "cat /proc/version"):
            return f"Linux version {KERNEL} {self._hostname}"
        if name == "echo":
            return rest
        if command == "hostname":
            return self._hostname
        if command == "whoami":
            return self._user
        if command == "exit":
            self._state = "login"
            return f"\r\n{self._hostname} login:"
        return f"sh: {name}: not found"

    async def send_eof(self) -> None:
        pass

    async def aclose(self) -> None:
        self._board.shells.discard(self)
        await self._send.aclose()
        await self._receive.aclose()


@dataclass(kw_only=True)
class MockDut(CompositeInterface, Driver):
    """A mock board; declare its ``power`` and ``console`` children in the exporter config."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_example_robot.client.MockDutClient"


@dataclass(kw_only=True)
class MockDutPower(PowerInterface, Driver):
    """Power switch of a mock board; powering on boots the board's console."""

    board: str = "dut"

    @export
    async def on(self) -> None:
        board(self.board).power_on()

    @export
    async def off(self) -> None:
        board(self.board).power_off()

    @export
    async def status(self) -> str:
        return "on" if board(self.board).powered else "off"

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        while True:
            powered = board(self.board).powered
            yield PowerReading(voltage=5.0 if powered else 0.0, current=0.8 if powered else 0.0)
            await sleep(0.1)


@dataclass(kw_only=True)
class MockDutConsole(NetworkInterface, Driver):
    """Serial console of a mock board, as a byte stream."""

    board: str = "dut"
    hostname: str = "mockdut"

    @exportstream
    @asynccontextmanager
    async def connect(self):
        shell = FakeShell(board(self.board), self.hostname)
        board(self.board).attach(shell)
        try:
            yield shell
        finally:
            board(self.board).shells.discard(shell)
