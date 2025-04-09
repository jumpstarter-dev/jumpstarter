import sys
import termios
import tty
from contextlib import contextmanager

from anyio import create_task_group
from anyio.streams.file import FileReadStream, FileWriteStream

from jumpstarter.client import DriverClient


class ConsoleExit(Exception):
    pass


class Console:
    def __init__(self, serial_client: DriverClient):
        self.serial_client = serial_client

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
            # Clear screen and move cursor to top-left (like \033c\033[2J\033[H).
            print("\033c\033[2J\033[H", end="")

    async def __run(self):
        async with self.serial_client.stream_async(method="connect") as stream:
            try:
                async with create_task_group() as tg:
                    tg.start_soon(self.__serial_to_stdout, stream)
                    tg.start_soon(self.__stdin_to_serial, stream)
            except* ConsoleExit:
                pass

    async def __serial_to_stdout(self, stream):
        stdout = FileWriteStream(sys.stdout.buffer)
        while True:
            data = await stream.receive()
            await stdout.send(data)
            sys.stdout.flush()

    async def __stdin_to_serial(self, stream):
        stdin = FileReadStream(sys.stdin.buffer)
        ctrl_b_count = 0
        while True:
            data = await stdin.receive(max_bytes=1)
            if not data:
                continue
            if data == b"\x02":  # Ctrl-B
                ctrl_b_count += 1
                if ctrl_b_count == 3:
                    raise ConsoleExit
            else:
                ctrl_b_count = 0
            await stream.send(data)
