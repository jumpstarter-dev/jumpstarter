from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_opendal.client import FlasherClient, operator_for_path
from jumpstarter_driver_power.client import PowerClient
from opendal import Operator

from jumpstarter.client.decorators import driver_click_group

PROMPT = "CMD >> "


@dataclass(kw_only=True)
class RideSXClient(FlasherClient, CompositeClient):
    """Client for RideSX"""

    def __post_init__(self):
        super().__post_init__()

    def boot_to_fastboot(self):
        return self.call("boot_to_fastboot")

    def _upload_file_if_needed(self, file_path: str, operator: Operator | None = None) -> str:
        if operator is None:
            path_buf, operator, operator_scheme = operator_for_path(file_path)
        else:
            path_buf = Path(file_path)
            operator_scheme = "unknown"

        filename = Path(path_buf).name

        if self._should_upload_file(self.storage, filename, path_buf, operator):
            if operator_scheme == "http":
                self.logger.info(f"Downloading {file_path} to storage as {filename}")
            else:
                self.logger.info(f"Uploading {file_path} to storage as {filename}")

            self.storage.write_from_path(filename, path_buf, operator=operator)
        else:
            self.logger.info(f"File {filename} already exists in storage with matching hash, skipping upload")

        return filename

    def flash_images(self, partitions: Dict[str, str], operators: Optional[Dict[str, Operator]] = None):
        """Flash images to specified partitions

        Args:
            partitions: Dictionary mapping partition names to file paths
            operators: Optional dictionary mapping partition names to operators
        """
        if not partitions:
            raise ValueError("At least one partition must be provided")

        operators = operators or {}
        remote_files = {}

        for partition, file_path in partitions.items():
            self.logger.info(f"Processing {partition} image: {file_path}")
            operator = operators.get(partition)
            remote_files[partition] = self._upload_file_if_needed(file_path, operator)

        self.logger.info("Checking for fastboot devices on Exporter...")
        detection_result = self.call("detect_fastboot_device", 5, 2.0)

        if detection_result["status"] != "device_found":
            raise RuntimeError("No fastboot devices found. Make sure device is in fastboot mode.")

        device_id = detection_result["device_id"]
        self.logger.info(f"found fastboot device: {device_id}")

        flash_result = self.call("flash_with_fastboot", device_id, remote_files)

        return flash_result

    def flash(
        self,
        path: str | Dict[str, str],
        *,
        partition: str | None = None,
        operator: Operator | Dict[str, Operator] | None = None,
        compression=None,
    ):
        if isinstance(path, dict):
            partitions = path
            operators = operator if isinstance(operator, dict) else None
        else:
            if partition is None:
                raise ValueError("'partition' must be provided")
            partitions = {partition: path}
            operators = {partition: operator} if isinstance(operator, Operator) else None

        self.logger.info("Starting RideSX flash operation")

        self.boot_to_fastboot()

        result = self.flash_images(partitions, operators)

        self.logger.info("flash operation completed successfully")
        return result

    def cli(self):
        generic_cli = FlasherClient.cli(self)

        @driver_click_group(self)
        def storage():
            """RideSX storage operations"""
            pass

        for name, cmd in generic_cli.commands.items():
            storage.add_command(cmd, name=name)

        return storage


@dataclass(kw_only=True)
class RideSXPowerClient(PowerClient):
    """Power control client for RideSX"""

    def __post_init__(self):
        super().__post_init__()

    @property
    def serial(self):
        return self.children["serial"]

    def on(self) -> None:
        """Turn device power on"""
        self.call("on")

    def off(self) -> None:
        """Turn device power off"""
        self.call("off")

    def cycle(self, wait: int = 2):
        """Power cycle the device"""
        self.call("cycle", wait)

    def rescue(self) -> None:
        """Rescue mode"""
        self.call("rescue")
