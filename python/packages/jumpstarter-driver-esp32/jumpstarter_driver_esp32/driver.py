import asyncio
import contextlib
import gc
import os
import tempfile
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import esptool.cmds
from anyio import CancelScope, to_thread
from anyio.streams.file import FileReadStream
from esptool.loader import ESPLoader
from jumpstarter_driver_opendal.driver import FlasherInterface

from jumpstarter.client.flasher import FlashPhase, FlashStatus
from jumpstarter.driver import Driver, export


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
        """Connect, load the flasher stub and raise the baud rate.

        The chip is reset into its application once the body succeeds.
        """
        esp = self._connect_esp()
        try:
            if not esp.IS_STUB:
                esp = esp.run_stub()
            if self.flash_baudrate > self.baudrate:
                esp.change_baud(self.flash_baudrate)
            yield esp
            esp.hard_reset()
        finally:
            self._close_esp(esp)

    def _write_images(self, esp, images: list[_Image], *, report: Callable[[FlashStatus], None]):
        """Write every image in order, reporting each one once it is written."""
        for step, image in enumerate(images, 1):
            esptool.cmds.write_flash(esp, [(image.address, image.data)])
            report(
                FlashStatus(
                    phase=FlashPhase.STEP,
                    message="written",
                    step_index=step,
                    total_steps=len(images),
                    step_name=str(image),
                )
            )

    async def _receive(self, source) -> bytes:
        data = bytearray()
        async with self.resource(source) as res:
            async for chunk in res:
                data += chunk
        return bytes(data)

    def _log_status(self, status: FlashStatus):
        self.logger.debug("%s: %s %s", status.step_name, status.message, status.progress)

    @export
    async def flash(self, source, target: str | None = None):
        """Write one image at ``target`` (default 0x0).

        Kept for clients that predate ``flash_images``.
        """
        image = _Image(address=int(target or "0", 0), data=await self._receive(source), name="image")

        def work():
            with self._stub_session() as esp:
                self._write_images(esp, [image], report=self._log_status)

        await to_thread.run_sync(work)

    @export
    async def flash_images(self, source, layout: list[dict]) -> AsyncGenerator[FlashStatus, None]:
        """Write several images in one esptool session, reporting each one.

        ``source`` carries the images back to back, and ``layout`` gives each
        one's ``address``, ``size`` and ``name`` in the same order. The last
        entry may leave out its size to take the rest of the stream.
        """
        images = _split_images(await self._receive(source), layout)

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
            if not worker.done():
                # The client went away mid-flash. Let the write finish so that
                # esptool lets go of the serial port before another call needs it.
                with CancelScope(shield=True):
                    await asyncio.wait({worker})
                if not worker.cancelled() and worker.exception() is not None:
                    self.logger.warning("flash failed after the client left: %s", worker.exception())

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


def _split_images(data: bytes, layout: list[dict]) -> list[_Image]:
    """Cut ``data`` into the images ``layout`` describes, in order.

    Numbers in ``layout`` arrive as floats: driver call arguments travel as
    protobuf ``Value``, which has no integer type.
    """
    images = []
    offset = 0
    for index, entry in enumerate(layout):
        if entry.get("size") is not None:
            size = int(entry["size"])
        elif index == len(layout) - 1:
            size = len(data) - offset
        else:
            raise ValueError("only the last image may leave out its size")
        name = entry.get("name") or f"image {index + 1}"
        images.append(_Image(address=int(entry["address"]), data=data[offset : offset + size], name=name))
        offset += size
    if offset != len(data):
        raise ValueError(f"received {len(data)} bytes, but the images add up to {offset}")
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
