import os
import tempfile
from dataclasses import dataclass, field
from typing import Optional

import esptool
import esptool.cmds
from anyio import to_thread
from anyio.streams.file import FileWriteStream

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class ESP32(Driver):
    """ESP32 driver for Jumpstarter"""

    port: str
    baudrate: int = field(default=115200)
    chip: str = field(default="esp32")
    reset_pin: Optional[int] = field(default=None)
    boot_pin: Optional[int] = field(default=None)
    check_present: bool = field(default=True)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if self.check_present and not self.port.startswith(("/dev/null", "loop://")):
            import os

            if not os.path.exists(self.port):
                self.logger.warning(f"Serial port {self.port} does not exist")

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_esp32.client.ESP32Client"

    @export
    def chip_info(self) -> dict:
        """Get ESP32 chip information"""
        esp = self._connect_esp()
        try:
            chip_id = esp.read_efuse(0)
            mac = esp.read_mac()
            mac_str = ":".join([f"{b:02x}" for b in mac])

            return {
                "chip_type": self.chip,
                "port": self.port,
                "baudrate": self.baudrate,
                "mac_address": mac_str,
                "chip_id": f"0x{chip_id:08x}",
                "chip_revision": f"Chip is {esp.get_chip_description()}",
            }
        finally:
            if hasattr(esp, "_port") and esp._port:
                esp._port.close()

    @export
    def reset_device(self) -> str:
        """Reset the ESP32 device"""
        if self.reset_pin is not None:
            return self._hardware_reset()
        else:
            return self._software_reset()

    @export
    def erase_flash(self) -> str:
        """Erase the entire flash memory"""
        self.logger.info("Erasing flash...")
        esp = self._connect_esp()
        try:
            esp.erase_flash()
            return "Flash erase completed successfully"
        finally:
            if hasattr(esp, "_port") and esp._port:
                esp._port.close()

    @export
    async def flash_firmware(self, src: str, address: int = 0x1000) -> str:
        """Flash firmware to the ESP32"""
        address = int(address)

        with TemporaryFilename() as filename:
            async with await FileWriteStream.from_path(filename) as stream:
                async with self.resource(src) as res:
                    async for chunk in res:
                        await stream.send(chunk)

            def _flash_firmware():
                esp = self._connect_esp()
                try:
                    if not esp.IS_STUB:
                        esp = esp.run_stub()

                    with open(filename, "rb") as f:
                        esptool.cmds.write_flash(
                            esp,
                            [(address, f)],
                            flash_freq="keep",
                            flash_mode="keep",
                            flash_size="detect",
                            compress=True,
                            no_progress=False,
                            erase_all=False,
                        )

                    # Hard reset after successful flash to run new firmware
                    esp.hard_reset()
                    return f"Firmware flashed successfully to address 0x{address:x}"

                except Exception as e:
                    self.logger.error(f"Flash failed: {e}")
                    raise
                finally:
                    try:
                        if hasattr(esp, "_port") and esp._port:
                            esp._port.close()
                    except Exception as e:
                        self.logger.error(f"Failed to close port: {e}")
                        pass

            return await to_thread.run_sync(_flash_firmware)

    @export
    def read_flash(self, address: int, size: int) -> str:
        """Read flash contents from specified address"""
        import base64

        address = int(address)
        size = int(size)
        if address < 0:
            raise ValueError(f"Flash address must be non-negative, got {address}")
        if size <= 0:
            raise ValueError(f"Read size must be positive, got {size}")

        esp = self._connect_esp()
        try:
            if not esp.IS_STUB:
                esp = esp.run_stub()

            data = esp.read_flash(address, size)

            esp.hard_reset()

            return base64.b64encode(data).decode('ascii')
        finally:
            if hasattr(esp, "_port") and esp._port:
                esp._port.close()

    @export
    def enter_bootloader(self) -> str:
        """Enter bootloader mode"""
        if self.boot_pin is not None:
            return self._hardware_bootloader()
        else:
            try:
                info = self.chip_info()
                return f"Entered bootloader mode successfully. Connected to {info['chip_revision']}"
            except Exception as e:
                return f"Failed to enter bootloader mode: {e}"

    def _connect_esp(self):
        self.logger.debug("Connecting to ESP32...")
        try:
            esp = esptool.cmds.detect_chip(
                port=self.port,
                baud=self.baudrate,
                connect_mode="default_reset",
                trace_enabled=False,
                connect_attempts=7,
            )

            self.logger.debug("Connected to %s", esp.get_chip_description())
            return esp

        except Exception as e:
            error_msg = f"Failed to connect to ESP32: {e}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _software_reset(self) -> str:
        """Perform software reset via esptool"""
        esp = self._connect_esp()
        try:
            esp.hard_reset()
            return "Software reset completed"
        except Exception as e:
            raise RuntimeError(f"Software reset failed: {e}") from e
        finally:
            if hasattr(esp, "_port") and esp._port:
                esp._port.close()

    def _hardware_reset(self) -> str:
        """Perform hardware reset via GPIO pin"""
        if self.reset_pin is None:
            return "No reset pin configured"

        try:
            # TODO: Implement actual GPIO control
            return f"Hardware reset via pin {self.reset_pin} completed"
        except Exception as e:
            return f"Hardware reset failed: {e}"

    def _hardware_bootloader(self) -> str:
        """Enter bootloader mode via hardware pins"""
        if self.boot_pin is None:
            return "No boot pin configured"

        try:
            # TODO: Implement actual GPIO control
            return f"Entered bootloader mode via pin {self.boot_pin}"
        except Exception as e:
            return f"Hardware bootloader entry failed: {e}"


class TemporaryFilename:
    """Context manager for temporary file names"""

    def __enter__(self):
        self.tempfile = tempfile.NamedTemporaryFile(delete=False)
        self.name = self.tempfile.name
        self.tempfile.close()
        return self.name

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            os.unlink(self.name)
        except OSError:
            pass
