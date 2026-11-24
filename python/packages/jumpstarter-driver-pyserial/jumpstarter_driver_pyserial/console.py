import logging
import os
import sys
import termios
import tty
from collections.abc import Collection
from contextlib import contextmanager

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream, create_task_group, to_thread, wait_readable
from anyio.abc import TaskGroup
from anyio.streams.file import FileWriteStream
from jumpstarter_driver_power.client import PowerClient

from jumpstarter.client import DriverClient

logger = logging.getLogger(__name__)

EXIT_KEY = b"\x02"  # Ctrl-B
POWER_KEY = b"\x1d"  # Ctrl-]
PRESSES = 3
SEND_CHUNK = 64 * 1024  # gRPC rejects stream messages over 4 MiB

# receive() raises EndOfStream on a clean close; send() only raises BrokenResourceError.
# Treat them uniformly.
STREAM_ERRORS = (EndOfStream, BrokenResourceError, ClosedResourceError)


class ConsoleStreamDrop(Exception):
    """Serial stream closed by the exporter (kicked, token revoked, lease ended)."""


class HotkeyDetector:
    """Detects a hotkey pressed `presses` times in a row and returns it.

    Partial sequences are forwarded like ordinary input; only the byte that
    completes a sequence is consumed.
    """

    def __init__(self, keys: Collection[bytes], presses: int = PRESSES):
        self.keys = keys
        self.presses = presses
        self._last: bytes | None = None
        self._count = 0

    def feed(self, byte: bytes) -> bytes | None:
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
        self._stream = None
        self._typed_ahead = bytearray()
        self._send_warned = False
        self._power_cycling = False
        failure = None
        async with create_task_group() as tg:
            tg.start_soon(self.__read_stdin, tg)
            try:
                method = "observe" if self.observe else "connect"
                async with self.serial_client.stream_async(method=method) as stream:
                    if not self.observe:
                        await self.__attach_input(stream)
                    await self.__serial_to_stdout(stream)
            except Exception as error:  # noqa: BLE001 - re-raised below to avoid an ExceptionGroup
                failure = error
                tg.cancel_scope.cancel()
        if failure is not None:
            raise failure

    async def __serial_to_stdout(self, stream):
        stdout = FileWriteStream(sys.stdout.buffer)
        try:
            while True:
                data = await stream.receive()
                await stdout.send(data)
                sys.stdout.flush()
        except STREAM_ERRORS:
            raise ConsoleStreamDrop() from None

    async def __attach_input(self, stream):
        """Drain held input before publishing the stream, keeping later input in order."""
        while self._typed_ahead:
            data = bytes(self._typed_ahead[:SEND_CHUNK])
            del self._typed_ahead[:SEND_CHUNK]
            await self.__send(stream, data)
        self._stream = stream

    async def __send(self, stream, data: bytes):
        try:
            await stream.send(data)
        except STREAM_ERRORS:  # stream breaking; __serial_to_stdout surfaces the drop
            if not self._send_warned:
                logger.warning("send failed, input may not reach the device")
            self._send_warned = True
        else:
            self._send_warned = False

    async def _power_cycle(self):
        assert self.power_control is not None
        try:
            # cycle() is a blocking client call; keep it off the console's event loop
            await to_thread.run_sync(self.power_control.cycle)
        except Exception:
            logger.warning("power cycle failed", exc_info=True)
        finally:
            self._power_cycling = False

    async def __read_stdin(self, tg: TaskGroup):
        fd = sys.stdin.fileno()
        hotkeys = HotkeyDetector({EXIT_KEY} if self.power_control is None else {EXIT_KEY, POWER_KEY})
        while True:
            # Waited for on the event loop, so that cancelling the console interrupts it.
            # stdin stays blocking: it usually shares its open file description with
            # stdout, whose writes must not fail with EAGAIN.
            await wait_readable(fd)
            data = os.read(fd, 1)
            if not data:
                raise EndOfStream
            key = hotkeys.feed(data)
            if key == EXIT_KEY:
                tg.cancel_scope.cancel()
                return
            if key == POWER_KEY:
                if not self._power_cycling:
                    # Set before scheduling: keys read before the task starts already count as during the cycle.
                    self._power_cycling = True
                    self._typed_ahead.clear()
                    tg.start_soon(self._power_cycle)
            elif self._stream is not None:
                await self.__send(self._stream, data)
            elif not self.observe and not self._power_cycling:
                self._typed_ahead += data
