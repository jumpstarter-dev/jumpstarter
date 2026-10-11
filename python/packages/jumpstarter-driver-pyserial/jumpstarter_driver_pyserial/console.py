import logging
import sys
import termios
import tty
from collections.abc import Awaitable, Callable
from contextlib import contextmanager, suppress

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream, create_task_group
from anyio.streams.file import FileReadStream, FileWriteStream

from jumpstarter.client import DriverClient

logger = logging.getLogger(__name__)

EXIT_KEY = b"\x02"  # Ctrl-B
POWER_KEY = b"\x1d"  # Ctrl-]
PRESSES = 3

# receive() raises EndOfStream on a clean GOAWAY/EOF; send() only ever raises
# BrokenResourceError (streams/router.py:40-44). Treat the set uniformly.
STREAM_ERRORS = (EndOfStream, BrokenResourceError, ClosedResourceError)


class ConsoleExit(Exception):
    pass


class ConsoleStreamDrop(Exception):
    """Serial stream dropped; caller may reconnect."""


class HotkeyDetector:
    """Detects repeated key presses for hotkey sequences."""

    def __init__(self, keys: set[bytes], presses: int = PRESSES):
        self.keys = keys
        self.presses = presses
        self._last: bytes | None = None
        self._count = 0

    def feed(self, byte: bytes) -> bytes | None:
        """Feed a byte and return the key if hotkey sequence detected."""
        if byte not in self.keys:
            self._last, self._count = None, 0
            return None
        self._count = self._count + 1 if byte == self._last else 1
        self._last = byte
        if self._count < self.presses:
            return None
        self._last, self._count = None, 0
        return byte


class Console:
    def __init__(
        self,
        serial_client: DriverClient,
        observe: bool = False,
        on_power_cycle: Callable[[], Awaitable[None]] | None = None,
    ):
        self.serial_client = serial_client
        self.observe = observe
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
            method = "observe" if self.observe else "connect"
            async with self.serial_client.stream_async(method=method) as stream:
                try:
                    async with create_task_group() as tg:
                        tg.start_soon(self.__serial_to_stdout, stream)
                        tg.start_soon(self.__read_stdin, None if self.observe else stream)
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
        except STREAM_ERRORS:
            raise ConsoleStreamDrop() from None

    async def __read_stdin(self, stream=None):
        stdin = FileReadStream(sys.stdin.buffer)
        keys = {EXIT_KEY, POWER_KEY} if self.on_power_cycle is not None else {EXIT_KEY}
        hotkeys = HotkeyDetector(keys)
        while True:
            data = await stdin.receive(max_bytes=1)
            if not data:
                continue
            hit = hotkeys.feed(data)
            if hit == EXIT_KEY:
                raise ConsoleExit
            if hit == POWER_KEY:
                await self.on_power_cycle()
                continue
            if stream is not None:
                with suppress(*STREAM_ERRORS):
                    await stream.send(data)
