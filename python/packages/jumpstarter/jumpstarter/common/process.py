from __future__ import annotations

import os
import signal
import subprocess
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from os import PathLike
from typing import Literal

from anyio import (
    CancelScope,
    EndOfStream,
    create_task_group,
    move_on_after,
    notify_closing,
    open_process,
    wait_readable,
)
from anyio.abc import Process
from anyio.lowlevel import checkpoint

OutputSource = Literal["stdout", "stderr"]

_READ_SIZE = 65536


@dataclass(frozen=True, slots=True)
class OutputChunk:
    source: OutputSource
    data: bytes


@dataclass(frozen=True, slots=True)
class _Iterator:
    receive: Callable[[], Awaitable[OutputChunk]]

    def __aiter__(self) -> _Iterator:
        return self

    async def __anext__(self) -> OutputChunk:
        try:
            return await self.receive()
        except EndOfStream:
            raise StopAsyncIteration from None


class _Pipe:
    """The read end of a pipe the child writes to.

    The pipes are not left to asyncio: its Process.wait() waits for every pipe it
    manages to reach end of file, so a background job holding one open would keep
    an exited process from ever being waited for.
    """

    def __init__(self, fd: int) -> None:
        os.set_blocking(fd, False)
        self.fd = fd

    async def receive(self) -> bytes:
        await checkpoint()
        while True:
            try:
                data = os.read(self.fd, _READ_SIZE)
            except BlockingIOError:
                await wait_readable(self.fd)
                continue
            if not data:
                raise EndOfStream
            return data

    def close(self) -> None:
        notify_closing(self.fd)
        os.close(self.fd)


class StreamingProcess:
    """A running subprocess whose stdout and stderr are read as tagged chunks or lines.

    Read one process either by chunks (``receive`` or ``async for``) or by lines
    (``receive_line`` or ``lines()``), not both.
    """

    def __init__(
        self,
        process: Process,
        pipes: dict[OutputSource, _Pipe],
        *,
        new_process_group: bool,
        stop_grace: float,
        max_line: int,
    ) -> None:
        self._process = process
        self._open = pipes
        self._new_process_group = new_process_group
        self._stop_grace = stop_grace
        self._max_line = max_line
        self._pending: deque[OutputChunk] = deque()
        self._partial: dict[OutputSource, bytes] = {"stdout": b"", "stderr": b""}

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def receive(self) -> OutputChunk:
        """Return the next chunk from either pipe; raise EndOfStream once both have ended."""
        while not self._pending:
            if not self._open:
                raise EndOfStream
            await self._read_any()
        return self._pending.popleft()

    async def receive_line(self) -> OutputChunk:
        """Return the next line, keeping its newline; a pipe's last line may lack one."""
        while (line := self._pop_line()) is None:
            try:
                chunk = await self.receive()
            except EndOfStream:
                if (rest := self._pop_rest()) is not None:
                    return rest
                raise
            self._partial[chunk.source] += chunk.data
        return line

    def __aiter__(self) -> AsyncIterator[OutputChunk]:
        return _Iterator(self.receive)

    def lines(self) -> AsyncIterator[OutputChunk]:
        return _Iterator(self.receive_line)

    async def wait(self) -> int:
        return await self._process.wait()

    def send_signal(self, sig: signal.Signals) -> None:
        """Signal the process, or its whole group if it leads one; a no-op once it has exited."""
        if self._process.returncode is not None:
            return
        with suppress(ProcessLookupError):
            if self._new_process_group:
                os.killpg(self._process.pid, sig)
            else:
                self._process.send_signal(sig)

    def terminate(self) -> None:
        self.send_signal(signal.SIGTERM)

    async def stop(self) -> int:
        """SIGTERM, then SIGKILL after the stop grace period; also completes while cancelled."""
        with CancelScope(shield=True):
            if self._stop_grace > 0:
                self.terminate()
                with move_on_after(self._stop_grace):
                    await self._process.wait()
            self.send_signal(signal.SIGKILL)
            returncode = await self._process.wait()
        return returncode

    async def _read_any(self) -> None:
        if len(self._open) == 1:
            await self._read(*next(iter(self._open.items())))
            return
        # The task group ends with this call: these processes are read from async
        # generators that grpc.aio may finalize in another task, so no scope may
        # stay open across their yield. Chunks go to the instance queue, so one
        # received just as the caller's timeout fires is kept for the next call.
        async with create_task_group() as tg:

            async def read_then_stop(source: OutputSource, pipe: _Pipe) -> None:
                await self._read(source, pipe)
                if self._pending:
                    tg.cancel_scope.cancel()

            for source, pipe in list(self._open.items()):
                tg.start_soon(read_then_stop, source, pipe)

    async def _read(self, source: OutputSource, pipe: _Pipe) -> None:
        try:
            data = await pipe.receive()
        except EndOfStream:
            del self._open[source]
            pipe.close()
        else:
            self._pending.append(OutputChunk(source, data))

    def _close_pipes(self) -> None:
        for pipe in self._open.values():
            pipe.close()
        self._open.clear()

    def _pop_line(self) -> OutputChunk | None:
        for source, buffered in self._partial.items():
            newline = buffered.find(b"\n")
            if newline != -1 and newline < self._max_line:
                end = newline + 1
            elif len(buffered) >= self._max_line:
                end = self._max_line
            else:
                continue
            self._partial[source] = buffered[end:]
            return OutputChunk(source, buffered[:end])
        return None

    def _pop_rest(self) -> OutputChunk | None:
        for source, buffered in self._partial.items():
            if buffered:
                self._partial[source] = b""
                return OutputChunk(source, buffered)
        return None


@asynccontextmanager
async def open_streaming_process(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | PathLike[str] | None = None,
    stderr: Literal["pipe", "stdout", "inherit"] = "pipe",
    new_process_group: bool = False,
    stop_grace: float = 5.0,
    max_line: int = 65536,
) -> AsyncGenerator[StreamingProcess]:
    """Start *command* with an empty stdin and stream its output.

    Leaving the context stops the process if it is still running, also when the
    caller is cancelled. With *new_process_group* the process starts a new session
    and stopping it signals the whole group.
    """
    stdout_read, stdout_write = os.pipe()
    stderr_read, stderr_write = os.pipe() if stderr == "pipe" else (None, None)
    try:
        process = await open_process(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=stdout_write,
            stderr=stderr_write if stderr == "pipe" else subprocess.STDOUT if stderr == "stdout" else None,
            env=env,
            cwd=cwd,
            start_new_session=new_process_group,
        )
    except BaseException:
        os.close(stdout_read)
        if stderr_read is not None:
            os.close(stderr_read)
        raise
    finally:
        os.close(stdout_write)
        if stderr_write is not None:
            os.close(stderr_write)

    pipes: dict[OutputSource, _Pipe] = {"stdout": _Pipe(stdout_read)}
    if stderr_read is not None:
        pipes["stderr"] = _Pipe(stderr_read)
    streaming = StreamingProcess(
        process, pipes, new_process_group=new_process_group, stop_grace=stop_grace, max_line=max_line
    )
    try:
        yield streaming
    finally:
        with CancelScope(shield=True):
            await streaming.stop()
            streaming._close_pipes()
            await process.aclose()
