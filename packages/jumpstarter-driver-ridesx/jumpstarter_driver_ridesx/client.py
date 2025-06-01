import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import click
import pexpect
from jumpstarter_driver_opendal.client import OpendalClient, operator_for_path
from jumpstarter_driver_opendal.common import PathBuf
from opendal import Operator

from jumpstarter.client import DriverClient

PROMPT = "CMD >> "

@dataclass(kw_only=True)
class RideSXClient(DriverClient):
    """Client for RideSX"""

    def __post_init__(self):
        super().__post_init__()
        self.logger = logging.getLogger(__name__)

    @property
    def storage(self) -> OpendalClient:
        return self.children["storage"]

    @property
    def serial(self):
        return self.children["serial"]

    def boot_to_fastboot(self):
        self.logger.info("Booting device to fastboot mode")

        commands = [
            ('devicePower 0', 0.5),
            ('ttl outputBit 1 0', 0.5),
            ('gpio volup 0', 0.05),
            ('ttl outputBit 2 1', 0.1),
            ('ttl outputBit 4 0', 0.5),
            ('devicePower 1', 0.9),
            ('ttl outputBit 1 1', 0.8),
            ('ttl outputBit 1 0', 8.1),
            ('ttl outputBit 2 0', 0),
        ]

        with self.serial.pexpect() as p:
            import sys
            p.logfile_read = sys.stdout.buffer

            # try:
            #     p.sendline('')
            #     p.sendline('')

            #     p.expect_exact(PROMPT, timeout=5.0)
            #     self.logger.warning("found initial prompt")
            # except pexpect.TIMEOUT:
            #     raise TimeoutError("did not find initial prompt") from None

            for _, (command, delay) in enumerate(commands, 1):
                self.logger.info(f"Executing {command}")
                command_with_cr = f"{command}\r"
                p.send(command_with_cr.encode())

                try:
                    p.expect_exact('ok', timeout=5.0)
                    self.logger.debug(f"Command {command} acknowledged with 'ok'")
                    p.expect_exact(PROMPT, timeout=2.0)
                    self.logger.debug(f"prompt returned after command: {command}")
                    time.sleep(delay)

                except pexpect.TIMEOUT:
                    self.logger.warning(f"timeout waiting for response after command: {command}")
                except Exception as e:
                    self.logger.warning(f"unexpected response after command {command}: {e}")

        self.logger.info("device should now be in fastboot mode")

    def _upload_file_if_needed(self, file_path: str, operator: Operator | None = None) -> str:
        if operator is None:
            path_buf, operator, operator_scheme = operator_for_path(file_path)
        else:
            path_buf = PathBuf(file_path)
            operator_scheme = "unknown"

        if isinstance(path_buf, Path):
            filename = path_buf.name
        else:
            filename = Path(str(path_buf)).name

        if self.storage.exists(filename):
            self.logger.info(f"File {filename} already exists in storage, skipping upload")
        else:
            if operator_scheme == "http":
                self.logger.info(f"Downloading {file_path} to storage as {filename}")
            else:
                self.logger.info(f"Uploading {file_path} to storage as {filename}")

            self.storage.write_from_path(filename, path_buf, operator=operator)

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

        # Upload all files first
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

        # Call the server with partition mapping
        flash_result = self.call("flash_with_fastboot", device_id, remote_files)

        return flash_result

    def flash(self, partitions: Dict[str, str], operators: Optional[Dict[str, Operator]] = None):
        """Flash partitions to the device"""
        self.logger.info("Starting RideSX flash operation")

        self.boot_to_fastboot()

        self.logger.info("waiting for fastboot mode to be ready...")
        time.sleep(3)

        result = self.flash_images(partitions, operators)

        self.logger.info("flash operation completed successfully")
        return result

    def cli(self):
        @click.group()
        def storage():
            """Storage operations"""
            pass

        @storage.command()
        @click.option('--partition', '-p', multiple=True,
                     help='Partition to flash in format partition:file')
        def flash(partition):
            """Flash partitions to device.

            Examples:
                j storage flash --partition aboot:/path/to/aboot.img --partition rootfs:/path/to/rootfs.img
                j storage flash -p boot:boot.img -p system:system.img -p userdata:userdata.img
            """
            if not partition:
                click.echo("Error: At least one --partition must be provided")
                click.echo("Usage: j storage flash --partition <partition>:<file> [--partition <partition>:<file> ...]")
                raise click.Abort()

            partitions = {}

            for part_spec in partition:
                if ':' not in part_spec:
                    click.echo(f"Error: Invalid partition specification '{part_spec}'. Expected format: partition:file")
                    raise click.Abort()

                partition_name, file_path = part_spec.split(':', 1)
                if not partition_name or not file_path:
                    click.echo(f"Error: Invalid partition specification '{part_spec}'. \
                        Both partition and file must be specified")
                    raise click.Abort()

                partitions[partition_name] = file_path

            try:
                self.flash(partitions)
                click.echo("Flash operation completed successfully")
            except Exception as e:
                click.echo(f"Flash operation failed: {e}")
                raise click.Abort() from e

        return storage
