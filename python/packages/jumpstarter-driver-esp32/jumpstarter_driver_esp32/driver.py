import asyncio
import contextlib
import gc
import logging
import os
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import esptool.cmds
from anyio import CancelScope, to_thread
from anyio.streams.file import FileReadStream
from esptool.loader import ESPLoader
from esptool.logger import TemplateLogger, log
from jumpstarter_driver_opendal.driver import FlasherInterface

from jumpstarter.client.flasher import FlashPhase, FlashStatus
from jumpstarter.common.resources import PresignedRequestResource
from jumpstarter.driver import Driver, export
from jumpstarter.streams.encoding import Compression, create_decompressor

# Least time between two progress updates for one image. The first and the
# last update of each image are always sent.
PROGRESS_INTERVAL = 1.0

_progress = threading.local()
_esptool_logger = logging.getLogger("esptool")


class _ExporterLogger(TemplateLogger):
    """Route esptool's output into the exporter.

    esptool reports through one process-wide logger. Write progress goes to
    the flash that a worker thread registered in ``_progress``; messages go
    to the ``esptool`` logger. It keeps no instance state, because esptool
    5.2 installs a logger by swapping the class of its own logger object.
    """

    def print(self, *args, **kwargs):
        message = " ".join(str(arg) for arg in args).strip()
        if message:
            _esptool_logger.info(message)

    def note(self, message: str):
        _esptool_logger.info(message)

    def warning(self, message: str):
        _esptool_logger.warning(message)

    def error(self, message: str):
        _esptool_logger.error(message)

    def stage(self, finish: bool = False):
        pass

    def progress_bar(self, cur_iter, total_iters, prefix="", suffix="", bar_length=30):
        callback = getattr(_progress, "callback", None)
        if callback is not None:
            callback(cur_iter, total_iters)

    def set_verbosity(self, verbosity: str):
        pass


log.set_logger(_ExporterLogger())


@dataclass(frozen=True)
class _Image:
    address: int
    data: bytes
    name: str

    def __str__(self) -> str:
        return f"{self.name} @ {self.address:#x}"


@dataclass(kw_only=True)
class Esp32Flasher(FlasherInterface, Driver):
    """ESP32 flasher driver for Jumpstarter.

    Requires a PySerial child driver named "serial" for serial port access.
    """

    baudrate: int = 115200
    flash_baudrate: int = 460800
    chip: str = "esp32"

    @property
    def _serial(self):
        return self.children["serial"]

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_esp32.client.Esp32FlasherClient"

    def _connect_esp(self):
        port = self._serial.url
        self.logger.info("_connect_esp: releasing serial port %s via close()...", port)
        self._serial.close()
        self.logger.debug("_connect_esp: serial port released, calling esptool.detect_chip on %s", port)
        try:
            esp = esptool.cmds.detect_chip(
                port=port,
                baud=self.baudrate,
                connect_mode="default_reset",
                trace_enabled=False,
                connect_attempts=7,
            )
        except Exception as e:
            self.logger.debug("_connect_esp: detect_chip failed on %s", port)
            e.__traceback__ = None
            self._force_release_port(port)
            raise
        self.logger.debug("_connect_esp: connected to %s", esp.get_chip_description())  # ty: ignore[unresolved-attribute]
        return esp

    def _close_esp(self, esp):
        port_path = None
        with contextlib.suppress(Exception):
            if hasattr(esp, "_port") and esp._port:
                port_path = getattr(esp._port, "portstr", None) or getattr(esp._port, "name", None)
                esp._port.close()
                esp._port = None
        if port_path:
            self._force_release_port(port_path)

    def _force_release_port(self, port: str):
        """Force-close any leaked file descriptors pointing to the serial port.

        Scans /proc/self/fd/ for symlinks resolving to the port device and
        closes them. This handles cases where esptool's internal objects
        (ESPLoader, slip_reader generator, exception chains) keep the fd alive.
        """
        gc.collect()
        try:
            real_port = os.path.realpath(port)
        except (OSError, ValueError):
            return
        fd_dir = "/proc/self/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            return
        for fd_name in fds:
            try:
                fd_path = os.path.realpath(os.path.join(fd_dir, fd_name))
                if fd_path == real_port:
                    fd_num = int(fd_name)
                    os.close(fd_num)
                    self.logger.debug("_force_release_port: closed leaked fd %d for %s", fd_num, port)
            except (OSError, ValueError):
                continue

    @contextmanager
    def _stub_session(self) -> Generator[Any, None, None]:
        """Connect, load the flasher stub and switch to ``flash_baudrate``.

        The chip is reset into its application once the body succeeds.
        """
        esp = self._connect_esp()
        try:
            esp = esp.run_stub()
            esp.change_baud(self.flash_baudrate)
            yield esp
            esp.hard_reset()
        finally:
            self._close_esp(esp)

    def _write_image(
        self,
        esp,
        image: _Image,
        *,
        report: Callable[[FlashStatus], None],
        step: int,
        steps: int,
    ):
        """Write one image, reporting esptool's progress."""
        last_report: float | None = None

        def status(message: str, progress: float | None) -> FlashStatus:
            return FlashStatus(
                phase=FlashPhase.STEP,
                message=message,
                step_index=step,
                total_steps=steps,
                step_name=str(image),
                progress=progress,
            )

        def on_progress(done: int, total: int):
            nonlocal last_report
            now = time.monotonic()
            if done < total and last_report is not None and now - last_report < PROGRESS_INTERVAL:
                return
            last_report = now
            report(status("writing", done / total))

        _progress.callback = on_progress
        try:
            esptool.cmds.write_flash(esp, [(image.address, image.data)])
        finally:
            _progress.callback = None

    def _write_images(self, esp, images: list[_Image], *, report: Callable[[FlashStatus], None]):
        """Write every image in order."""
        for step, image in enumerate(images, 1):
            self._write_image(esp, image, report=report, step=step, steps=len(images))

    async def _receive(self, source) -> bytes:
        data = bytearray()
        async with self.resource(source) as res:
            async for chunk in res:
                data += chunk
        return bytes(data)

    @export
    async def flash(self, source, target: str | None = None):
        """Write one image at ``target`` (default 0x0).

        Kept for clients that predate ``flash_images``.
        """
        image = _Image(address=int(target or "0", 0), data=await self._receive(source), name="image")
        async for _ in self._flash([image]):
            pass

    @export
    async def flash_images(
        self, source, layout: list[dict], compression: Compression | None = None
    ) -> AsyncGenerator[FlashStatus, None]:
        """Write several images in one esptool session, reporting progress.

        ``layout`` gives each image's ``address`` and ``name``, in order, and
        where it comes from: ``size`` bytes of ``source``, which carries those
        images back to back, or a ``url`` to download. Every image is
        decompressed with ``compression``.
        """
        upload = await self._receive(source)
        downloads = [await self._receive(_get(entry["url"])) for entry in layout if "url" in entry]
        images = _split_images(upload, layout, downloads, compression)
        async for status in self._flash(images):
            yield status

    async def _flash(self, images: list[_Image]) -> AsyncGenerator[FlashStatus, None]:
        """Write ``images`` in one esptool session, yielding progress as it goes."""
        loop = asyncio.get_running_loop()
        updates: asyncio.Queue[FlashStatus | None] = asyncio.Queue()

        def report(status: FlashStatus | None):
            loop.call_soon_threadsafe(updates.put_nowait, status)

        def work():
            try:
                with self._stub_session() as esp:
                    report(FlashStatus(phase=FlashPhase.STEP, message=f"Connected to {esp.get_chip_description()}"))
                    self._write_images(esp, images, report=report)
            finally:
                report(None)

        # The worker has to keep running across each yield, which a task group
        # inside an async generator cannot do. Jumpstarter runs on asyncio, so
        # a plain task is safe.
        worker = loop.create_task(to_thread.run_sync(work))
        try:
            while (status := await updates.get()) is not None:
                yield status
            await worker
        finally:
            # If the client went away mid-flash, let the write finish so that
            # esptool lets go of the serial port before another call needs it.
            with CancelScope(shield=True):
                await asyncio.wait({worker})

        yield FlashStatus(phase=FlashPhase.COMPLETE, message=f"Flashed {len(images)} image(s)")

    @export
    async def dump(self, target, partition: str | None = None):
        address, size = _parse_region(partition)
        with _temporary_filename() as filename:

            def _do_read():
                with self._stub_session() as esp:
                    esptool.cmds.read_flash(esp, address, size, filename)

            await to_thread.run_sync(_do_read)

            async with await FileReadStream.from_path(filename) as stream, self.resource(target) as res:
                async for chunk in stream:
                    await res.send(chunk)

    @export
    def get_chip_info(self) -> dict[str, str]:
        esp = self._connect_esp()
        try:
            mac = esp.read_mac()
            return {
                "chip": esp.get_chip_description(),
                "features": ", ".join(esp.get_chip_features()),
                "mac": ":".join(f"{b:02x}" for b in mac),
            }
        finally:
            self._close_esp(esp)

    @export
    def erase(self):
        with self._stub_session() as esp:
            esptool.cmds.erase_flash(esp)

    @export
    def hard_reset(self):
        """Hard reset the ESP32 via DTR/RTS toggle.

        On boards with the classic auto-program circuit (two cross-coupled NPN
        transistors), EN is only pulled low when DTR and RTS are in opposite
        states.  We must explicitly de-assert DTR (pin high) before asserting
        RTS (pin low) so that the pair (DTR=1, RTS=0) drives EN low.
        """
        self._serial.set_dtr(False)
        self._serial.set_rts(True)
        time.sleep(0.1)
        self._serial.set_rts(False)

    @export
    def enter_bootloader(self):
        """Enter ESP32 download mode via the classic auto-program circuit.

        Matches esptool's ClassicReset sequence: opposite DTR/RTS states drive
        EN and IO0 through cross-coupled NPN transistors.
        """
        self._serial.set_dtr(False)
        self._serial.set_rts(True)   # DTR=1, RTS=0 → EN low, IO0 high (reset)
        time.sleep(0.1)
        self._serial.set_dtr(True)
        self._serial.set_rts(False)  # DTR=0, RTS=1 → EN high, IO0 low (boot select)
        time.sleep(0.05)
        self._serial.set_dtr(False)  # DTR=1, RTS=1 → EN high, IO0 high (release)


def _get(url: str) -> dict:
    """A resource handle that has the exporter download ``url`` itself."""
    return PresignedRequestResource(headers={}, url=url, method="GET").model_dump(mode="json")


def _split_images(
    upload: bytes, layout: list[dict], downloads: list[bytes], compression: Compression | None
) -> list[_Image]:
    """Build the images ``layout`` describes, in order.

    Entries with a ``url`` take the next of ``downloads``; the others take
    the next ``size`` bytes of ``upload``. Each image is decompressed on its
    own: concatenated compressed files do not decompress as one stream.

    Numbers in ``layout`` arrive as floats: driver call arguments travel as
    protobuf ``Value``, which has no integer type.
    """
    images = []
    offset = 0
    remaining = iter(downloads)
    for entry in layout:
        if "url" in entry:
            data = next(remaining)
        else:
            size = int(entry["size"])
            data = upload[offset : offset + size]
            offset += size
        if compression is not None:
            data = create_decompressor(Compression(compression)).decompress(data)
        image = _Image(address=int(entry["address"]), data=data, name=entry["name"])
        if not image.data:
            # esptool would skip it with a warning, as if it matched the flash
            raise ValueError(f"{image} is empty")
        images.append(image)
    if offset != len(upload):
        raise ValueError(f"received {len(upload)} bytes, but the images add up to {offset}")
    _check_no_overlap(images)
    return images


def _check_no_overlap(images: list[_Image]):
    """Refuse images that share a flash sector: writing one would erase part of the other.

    Mirrors the check the esptool CLI applies to its address/file pairs,
    which its write_flash() API leaves to the caller.
    """
    sector = ESPLoader.FLASH_SECTOR_SIZE
    previous = None
    end = 0
    for image in sorted(images, key=lambda image: image.address):
        if previous is not None and image.address & ~(sector - 1) < end:
            raise ValueError(f"{image} overlaps {previous}")
        end = (image.address + len(image.data) + sector - 1) & ~(sector - 1)
        previous = image


def _parse_region(partition: str | None) -> tuple[int, int]:
    if partition is None:
        return (0x0, 0x400000)
    if ":" in partition:
        addr_str, size_str = partition.split(":", 1)
        return (int(addr_str, 0), int(size_str, 0))
    return (int(partition, 0), 0x400000)


@contextmanager
def _temporary_filename():
    fd, name = tempfile.mkstemp()
    os.close(fd)
    try:
        yield name
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
