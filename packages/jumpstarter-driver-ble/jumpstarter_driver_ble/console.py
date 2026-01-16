import sys
import termios
import tty
from contextlib import contextmanager

from anyio import create_task_group
from anyio.streams.file import FileReadStream, FileWriteStream

from jumpstarter.client import DriverClient


class BleConsoleExit(Exception):
    pass


class BleConsole:
    def __init__(self, ble_client: DriverClient):
        self.ble_client = ble_client

    def run(self):
        with self.setraw():
            self.ble_client.portal.call(self.__run)

    @contextmanager
    def setraw(self):
        original = termios.tcgetattr(sys.stdin.fileno())
        try:
            tty.setraw(sys.stdin.fileno())
            yield
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)

    async def __run(self):
        async with self.ble_client.stream_async(method="connect") as stream:
            try:
                async with create_task_group() as tg:
                    tg.start_soon(self.__ble_to_stdout, stream)
                    tg.start_soon(self.__stdin_to_ble, stream)
            except* BleConsoleExit:
                pass

    async def __ble_to_stdout(self, stream):
        stdout = FileWriteStream(sys.stdout.buffer)
        while True:
            data = await stream.receive()
            await stdout.send(data)
            sys.stdout.flush()

    async def __stdin_to_ble(self, stream):
        stdin = FileReadStream(sys.stdin.buffer)
        ctrl_b_count = 0
        while True:
            data = await stdin.receive(max_bytes=1)
            if not data:
                continue
            if data == b"\x02":  # Ctrl-B
                ctrl_b_count += 1
                if ctrl_b_count == 3:
                    raise BleConsoleExit
            else:
                ctrl_b_count = 0
            await stream.send(data)
