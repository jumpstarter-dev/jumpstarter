import sys
import termios
import tty
from collections.abc import Awaitable, Callable
from contextlib import contextmanager

from anyio import EndOfStream, create_task_group
from anyio.streams.file import FileReadStream, FileWriteStream

from jumpstarter.client import DriverClient


class ConsoleExit(Exception):
    pass


class ConsoleStreamDrop(Exception):
    """Serial stream dropped; caller may reconnect."""
    pass


class Console:
    def __init__(self, serial_client: DriverClient, on_power_cycle: Callable[[], Awaitable[None]] | None = None):
        self.serial_client = serial_client
        self.on_power_cycle = on_power_cycle

    def run(self):
        with self.setraw():
            self.serial_client.portal.call(self.__run)

    @contextmanager
    def setraw(self):
        original = termios.tcgetattr(sys.stdin.fileno())
        try:
            tty.setraw(sys.stdin.fileno())
            yield
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)

    async def __run(self):
        try:
            async with self.serial_client.stream_async(method="connect") as stream:
                try:
                    async with create_task_group() as tg:
                        tg.start_soon(self.__serial_to_stdout, stream)
                        tg.start_soon(self.__stdin_to_serial, stream)
                except* ConsoleExit:
                    pass
                except* ConsoleStreamDrop:
                    raise ConsoleStreamDrop() from None
        except EndOfStream:
            raise ConsoleStreamDrop() from None

    async def __serial_to_stdout(self, stream):
        stdout = FileWriteStream(sys.stdout.buffer)
        try:
            while True:
                data = await stream.receive()
                await stdout.send(data)
                sys.stdout.flush()
        except EndOfStream:
            raise ConsoleStreamDrop() from None

    async def __stdin_to_serial(self, stream):
        stdin = FileReadStream(sys.stdin.buffer)
        ctrl_b_count = 0
        ctrl_bracket_count = 0  # Ctrl-] x3 triggers power cycle
        while True:
            data = await stdin.receive(max_bytes=1)
            if not data:
                continue
            if data == b"\x02":  # Ctrl-B
                ctrl_b_count += 1
                ctrl_bracket_count = 0
                if ctrl_b_count == 3:
                    raise ConsoleExit
            elif data == b"\x1d":  # Ctrl-]
                ctrl_bracket_count += 1
                ctrl_b_count = 0
                if ctrl_bracket_count == 3 and self.on_power_cycle is not None:
                    await self.on_power_cycle()
                    ctrl_bracket_count = 0
                    continue
            else:
                ctrl_b_count = 0
                ctrl_bracket_count = 0
            await stream.send(data)
