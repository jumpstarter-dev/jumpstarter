import os
import tarfile
import tempfile
import time
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from anyio import CancelScope, EndOfStream, current_time, fail_after, to_thread
from anyio.streams.file import FileWriteStream
from jumpstarter_driver_composite.driver import CompositeInterface
from jumpstarter_driver_pyserial.driver import PySerial

from jumpstarter.client.flasher import FlashPhase, FlashStatus
from jumpstarter.common.process import OutputChunk, StreamingProcess, open_streaming_process
from jumpstarter.driver import Driver, export
from jumpstarter.driver.flasher import StreamingFlasherInterface
from jumpstarter.streams.progress import ProgressAttribute

# Fixed-name firmware artifacts the client uploads, keyed by the token exposed
# to the flash command. Order matters only for logging.
ARTIFACT_FILES = {
    "hex": "zephyr.hex",
    "bin": "zephyr.bin",
    "elf": "zephyr.elf",
}


def _extract(archive_path: str, dest: str) -> None:
    """Extract the uploaded tar into ``dest`` and delete it. Blocking."""
    with tarfile.open(archive_path) as tar:
        tar.extractall(dest, filter="data")
    os.unlink(archive_path)


def _remaining(deadline: float | None) -> float | None:
    """Seconds left until ``deadline`` (None: no deadline), for fail_after()."""
    return None if deadline is None else max(deadline - current_time(), 0)


# How long an interrupted flash command gets to exit after SIGTERM before SIGKILL.
_TERMINATE_GRACE = 5.0

# Minimum interval between two DOWNLOAD progress updates, in seconds.
_PROGRESS_INTERVAL = 0.5


@dataclass(kw_only=True)
class Zephyr(StreamingFlasherInterface, CompositeInterface, Driver):
    """Flash Zephyr firmware to a board attached to the exporter.

    This is the exporter-side half of the "server-side twister" workflow: the
    test runner (twister) runs on the client/CI machine and only ships the built
    firmware image(s) here; the exporter owns *how* to flash. The flash command
    and the board's probe/target configuration live entirely in this driver's
    config, so the client stays board-agnostic.

    Progress is reported as :class:`~jumpstarter.client.flasher.FlashStatus`
    updates, so the client is the stock
    :class:`~jumpstarter.client.flasher.StreamingFlasherClient` plus the
    Zephyr-specific commands.

    The client uploads a small tar of fixed-name artifacts (``zephyr.hex`` and,
    if present, ``zephyr.bin`` / ``zephyr.elf``). The driver extracts them into a
    temporary directory and runs ``flash_command`` from that directory.

    The command may reference the uploaded files either by bare name (it runs
    with cwd set to the upload directory) or via the tokens ``{hex}``, ``{bin}``,
    ``{elf}`` and ``{dir}``, which are substituted with absolute paths.

    Example exporter config::

        export:
          zephyr:
            type: jumpstarter_driver_zephyr.driver.Zephyr
            config:
              flash_command: >-
                openocd -f interface/cmsis-dap.cfg -f target/cc13x2_cc26x2.cfg
                -c "program {hex} verify reset exit"
    """

    flash_command: str
    """Shell command used to flash the board (required).

    Run via ``bash -c`` with cwd set to the directory holding the uploaded
    firmware. Supports the ``{hex}``/``{bin}``/``{elf}``/``{dir}`` tokens
    described above. The board's probe/target configuration belongs here, not on
    the client.
    """

    flash_timeout: int = 300
    """Seconds before the flash command, and everything it started, is stopped. 0 disables."""

    release_consoles: bool = False
    """Close every PySerial child's tty while the flash command runs.

    Only needed when the flash tool opens the same tty as the console (e.g.
    esptool). Off by default: while the tty is closed, whatever the board prints
    is lost, including its first output after the reset that ends the flash.
    """

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_zephyr.client.ZephyrClient"

    def _resolve_files(self, build_dir: str) -> dict[str, str]:
        """Map artifact tokens to absolute paths for the files that were sent.

        Always includes ``dir`` (the upload directory). Each of hex/bin/elf is
        included only if that file was actually uploaded.
        """
        files: dict[str, str] = {"dir": build_dir}
        for token, name in ARTIFACT_FILES.items():
            path = os.path.join(build_dir, name)
            if os.path.isfile(path):
                files[token] = path
        return files

    def _resolve_command(self, files: dict[str, str]) -> str:
        """Substitute the {hex}/{bin}/{elf}/{dir} tokens in the flash command.

        Uses plain replacement (not str.format) so literal braces in the command
        — e.g. an openocd ``-c`` argument — are left untouched.
        """
        cmd = self.flash_command
        for token in ("hex", "bin", "elf", "dir"):
            cmd = cmd.replace("{" + token + "}", files.get(token, ""))
        return cmd

    async def _stream_cmd(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> AsyncGenerator[str, None]:
        """Run a subprocess and yield merged stdout/stderr line by line.

        Yielding each line as it arrives keeps the gRPC stream active during long
        flashes (so HTTP/2 keepalive doesn't trip) and gives the client a live
        view. Raises RuntimeError on non-zero exit so the failure propagates to
        the client (and therefore to twister's flash step).

        The command runs in its own process group. If the stream stops before
        the command exits (client disconnect, Ctrl-C, cancellation), or the
        command is still running ``timeout`` seconds after it started, the
        whole group is stopped, including children that outlive the command, so
        no flash tool is left holding the probe or the tty.
        """
        self.logger.debug("Running command: %s", " ".join(cmd))

        deadline = current_time() + timeout if timeout else None
        tail: deque[str] = deque(maxlen=50)
        async with open_streaming_process(
            cmd, cwd=cwd, stderr="stdout", new_process_group=True, stop_grace=_TERMINATE_GRACE
        ) as process:
            try:
                while (chunk := await self._next_line(process, deadline)) is not None:
                    line = chunk.data.decode("utf-8", errors="replace").rstrip("\r\n")
                    tail.append(line)
                    yield line
                with fail_after(_remaining(deadline)):
                    rc = await process.wait()
            except TimeoutError:
                self.logger.warning("Flash command timed out after %ss, stopping it", timeout)
                await process.stop()
                tail_text = "\n".join(tail)
                raise RuntimeError(f"flash command timed out after {timeout}s:\n{tail_text}") from None
            finally:
                if process.returncode is None:
                    self.logger.warning("Flash interrupted, terminating the flash command")

        if rc != 0:
            tail_text = "\n".join(tail)
            raise RuntimeError(f"flash command failed (rc={rc}):\n{tail_text}")

    @staticmethod
    async def _next_line(process: StreamingProcess, deadline: float | None) -> OutputChunk | None:
        """The command's next output line, or None once its output has ended.

        Raises TimeoutError at ``deadline``. The timeout scope lives here, not in
        the generator above: a scope must not stay open across a ``yield``.
        """
        with fail_after(_remaining(deadline)):
            try:
                return await process.receive_line()
            except EndOfStream:
                return None

    def _serial_children(self) -> list[PySerial]:
        """PySerial children whose tty must be freed while flashing.

        Only PySerial implements release()/acquire(), which close and reopen the
        tty so a flasher that shares it (e.g. a CMSIS-DAP probe's ACM port, or
        esptool) can open it. All of them are released, whatever their name:
        attached clients only see a pause.
        """
        return [child for child in self.children.values() if isinstance(child, PySerial)]

    async def _receive(self, src: str, archive_path: str) -> AsyncGenerator[FlashStatus, None]:
        """Write the uploaded archive to ``archive_path``, reporting progress."""
        received = 0
        last_update = time.monotonic()
        async with await FileWriteStream.from_path(archive_path) as stream, self.resource(src) as res:
            total = res.extra(ProgressAttribute.total, None)
            bytes_total = int(total) if total else None
            async for chunk in res:
                await stream.send(chunk)
                received += len(chunk)
                now = time.monotonic()
                if now - last_update >= _PROGRESS_INTERVAL:
                    last_update = now
                    yield FlashStatus(
                        phase=FlashPhase.DOWNLOAD,
                        message="Receiving firmware",
                        bytes_transferred=received,
                        bytes_total=bytes_total,
                    )
        yield FlashStatus(
            phase=FlashPhase.DOWNLOAD,
            message="Received firmware archive",
            bytes_transferred=received,
            bytes_total=bytes_total,
        )

    async def _flash(self, src: str) -> AsyncGenerator[FlashStatus, None]:
        consoles = self._serial_children() if self.release_consoles else []

        with tempfile.TemporaryDirectory(prefix="zephyr-flash-") as build_dir:
            archive_path = os.path.join(build_dir, ".upload.tar")

            async with aclosing(self._receive(src, archive_path)) as statuses:
                async for status in statuses:
                    yield status

            await to_thread.run_sync(_extract, archive_path, build_dir)

            files = self._resolve_files(build_dir)
            present = [name for token, name in ARTIFACT_FILES.items() if token in files]
            if not present:
                raise ValueError(
                    f"No firmware artifacts found in upload (expected one of {list(ARTIFACT_FILES.values())})."
                )
            yield FlashStatus(phase=FlashPhase.STEP, message=f"Received firmware: {', '.join(present)}")

            resolved = self._resolve_command(files)
            yield FlashStatus(phase=FlashPhase.STEP, message=f"Flashing with: {resolved}")

            released: list[PySerial] = []
            try:
                for console in consoles:
                    await console.release()
                    released.append(console)

                # aclosing: if this stream is closed early, close the command's
                # stream right away so it reaps the flash process before the
                # consoles are re-acquired below.
                cmd = ["bash", "-c", resolved]
                timeout = self.flash_timeout if self.flash_timeout > 0 else None
                async with aclosing(self._stream_cmd(cmd, cwd=build_dir, timeout=timeout)) as lines:
                    async for line in lines:
                        yield FlashStatus(phase=FlashPhase.STEP, message="Flashing", stdout=line)
            finally:
                with CancelScope(shield=True):
                    for console in released:
                        await console.acquire()

        yield FlashStatus(phase=FlashPhase.COMPLETE, message="Flash complete")

    @export
    async def flash(self, source: str, manifest: Any | None = None) -> AsyncGenerator[FlashStatus, None]:
        """Flash firmware uploaded as a tar of fixed-name artifacts.

        Args:
            source: Streaming resource handle for the tar archive produced by
                the client. The archive contains ``zephyr.hex`` and, when
                present, ``zephyr.bin`` / ``zephyr.elf`` at its top level.
            manifest: Unused. Part of the streaming flasher interface.

        Yields:
            ``DOWNLOAD`` updates while the archive is received, ``STEP``
            updates while flashing (each output line of the flash command in
            ``stdout``), then ``COMPLETE``. A failure ends the stream with an
            ``ERROR`` update carrying the reason, which the client raises.
        """
        try:
            async with aclosing(self._flash(source)) as statuses:
                async for status in statuses:
                    yield status
        except Exception as exc:  # noqa: BLE001 - reported to the client as an ERROR update
            self.logger.error("Flash failed: %s", exc)
            yield FlashStatus(phase=FlashPhase.ERROR, message=str(exc))
