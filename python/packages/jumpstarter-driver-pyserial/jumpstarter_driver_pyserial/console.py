import sys
from contextlib import contextmanager
from functools import partial

import click
from anyio import EndOfStream, create_task_group
from anyio.streams.file import FileReadStream, FileWriteStream

from jumpstarter.client import DriverClient


class ConsoleExit(Exception):
    pass


class Console:
    def __init__(self, serial_client: DriverClient, observe: bool = False):
        self.serial_client = serial_client
        self.observe = observe

    def run(self):
        with self.setraw() as terminal:
            self.serial_client.portal.call(self.__run, terminal)

    @contextmanager
    def setraw(self):
        if sys.platform == "win32":
            from .windows_console import WindowsTerminal

            with WindowsTerminal() as terminal:
                yield terminal
            return

        # Keep the terminal backend separate from portable serial streams and expect APIs.
        try:
            import termios
            import tty
        except ImportError as exc:
            raise click.ClickException(
                "Interactive serial console is not available on this platform. "
                "Use 'pipe' or the stream/pexpect API instead."
            ) from exc

        original = termios.tcgetattr(sys.stdin.fileno())
        try:
            tty.setraw(sys.stdin.fileno())
            yield
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)

    async def __run(self, terminal=None):
        method = "observe" if self.observe else "connect"
        async with self.serial_client.stream_async(method=method) as stream:
            try:
                async with create_task_group() as tg:
                    tg.start_soon(self.__serial_to_stdout, stream, terminal)
                    tg.start_soon(self.__read_stdin, None if self.observe else stream, terminal)
            except* (ConsoleExit, EndOfStream):
                pass

    async def __serial_to_stdout(self, stream, terminal=None):
        stdout = terminal or FileWriteStream(sys.stdout.buffer)
        while True:
            data = await stream.receive()
            await stdout.send(data)
            sys.stdout.flush()

    async def __read_stdin(self, stream=None, terminal=None):
        if terminal is not None:
            with terminal.attach():
                await self.__forward_stdin(terminal.receive, stream)
        else:
            await self.__forward_stdin(partial(FileReadStream(sys.stdin.buffer).receive, max_bytes=1), stream)

    async def __forward_stdin(self, receive, stream):
        ctrl_b_count = 0
        while True:
            data = await receive()
            if not data:
                continue
            for offset, value in enumerate(data):
                ctrl_b_count = ctrl_b_count + 1 if value == 2 else 0
                if ctrl_b_count == 3:
                    if stream is not None and offset:
                        await stream.send(data[:offset])
                    raise ConsoleExit
            if stream is not None:
                await stream.send(data)
