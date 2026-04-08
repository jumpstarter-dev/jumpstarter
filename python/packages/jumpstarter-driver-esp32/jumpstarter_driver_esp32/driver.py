import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass

import esptool.cmds
from anyio import to_thread
from anyio.streams.file import FileReadStream, FileWriteStream
from jumpstarter_driver_opendal.driver import FlasherInterface

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class Esp32Flasher(FlasherInterface, Driver):
    """ESP32 flasher driver for Jumpstarter.

    Requires a PySerial child driver named "serial" for serial port access.
    """

    baudrate: int = 115200
    chip: str = "esp32"

    @property
    def _serial(self):
        return self.children["serial"]

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_esp32.client.Esp32FlasherClient"

    def _connect_esp(self):
        port = self._serial.url
        self.logger.debug("Connecting to ESP32 on %s...", port)
        esp = esptool.cmds.detect_chip(
            port=port,
            baud=self.baudrate,
            connect_mode="default_reset",
            trace_enabled=False,
            connect_attempts=7,
        )
        self.logger.debug("Connected to %s", esp.get_chip_description())  # type: ignore[attr-defined]
        return esp

    def _close_esp(self, esp):
        try:
            if hasattr(esp, "_port") and esp._port:
                esp._port.close()
        except Exception:
            pass

    @export
    async def flash(self, source: str, target: str | None = None):
        """Flash a firmware image to the ESP32."""
        address = int(target or "0", 0)
        with _temporary_filename() as filename:
            async with await FileWriteStream.from_path(filename) as stream:
                async with self.resource(source) as res:
                    async for chunk in res:
                        await stream.send(chunk)

            def _do_flash():
                esp = self._connect_esp()
                try:
                    if not esp.IS_STUB:
                        esp = esp.run_stub()
                    with open(filename, "rb") as f:
                        esptool.cmds.write_flash(esp, [(address, f)])
                    esp.hard_reset()
                finally:
                    self._close_esp(esp)

            await to_thread.run_sync(_do_flash)

    @export
    async def dump(self, target: str, partition: str | None = None):
        """Dump firmware from the ESP32 to a resource handle."""
        address, size = _parse_region(partition)
        with _temporary_filename() as filename:

            def _do_read():
                esp = self._connect_esp()
                try:
                    if not esp.IS_STUB:
                        esp = esp.run_stub()
                    esptool.cmds.read_flash(esp, address, size, filename)
                    esp.hard_reset()
                finally:
                    self._close_esp(esp)

            await to_thread.run_sync(_do_read)

            async with await FileReadStream.from_path(filename) as stream:
                async with self.resource(target) as res:
                    async for chunk in stream:
                        await res.send(chunk)

    @export
    def get_chip_info(self) -> dict[str, str]:
        """Get information about the connected ESP32 chip."""
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
        """Erase the ESP32 flash memory."""
        esp = self._connect_esp()
        try:
            if not esp.IS_STUB:
                esp = esp.run_stub()
            esptool.cmds.erase_flash(esp)
            esp.hard_reset()
        finally:
            self._close_esp(esp)

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
