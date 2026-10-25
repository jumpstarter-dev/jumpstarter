import logging
import sys
import termios
import tty
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import NoReturn

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream, create_task_group, to_thread
from anyio.streams.file import FileReadStream, FileWriteStream
from jumpstarter_driver_power.client import PowerClient

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
    """Serial stream closed by the exporter (kicked, token revoked, lease ended)."""


async def _exit_console() -> NoReturn:
    """Exit action bound to EXIT_KEY in the hotkey action map."""
    raise ConsoleExit


class HotkeyDetector:
    """Detects a hotkey pressed `presses` times in a row and returns its action.

    Partial sequences are forwarded like ordinary input; only the byte that
    completes a sequence is consumed.
    """

    def __init__(self, actions: dict[bytes, Callable[[], Awaitable[None]]], presses: int = PRESSES):
        self.actions = actions
        self.presses = presses
        self._last: bytes | None = None
        self._count = 0

    def feed(self, byte: bytes) -> Callable[[], Awaitable[None]] | None:
        """Feed a byte and return its action if a hotkey sequence completed."""
        if byte not in self.actions:
            self._last, self._count = None, 0
            return None
        self._count = self._count + 1 if byte == self._last else 1
        self._last = byte
        if self._count < self.presses:
            return None
        self._last, self._count = None, 0
        return self.actions[byte]


class Console:
    def __init__(
        self,
        serial_client: DriverClient,
        observe: bool = False,
        power_control: PowerClient | None = None,
    ):
        self.serial_client = serial_client
        self.observe = observe
        self.power_control = power_control

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

    async def _power_cycle(self):
        assert self.power_control is not None
        try:
            # cycle() is a blocking client call; keep it off the console's event loop
            await to_thread.run_sync(self.power_control.cycle)
        except Exception:
            logger.warning("power cycle failed", exc_info=True)

    async def __read_stdin(self, stream=None):
        stdin = FileReadStream(sys.stdin.buffer)
        actions: dict[bytes, Callable[[], Awaitable[None]]] = {EXIT_KEY: _exit_console}
        if self.power_control is not None:
            actions[POWER_KEY] = self._power_cycle
        hotkeys = HotkeyDetector(actions)
        send_warned = False
        while True:
            data = await stdin.receive(max_bytes=1)
            if not data:
                continue
            action = hotkeys.feed(data)
            if action is not None:
                await action()
                continue
            if stream is not None:
                try:
                    await stream.send(data)
                except STREAM_ERRORS:  # stream breaking; the reader task surfaces the drop
                    if not send_warned:
                        logger.warning("send failed, input may not reach the device")
                    send_warned = True
                else:
                    send_warned = False
