import asyncio
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from jumpstarter_driver_opendal.driver import Opendal

from . import fastboot as fb
from .qdl.soc_profiles import SoCType, get_soc_profile
from .tac import send_power_commands_sequence
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.fls import get_fls_binary
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class RideSXDriver(Driver):
    """RideSX Driver"""

    driver_type = "automotive"

    soc_type: SoCType = field(default="sa8775p")
    decompression_timeout: int = field(default=15 * 60)  # 15 minutes
    flash_timeout: int = field(default=30 * 60)  # 30 minutes
    continue_timeout: int = field(default=20 * 60)  # 20 minutes
    erase_timeout: int = field(default=120)  # 2 minutes
    storage_dir: str = field(default="/var/lib/jumpstarter/ridesx")

    # FLS configuration
    fls_version: str | None = field(default=None)
    fls_allow_custom_binaries: bool = field(
        default=False,
        metadata={
            "help": "⚠️  SECURITY WARNING: Enables downloading custom FLS binaries. Only use in trusted environments."
        }
    )
    fls_custom_binary_url: str | None = field(
        default=None,
        metadata={"help": "Custom URL for FLS binary download. Requires fls_allow_custom_binaries=True."}
    )

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "serial" not in self.children:
            raise ConfigurationError("'serial' instance is required")

        # Security warning for custom binary downloads
        if self.fls_allow_custom_binaries:
            self.logger.warning(
                "⚠️  SECURITY WARNING: Custom FLS binary downloads are enabled. "
                "This allows arbitrary code execution on the exporter host. "
                "Only use this in trusted environments with verified binary sources."
            )

        Path(self.storage_dir).mkdir(parents=True, exist_ok=True)
        self.children["storage"] = Opendal(
            scheme="fs",
            kwargs={"root": self.storage_dir},
            remove_created_on_close=True,  # Clean up temporary firmware files on close
        )

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
                    timeout=self.decompression_timeout,
                )

            if result.stderr:
                self.logger.debug(f"decompression stderr: {result.stderr}")

            if not decompressed_file.exists() or decompressed_file.stat().st_size == 0:
                raise RuntimeError("decompression failed: output file is missing or empty")

            self.logger.info(f"successfully decompressed {compressed_file.name}")

            # Register with Opendal for automatic cleanup on close
            storage = self.children["storage"]
            relative_path = decompressed_file.relative_to(Path(self.storage_dir))
            storage.register_path(str(relative_path))

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
        return fb.detect_device(
            max_attempts=int(max_attempts),
            delay=float(delay),
        )

    @export
    def flash_with_fastboot(self, device_id: str, partitions: dict[str, str]):
        """Flash partitions using fastboot.

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

            fb.flash(
                partition_name, file_path,
                device_id=device_id, timeout=self.flash_timeout,
            )
            self.logger.info(f"Successfully flashed {partition_name}")

        fb.continue_boot(
            device_id=device_id,
            timeout=self.continue_timeout,
            raise_on_failure=False,
        )

    @staticmethod
    def _validate_oci_url(oci_url: str):
        """Validate that the URL is a proper OCI reference."""
        if oci_url.startswith("oci://"):
            return
        hint = ""
        if ":" in oci_url:
            _, after = oci_url.split(":", 1)
            if after.startswith(("/", "./", "../", "~")):
                hint = (
                    f"\n\nIt looks like '{oci_url}' is a partition:path mapping, not an OCI reference.\n"
                    f"For local files, use: j storage flash -t {oci_url}"
                )
        raise ValueError(
            f"OCI URL must start with oci://, got: {oci_url}"
            f"{hint}"
        )

    def _build_fls_command(self, oci_url, partitions):
        """Build FLS fastboot command and environment."""
        fls_binary = get_fls_binary(
            fls_version=self.fls_version,
            fls_binary_url=self.fls_custom_binary_url,
            allow_custom_binaries=self.fls_allow_custom_binaries,
        )
        fls_cmd = [fls_binary, "fastboot", oci_url]

        if partitions:
            for partition_name, filename in sorted(partitions.items()):
                if not filename or not filename.strip():
                    raise ValueError(
                        f"Partition '{partition_name}' has an empty filename. "
                        "Each partition must have a non-empty filename."
                    )
                fls_cmd.extend(["-t", f"{partition_name}:{filename}"])

        fls_cmd.extend(["--timeout", str(self.flash_timeout)])
        return fls_cmd

    @export
    def flash_oci_image(
        self,
        oci_url: str,
        partitions: dict[str, str] | None = None,
        oci_username: str | None = None,
        oci_password: str | None = None,
    ):
        """Flash OCI image using FLS fastboot CLI

        Args:
            oci_url: OCI image reference (e.g., "quay.io/bzlotnik/ridesx-image:latest")
            partitions: Optional mapping of partition -> filename inside OCI image
            oci_username: Registry username for OCI authentication
            oci_password: Registry password for OCI authentication
        """
        self._validate_oci_url(oci_url)

        if bool(oci_username) != bool(oci_password):
            raise ValueError("OCI authentication requires both --username and --password")

        fls_cmd = self._build_fls_command(oci_url, partitions)

        fls_env = os.environ.copy()
        if oci_username and oci_password:
            fls_env["FLS_REGISTRY_USERNAME"] = oci_username
            fls_env["FLS_REGISTRY_PASSWORD"] = oci_password

        self.logger.info(f"Running FLS fastboot: {' '.join(fls_cmd)}")
        if oci_username:
            self.logger.info("Using OCI registry credentials from environment")

        try:
            result = subprocess.run(
                fls_cmd, capture_output=True, text=True,
                check=True, timeout=self.flash_timeout + 30, env=fls_env,
            )

            self.logger.info("FLS fastboot auto-detection completed successfully")
            self.logger.debug(f"FLS stdout: {result.stdout}")
            if result.stderr:
                self.logger.debug(f"FLS stderr: {result.stderr}")

            return {"status": "success", "output": result.stdout}

        except subprocess.CalledProcessError as e:
            self.logger.error(f"FLS fastboot failed - return code: {e.returncode}")
            self.logger.error(f"stdout: {e.stdout}")
            self.logger.error(f"stderr: {e.stderr}")
            output = (e.stderr or e.stdout or "").strip()
            raise RuntimeError(f"FLS fastboot failed: {output}") from e

        except subprocess.TimeoutExpired:
            self.logger.error("FLS fastboot auto-detection timed out")
            raise RuntimeError("FLS fastboot auto-detection timeout") from None

        except FileNotFoundError:
            self.logger.error("FLS command not found - ensure FLS is installed and in PATH")
            raise RuntimeError("FLS command not found") from None

    @export
    def erase_partition(self, device_id: str, partition: str) -> dict[str, str]:
        """Erase a partition using fastboot.

        Args:
            device_id: The fastboot device ID
            partition: The partition name to erase (e.g., 'recoveryinfo')
        """
        if not partition or not partition.strip():
            raise ValueError("Partition name cannot be empty")

        self.logger.info(f"Erasing partition '{partition}' on device {device_id}")
        result = fb.erase(partition, device_id=device_id, timeout=self.erase_timeout)
        self.logger.info(f"Successfully erased partition '{partition}'")
        return {"status": "success", "partition": partition, "output": result.stdout}

    @export
    async def boot_to_fastboot(self):
        """Boot device to fastboot mode using the configured SoC profile."""
        profile = get_soc_profile(self.soc_type)
        self.logger.info("Booting device to fastboot mode (profile: %s)", profile.name)
        await send_power_commands_sequence(
            self.children["serial"],
            self.logger,
            profile.fastboot_commands,
        )
        self.logger.info("Device should now be in fastboot mode")


@dataclass(kw_only=True)
class RideSXPowerDriver(Driver):
    """RideSX Power Driver"""

    driver_type = "power"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "serial" not in self.children:
            raise ConfigurationError("'serial' instance is required")

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ridesx.client.RideSXPowerClient"

    @export
    async def on(self):
        """Turn device power on"""
        self.logger.info("Turning device power on")
        commands = [
            ("devicePower 1", 0.9),
            ("usbDevicePower 1", 0),
            ("gpio vbusdis1 0", 0.03),
        ]
        await send_power_commands_sequence(self.children["serial"], self.logger, commands)

    @export
    async def off(self):
        """Turn device power off"""
        self.logger.info("Turning device power off")
        commands = [
            ("gpio vbusdis1 0", 0),
            ("usbDevicePower 1", 0),
            ("devicePower 0", 0.5),
        ]
        await send_power_commands_sequence(self.children["serial"], self.logger, commands)

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
