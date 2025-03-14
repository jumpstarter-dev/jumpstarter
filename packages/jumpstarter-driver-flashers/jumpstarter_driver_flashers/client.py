import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PosixPath
from queue import Queue
from urllib.parse import urlparse

import asyncclick as click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_opendal.client import FlasherClient, OpendalClient, operator_for_path
from jumpstarter_driver_opendal.common import PathBuf
from jumpstarter_driver_pyserial.client import Console
from opendal import Operator

from jumpstarter_driver_flashers.bundle import FlasherBundleManifestV1Alpha1

from .uboot import UbootConsole
from jumpstarter.common.exceptions import ArgumentError

debug_console_option = click.option("--console-debug", is_flag=True, help="Enable console debug mode")

@dataclass(kw_only=True)
class BaseFlasherClient(FlasherClient, CompositeClient):
    """
    Client interface for software driven flashing

    This client provides methods to flash and dump images to a device under test (DUT)
    """

    def __post_init__(self):
        super().__post_init__()
        self._manifest = None
        self._console_debug = False

    def set_console_debug(self, debug: bool):
        """Set console debug mode"""
        self._console_debug = debug

    @contextmanager
    def busybox_shell(self):
        """Start a context manager busybox interactive console"""
        # Make the exporter download the bundle contents and set files in the right places
        self.logger.info("Setting up flasher bundle files in exporter")
        self.call("setup_flasher_bundle")
        with self._services_up():
            with self._busybox() as busybox:
                busybox.send("\n\n")
            yield self.serial

    @contextmanager
    def bootloader_shell(self):
        """Start a context manager uboot/bootloader for interactive console"""
        # Make the exporter download the bundle contents and set files in the right places
        self.logger.info("Setting up flasher bundle files in exporter")
        self.call("setup_flasher_bundle")
        with self._services_up():
            with self.serial.pexpect() as console:
                if self._console_debug:
                    console.logfile_read = sys.stdout.buffer
                uboot = UbootConsole(console=console, power=self.power, logger=self.logger)
                uboot.reboot_to_console()
                console.sendline("")
            yield self.serial

    def flash(
        self,
        path: PathBuf,
        *,
        partition: str | None = None,
        operator: Operator | None = None,
        os_image_checksum: str | None = None,
        force_exporter_http: bool = False,
        force_flash_bundle: str | None = None,
    ):
        """Flash image to DUT"""
        skip_exporter_http = False
        image_url = ""
        if path.startswith(("http://")) and not force_exporter_http:
            # busybox can handle the http from a remote directly, unless target is isolated
            image_url = path
            skip_exporter_http = True
        else:
            if operator is None:
                path, operator = operator_for_path(path)
            image_url = self.http.get_url() + "/" + path.name

        # start counting time for the flash operation
        start_time = time.time()

        if not skip_exporter_http:
            # Create a queue to handle exceptions from the thread
            error_queue = Queue()

            # Start the storage write operation in the background
            storage_thread = threading.Thread(target=self._transfer_bg_thread,
                                            args=(path, operator, os_image_checksum, self.http.storage, error_queue),
                                            name="storage_transfer")
            storage_thread.start()

        # Make the exporter download the bundle contents and set files in the right places
        self.logger.info("Setting up flasher bundle files in exporter")
        self.call("setup_flasher_bundle", force_flash_bundle)

        with self._services_up():
            with self._busybox() as console:
                manifest = self.manifest
                target = partition or manifest.spec.default_target
                if not target:
                    raise ArgumentError("No partition or default target specified")

                target_device = self._get_target_device(target, manifest, console)

                self.logger.info(f"Using target block device: {target_device}")

                # Preflash commands are executed before the flash operation
                # generally used to clean up boot entries in existing devices
                for preflash_command in manifest.spec.preflash_commands:
                    self.logger.info(f"Running preflash command: {preflash_command}")
                    console.sendline(preflash_command)
                    console.expect(manifest.spec.login.prompt, timeout=5)

                # make sure that the device is connected to the network and has an IP address
                console.sendline("udhcpc")
                console.expect(manifest.spec.login.prompt, timeout=10)

                if not skip_exporter_http:
                    # Wait for the storage write operation to complete before proceeding
                    self.logger.info("Waiting until the http image preparation in storage is completed")
                    storage_thread.join()

                    # Check if there were any exceptions in the background thread
                    if not error_queue.empty():
                        raise error_queue.get()

                self._flash_with_progress(console, manifest, path, image_url, target_device)

                total_time = time.time() - start_time
                # total time in minutes:seconds
                minutes, seconds = divmod(total_time, 60)
                self.logger.info(f"Flashing completed in {int(minutes)}:{int(seconds):02d}")
                console.sendline("reboot")
                time.sleep(2)
                self.logger.info("Powering off target")
                self.power.off()


    def _flash_with_progress(self, console, manifest, path, image_url, target_path):
        """Flash image to target device with progress monitoring.

        Args:
            console: Console object for device interaction
            manifest: Flasher manifest containing target definitions
            path: Path to the source image
            image_url: URL to download the image from
            target_path: Target device path to flash to
        """
        # Flash the image
        decompress_cmd = _get_decompression_command(path)
        flash_cmd = (
            f'( wget -q -O - "{image_url}" | '
            f'{decompress_cmd} '
            f'dd of={target_path} bs=64k iflag=fullblock oflag=direct) &'
        )
        console.sendline(flash_cmd)
        console.expect(manifest.spec.login.prompt, timeout=60)

        console.sendline("pidof dd")
        console.expect(manifest.spec.login.prompt, timeout=3)
        dd_pid = console.before.decode(errors="ignore").splitlines()[1].strip()

        # Initialize progress tracking variables
        last_pos = 0
        last_time = time.time()

        while True:
            console.sendline(f"cat /proc/{dd_pid}/fdinfo/1")
            console.expect(manifest.spec.login.prompt, timeout=3)
            if "No such file or directory" in console.before.decode(errors="ignore"):
                break
            data = console.before.decode(errors="ignore")
            match = re.search(r'pos:\s+(\d+)', data)
            if match:
                current_bytes = int(match.group(1))
                current_time = time.time()
                elapsed = current_time - last_time

                if elapsed >= 1.0:  # Update speed every second
                    bytes_diff = current_bytes - last_pos
                    speed_mb = (bytes_diff / (1024*1024)) / elapsed
                    total_mb = current_bytes/(1024*1024)
                    self.logger.info(f"Flash progress: {total_mb:.2f} MB, Speed: {speed_mb:.2f} MB/s")

                    last_pos = current_bytes
                    last_time = current_time
            time.sleep(1)
        self.logger.info("Flushing buffers")
        console.sendline("sync")
        console.expect(manifest.spec.login.prompt, timeout=1200)


    def _get_target_device(self, target: str, manifest: FlasherBundleManifestV1Alpha1, console) -> str:
        """Get the target device path from the manifest, resolving block devices if needed.

        Args:
            target: Target name from manifest
            manifest: Flasher manifest containing target definitions
            console: Console object for device interaction

        Returns:
            Resolved target device path

        Raises:
            ArgumentError: If target is not found in manifest
        """
        target_path = manifest.spec.targets.get(target)
        if target_path is None:
            raise ArgumentError(f"Target {target} not found in manifest")

        if target_path.startswith("/sys/class/block#"):
            target_path = self._lookup_block_device(
                console, manifest.spec.login.prompt, target_path.split("#")[1])

        return target_path


    def _transfer_bg_thread(self, src_path: PathBuf, src_operator: Operator, known_hash: str | None,
                            to_storage: OpendalClient, error_queue):
        """Transfer image to storage in the background

        Args:
            src_path: Path to the source image
            src_operator: Operator to read the source image
            to_storage: Storage operator to write the image to
            error_queue: Queue to put exceptions in if any
            known_hash: Known hash of the image
        """
        self.logger.info(f"Writing image to storage in the background: {src_path}")
        try:
            if known_hash and to_storage.exists(src_path.name):
                self.logger.info(f"Image {src_path} already exists in storage, checking hash")
                if to_storage.get_hash(src_path.name) == known_hash:
                    self.logger.info(f"Image {src_path} already exists in storage with matching hash, skipping")
                    return
            self.logger.info(f"Writing image from storage, with metadata: {src_operator.get_metadata(src_path)}")
            to_storage.write_from_path(src_path.name, src_path, src_operator)
            self.logger.info(f"Image written to storage: {src_path}")
        except Exception as e:
            self.logger.error(f"Error writing image to storage: {e}")
            error_queue.put(e)
            raise

    def _lookup_block_device(self, console, prompt, address: str) -> str:
        """Lookup block device for a given address.
        Sometimes targets don't get assigned block device numbers in a predictable way,
        so we need to lookup the block device by address.
        """
        console.send(f"ls -l /sys/class/block/ | grep {address} | head -n 1" + "\n")
        console.expect(prompt, timeout=5)
        # This produces an output like:
        # ls /sys/class/block/ -la | grep 4fb0000
        # lrwxrwxrwx    1 root     root             0 Jan  1
        # 00:00 mmcblk1 -> ../../devices/platform/bus@100000/4fb0000.mmc/mmc_host/mmc1/mmc1:aaaa/block/mmcblk1
        output = console.before.decode(errors="ignore")
        match = re.search(r'\s(\w+)\s->', output)
        if match:
            return "/dev/" + match.group(1)
        else:
            raise ArgumentError(f"No block device found for address {address}, output was: {output}")

    def dump(
        self,
        path: PathBuf,
        *,
        partition: str | None = None,
        operator: Operator | None = None,
    ):
        """Dump image from DUT"""
        raise NotImplementedError("Dump is not implemented for this driver yet")

    def _filename(self, path: PathBuf) -> str:
        """Extract filename from url or path"""
        if path.startswith(("http://", "https://")):
            return urlparse(path).path.split("/")[-1]
        else:
            return Path(path).name

    def _upload_artifact(self, storage, path: PathBuf, operator: Operator):
        """Upload artifact to storage"""
        filename = self._filename(path)
        if storage.exists(filename):
            # TODO: check hash for existing files
            self.logger.info(f"Artifact {filename} already exists in storage, skipping")
        storage.write_from_path(path, operator=operator)

    @contextmanager
    def _services_up(self): # TODO: may be we don't need this..
        try:
            self.http.start()
            self.tftp.start()
            yield
        finally:
            self.http.stop()
            self.tftp.stop()


    def _generate_uboot_env(self):
        tftp_host = self.tftp.get_host()
        return {
            "serverip": tftp_host,
        }


    @contextmanager
    def _busybox(self):
        """Start a busybox shell.

        This is a helper context manager that boots the device into uboot and returns a console object.
        """
        with self.serial.pexpect() as console:
            if self._console_debug:
                console.logfile_read = sys.stdout.buffer
            uboot = UbootConsole(console=console, power=self.power, logger=self.logger)
            # make sure that the device is booted into the uboot console
            uboot.reboot_to_console()
            # run dhcp discovery and gather details useful for later
            self._dhcp_details = uboot.setup_dhcp()
            self.logger.info(f"discovered dhcp details: {self._dhcp_details}")

            # configure the environment necessary
            env = self._generate_uboot_env()
            uboot.set_env_dict(env)

            manifest = self.manifest
            kernel_filename = Path(manifest.get_kernel_file()).name
            kernel_address = manifest.get_kernel_address()

            uboot.run_command(f"tftpboot {kernel_address} {kernel_filename}", timeout=120)

            if manifest.get_initram_file():
                initram_filename = Path(manifest.get_initram_file()).name
                initram_address = manifest.get_initram_address()
                uboot.run_command(f"tftpboot {initram_address} {initram_filename}", timeout=120)

            if manifest.get_dtb_file():
                dtb_filename = Path(manifest.get_dtb_file()).name
                dtb_address = manifest.get_dtb_address()
                uboot.run_command(f"tftpboot {dtb_address} {dtb_filename}", timeout=120)

            self.logger.info(f"Running boot command: {manifest.spec.bootcmd}")
            console.send(manifest.spec.bootcmd +"\n")

            # if manifest has login details, we need to login
            if manifest.spec.login.username:
                console.expect(manifest.spec.login.login_prompt, timeout=120)
                console.send(manifest.spec.login.username + "\n")

            # if manifest has password, we need to send it
            if manifest.spec.login.password:
                console.expect("ssword:", timeout=30)
                console.send(manifest.spec.login.password + "\n")

            console.expect(manifest.spec.login.prompt, timeout=120)
            yield console

    def use_dtb(self, path: PathBuf, operator: Operator | None = None):
        """Use DTB file"""
        if operator is None:
            path, operator = operator_for_path(path)

        ...

    def use_initram(self, path: PathBuf, operator: Operator | None = None):
        """Use initramfs file"""
        if operator is None:
            path, operator = operator_for_path(path)

        ...

    def use_kernel(self, path: PathBuf, operator: Operator | None = None):
        """Use kernel file"""
        if operator is None:
            path, operator = operator_for_path(path)

        ...

    @property
    def manifest(self):
        """Get flasher bundle manifest"""
        if self._manifest:
            return self._manifest

        yaml_str = self.call("get_flasher_manifest_yaml")
        self._manifest = FlasherBundleManifestV1Alpha1.from_string(yaml_str)
        return self._manifest

    def cli(self):
        @click.group
        def base():
            """Software-defined flasher interface"""
            pass

        @base.command()
        @click.argument("file")
        @click.option("--partition", type=str)
        @click.option('--os-image-checksum',
                        help='SHA256 checksum of OS image (direct value)')
        @click.option('--os-image-checksum-file',
                        help='File containing SHA256 checksum of OS image',
                        type=click.Path(exists=True, dir_okay=False))
        @click.option('--force-exporter-http', is_flag=True, help='Force use of exporter HTTP')
        @click.option('--force-flash-bundle', type=str, help='Force use of a specific flasher OCI bundle')
        @debug_console_option
        def flash(file, partition, os_image_checksum, os_image_checksum_file,
                  console_debug, force_exporter_http, force_flash_bundle):
            """Flash image to DUT from file"""
            if os_image_checksum_file and os.path.exists(os_image_checksum_file):
                with open(os_image_checksum_file) as f:
                    os_image_checksum = f.read().strip().split()[0]
                    self.logger.info(f"Read checksum from file: {os_image_checksum}")

            self.set_console_debug(console_debug)
            self.flash(file,
                       partition=partition,
                       force_exporter_http=force_exporter_http,
                       force_flash_bundle=force_flash_bundle)

        @base.command()
        @debug_console_option
        def bootloader_shell(console_debug):
            """Start a uboot/bootloader interactive console"""
            self.set_console_debug(console_debug)
            with self.bootloader_shell() as serial:
                c = Console(serial)
                c.run()

        @base.command()
        @debug_console_option
        def busybox_shell(console_debug):
            """Start a busybox shell"""
            self.set_console_debug(console_debug)
            with self.busybox_shell() as serial:
                c = Console(serial)
                c.run()

        return base


def _get_decompression_command(filename_or_url) -> str:
    """
    Determine the appropriate decompression command based on file extension

    Args:
        filename (str): Name of the file to check

    Returns:
        str: Decompression command ('zcat', 'xzcat', or 'cat' for uncompressed)
    """
    if type(filename_or_url) is PosixPath:
        filename = filename_or_url.name
    elif filename_or_url.startswith(("http://", "https://")):
        filename = urlparse(filename_or_url).path.split("/")[-1]

    filename = filename.lower()
    if filename.endswith(('.gz', '.gzip')):
        return 'zcat |'
    elif filename.endswith('.xz'):
        return 'xzcat |'
    return ''
