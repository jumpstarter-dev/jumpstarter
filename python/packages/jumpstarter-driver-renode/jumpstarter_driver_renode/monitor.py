from __future__ import annotations

import logging

from anyio import connect_tcp, fail_after, sleep
from anyio.abc import SocketAttribute, SocketStream

logger = logging.getLogger(__name__)


class RenodeMonitorError(Exception):
    """Raised when a Renode monitor command returns an error."""


class RenodeMonitor:
    """Async client for Renode's telnet monitor interface.

    Uses anyio.connect_tcp (the project's standard for TCP connections)
    to communicate with Renode's built-in monitor port. The protocol is
    line-oriented text over TCP.
    """

    _stream: SocketStream | None = None
    _buffer: bytes = b""
    _expected_prompts: set[bytes]

    def __init__(self) -> None:
        self._stream = None
        self._buffer = b""
        self._expected_prompts = {b"monitor"}

    def add_expected_prompt(self, name: str) -> None:
        """Register a machine name so its prompt is recognised."""
        self._expected_prompts.add(name.encode())

    async def connect(self, host: str, port: int, timeout: float = 10) -> None:
        """Connect to the Renode monitor, retrying until the prompt appears."""
        with fail_after(timeout):
            while True:
                try:
                    self._stream = await connect_tcp(host, port)
                    self._buffer = b""
                    await self._read_until_prompt()
                    logger.info("connected to Renode monitor at %s:%d", host, port)
                    return
                except OSError:
                    if self._stream is not None:
                        try:
                            await self._stream.aclose()
                        except Exception:
                            pass
                        self._stream = None
                    await sleep(0.5)

    _ERROR_MARKERS = ("Could not find", "Error", "Invalid", "Failed", "Unknown")

    async def execute(self, command: str, timeout: float = 30) -> str:
        """Send a command and return the response text (excluding the prompt).

        Raises RenodeMonitorError if the response indicates a command failure.
        Raises ValueError if the command contains newline characters.
        """
        if self._stream is None:
            raise RuntimeError("not connected to Renode monitor")

        if "\n" in command or "\r" in command:
            raise ValueError("monitor commands must not contain newline characters")

        logger.debug("monitor> %s", command)
        await self._stream.send(f"{command}\n".encode())
        with fail_after(timeout):
            response = await self._read_until_prompt()
        logger.debug("monitor< %s", response.strip())

        stripped = response.strip()
        if stripped:
            for line in stripped.splitlines():
                if any(line.startswith(m) for m in self._ERROR_MARKERS):
                    raise RenodeMonitorError(stripped)

        return response

    async def disconnect(self) -> None:
        """Close the monitor connection."""
        if self._stream is not None:
            try:
                await self._stream.aclose()
            except Exception:
                pass
            self._stream = None
            self._buffer = b""

    def close_sync(self) -> None:
        """Best-effort synchronous close of the monitor connection.

        Used during synchronous driver teardown when an event loop may
        not be available for ``await disconnect()``.
        """
        stream = self._stream
        self._stream = None
        self._buffer = b""
        if stream is not None:
            try:
                raw_sock = stream.extra(SocketAttribute.raw_socket)
                raw_sock.close()
            except Exception:
                pass

    async def _read_until_prompt(self) -> str:
        """Read from the stream until a monitor prompt line is detected.

        Returns the text received before the prompt.
        """
        if self._stream is None:
            raise RuntimeError("not connected to Renode monitor")

        while True:
            prompt_pos = self._find_prompt()
            if prompt_pos is not None:
                text_before = self._buffer[:prompt_pos].decode(errors="replace")
                self._buffer = self._buffer[prompt_pos:]
                prompt_end = self._buffer.find(b"\n")
                if prompt_end >= 0:
                    self._buffer = self._buffer[prompt_end + 1 :]
                else:
                    self._buffer = b""
                return text_before

            data = await self._stream.receive(4096)
            if not data:
                raise ConnectionError("Renode monitor connection closed")
            self._buffer += data

    def _find_prompt(self) -> int | None:
        """Find a Renode monitor prompt in the buffer.

        Renode prompts look like "(monitor) " or "(machine-name) ".
        Only matches prompts whose inner text is in _expected_prompts.
        """
        for line_start in self._iter_line_starts():
            line = self._buffer[line_start:]
            line_end = line.find(b"\n")
            if line_end < 0:
                candidate = line
            else:
                candidate = line[:line_end]
            candidate = candidate.rstrip()
            if self._is_prompt(candidate):
                return line_start
        return None

    def _iter_line_starts(self):
        """Yield byte offsets where lines begin in the buffer."""
        yield 0
        pos = 0
        while True:
            nl = self._buffer.find(b"\n", pos)
            if nl < 0:
                break
            yield nl + 1
            pos = nl + 1

    def _is_prompt(self, line: bytes) -> bool:
        """Check if a line is a known Renode monitor prompt."""
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith(b"(") and stripped.endswith(b")"):
            inner = stripped[1:-1]
            if inner in self._expected_prompts:
                return True
        return False
