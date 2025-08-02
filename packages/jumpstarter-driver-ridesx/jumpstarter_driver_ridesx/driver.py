import asyncio
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

from jumpstarter_driver_opendal.driver import Opendal

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class RideSXDriver(Driver):
    """RideSX Driver"""

    storage_dir: str = field(default="/var/lib/jumpstarter/ridesx")

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "serial" not in self.children:
            raise ConfigurationError("'serial' instance is required")

        Path(self.storage_dir).mkdir(parents=True, exist_ok=True)
        self.children["storage"] = Opendal(scheme="fs", kwargs={"root": self.storage_dir})

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ridesx.client.RideSXClient"

    def _get_decompression_command(self, filename: str) -> str:
        if filename.endswith((".gz", ".gzip")):
            return "zcat"
        elif filename.endswith(".xz"):
            return "xzcat"
        else:
            return "cat"

    def _needs_decompression(self, filename: str) -> bool:
        return filename.endswith((".gz", ".gzip", ".xz"))

    def _decompress_file(self, compressed_file: Path) -> Path:
        if compressed_file.name.endswith(".xz"):
            decompressed_name = compressed_file.name[:-3]
        elif compressed_file.name.endswith(".gz"):
            decompressed_name = compressed_file.name[:-3]
        elif compressed_file.name.endswith(".gzip"):
            decompressed_name = compressed_file.name[:-5]
        else:
            return compressed_file

        decompressed_file = compressed_file.parent / decompressed_name

        if decompressed_file.exists():
            self.logger.info(f"decompressed file already exists: {decompressed_name}")
            return decompressed_file

        self.logger.info(f"decompressing {compressed_file.name} to {decompressed_name}")

        decompress_cmd = self._get_decompression_command(compressed_file.name)

        try:
            cmd = f"{decompress_cmd} '{compressed_file}' > '{decompressed_file}'"
            self.logger.debug(f"running decompression command: {cmd}")

            with open(decompressed_file, "wb") as output_file:
                self.logger.debug(f"running decompression command: {decompress_cmd} {compressed_file}")
                result = subprocess.run(
                    [decompress_cmd, str(compressed_file)],
                    stdout=output_file,
                    stderr=subprocess.PIPE,
                    text=False,
                    check=True,
                    timeout=600,
                )

            if result.stderr:
                self.logger.debug(f"decompression stderr: {result.stderr}")

            if not decompressed_file.exists() or decompressed_file.stat().st_size == 0:
                raise RuntimeError("decompression failed: output file is missing or empty")

            self.logger.info(f"successfully decompressed {compressed_file.name}")
            return decompressed_file

        except subprocess.CalledProcessError as e:
            self.logger.error(f"decompression failed - return code: {e.returncode}")
            self.logger.error(f"stdout: {e.stdout}")
            self.logger.error(f"stderr: {e.stderr}")
            raise RuntimeError(f"failed to decompress {compressed_file.name}: {e}") from e
        except subprocess.TimeoutExpired:
            self.logger.error(f"decompression timed out for {compressed_file.name}")
            raise RuntimeError(f"decompression timeout for {compressed_file.name}") from None

    @export
    def detect_fastboot_device(self, max_attempts: int = 5, delay: float = 2.0):
        max_attempts = int(max_attempts)
        delay = float(delay)

        self.logger.info("checking for fastboot devices...")

        for attempt in range(max_attempts):
            try:
                self.logger.debug(f"running: fastboot devices -l (attempt {attempt + 1}/{max_attempts})")
                result = subprocess.run(
                    ["fastboot", "devices", "-l"], capture_output=True, text=True, check=True, timeout=10
                )

                self.logger.debug(f"fastboot devices output: {result.stdout.strip()}")
                self.logger.debug(f"fastboot devices return code: {result.returncode}")

                if result.stdout.strip():
                    device_id = result.stdout.strip().split()[0]
                    self.logger.info(f"Found fastboot device: {device_id}")
                    return {"status": "device_found", "device_id": device_id}
                else:
                    self.logger.warning(f"No fastboot devices found on attempt {attempt + 1}/{max_attempts}")
                    if attempt < max_attempts - 1:
                        time.sleep(delay)
            except subprocess.TimeoutExpired:
                self.logger.warning(f"Fastboot command timed out on attempt {attempt + 1}/{max_attempts}")
                if attempt < max_attempts - 1:
                    time.sleep(delay)
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Fastboot command failed with return code {e.returncode}")
                raise RuntimeError(f"Fastboot command failed: {e}") from e
            except FileNotFoundError:
                raise RuntimeError("fastboot command not found") from None

        self.logger.error("No fastboot devices found after all attempts")
        try:
            self.logger.info("Final attempt with verbose fastboot output...")
            result = subprocess.run(["fastboot", "devices", "-l"], capture_output=True, text=True, timeout=10)
            self.logger.error(f"Final fastboot stdout: '{result.stdout}'")
            self.logger.error(f"Final fastboot stderr: '{result.stderr}'")
        except Exception as e:
            self.logger.error(f"Final fastboot check failed: {e}")

        return {"status": "no_device_found", "device_id": None}

    @export
    def flash_with_fastboot(self, device_id: str, partitions: Dict[str, str]):
        """Flash partitions using fastboot

        Args:
            device_id: The fastboot device ID
            partitions: Dictionary mapping partition names to filenames
        """
        if not partitions:
            raise ValueError("At least one partition must be provided")

        self.logger.info(f"Flashing device {device_id} with partitions: {list(partitions.keys())}")

        for partition_name, filename in partitions.items():
            file_path = Path(self.storage_dir) / filename
            if not file_path.exists():
                raise FileNotFoundError(f"Image not found in storage: {filename}")

            if self._needs_decompression(filename):
                file_path = self._decompress_file(file_path)

            self.logger.info(f"Flashing {partition_name}: {file_path.name}")

            cmd = ["fastboot", "-s", device_id, "flash", partition_name, str(file_path)]
            self.logger.debug(f"Running command: {' '.join(cmd)}")

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=800)
                self.logger.info(f"Successfully flashed {partition_name}")
                self.logger.debug(f"Flash stdout: {result.stdout}")
                if result.stderr:
                    self.logger.debug(f"Flash stderr: {result.stderr}")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to flash {partition_name} - return code: {e.returncode}")
                self.logger.error(f"stdout: {e.stdout}")
                self.logger.error(f"stderr: {e.stderr}")
                raise RuntimeError(f"Failed to flash {partition_name}: {e}") from e
            except subprocess.TimeoutExpired:
                self.logger.error(f"timeout while flashing {partition_name}")
                raise RuntimeError(f"timeout while flashing {partition_name}") from None

        self.logger.info("Running fastboot continue...")
        cmd = ["fastboot", "-s", device_id, "continue"]
        self.logger.debug(f"Running command: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
            self.logger.debug(f"Fastboot continue stdout: {result.stdout}")
            self.logger.debug(f"Fastboot continue stderr: {result.stderr}")
            self.logger.info("Fastboot continue completed successfully")
        except subprocess.CalledProcessError as e:
            self.logger.warning(f"Fastboot continue failed - return code: {e.returncode}")
            self.logger.warning(f"stdout: {e.stdout}")
            self.logger.warning(f"stderr: {e.stderr}")

    @export
    async def boot_to_fastboot(self):
        """Boot device to fastboot mode"""
        self.logger.info("Booting device to fastboot mode")
        commands = [
            ("devicePower 0", 0.5),
            ("ttl outputBit 1 0", 0.5),
            ("gpio volup 0", 0.05),
            ("ttl outputBit 2 1", 0.1),
            ("ttl outputBit 4 0", 0.5),
            ("devicePower 1", 0.9),
            ("ttl outputBit 1 1", 0.8),
            ("ttl outputBit 1 0", 8.1),
            ("ttl outputBit 2 0", 0),
        ]
        serial = self.children["serial"]
        async with serial.connect() as stream:
            for command, delay in commands:
                self.logger.info(f"Executing {command}")
                await stream.send(f"{command}\r".encode())
                data = b""
                while b"ok" not in data:
                    chunk = await stream.receive()
                    data += chunk
                self.logger.debug(f"Command {command} acknowledged with 'ok'")
                prompt = b"CMD >> "
                while prompt not in data:
                    chunk = await stream.receive()
                    data += chunk
                self.logger.debug(f"prompt returned after command: {command}")
                await asyncio.sleep(delay)
        self.logger.info("device should now be in fastboot mode")

    async def _send_power_command(self, command: str):
        """Send a power command to the device via serial"""
        serial = self.children["serial"]
        async with serial.connect() as stream:
            self.logger.info(f"Executing power command: {command}")
            await stream.send(f"{command}\r".encode())
            data = b""
            while b"ok" not in data:
                chunk = await stream.receive()
                data += chunk
            self.logger.debug(f"Command {command} acknowledged with 'ok'")

    @export
    async def power_on(self):
        """Turn device power on"""
        self.logger.info("Turning device power on")
        await self._send_power_command("devicePower 1")

    @export
    async def power_off(self):
        """Turn device power off"""
        self.logger.info("Turning device power off")
        await self._send_power_command("devicePower 0")

    @export
    async def power_cycle(self, delay: float = 1.0):
        """Power cycle the device"""
        self.logger.info(f"Power cycling device with {delay}s delay")
        await self.power_off()
        await asyncio.sleep(delay)
        await self.power_on()


@dataclass(kw_only=True)
class RideSXPowerDriver(Driver):
    """RideSX Power Driver"""

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "serial" not in self.children:
            raise ConfigurationError("'serial' instance is required")

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ridesx.client.RideSXPowerClient"

    async def _send_power_command(self, command: str):
        """Send a power command to the device via serial"""
        serial = self.children["serial"]
        async with serial.connect() as stream:
            self.logger.info(f"Executing power command: {command}")
            await stream.send(f"{command}\r".encode())
            data = b""
            while b"ok" not in data:
                chunk = await stream.receive()
                data += chunk
            self.logger.debug(f"Command {command} acknowledged with 'ok'")

    @export
    async def on(self):
        """Turn device power on"""
        self.logger.info("Turning device power on")
        await self._send_power_command("devicePower 1")

    @export
    async def off(self):
        """Turn device power off"""
        self.logger.info("Turning device power off")
        await self._send_power_command("devicePower 0")

    @export
    async def cycle(self, delay: float = 2):
        """Power cycle the device"""
        self.logger.info(f"Power cycling device with {delay}s delay")
        await self.off()
        await asyncio.sleep(delay)
        await self.on()

    @export
    async def rescue(self):
        """Rescue mode - not implemented for RideSX"""
        raise NotImplementedError("Rescue mode not available for RideSX")
