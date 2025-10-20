import hashlib
import json
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

import click
import requests
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_opendal.client import FlasherClient, OpendalClient, operator_for_path
from jumpstarter_driver_opendal.common import PathBuf
from jumpstarter_driver_pyserial.client import Console
from opendal import Metadata, Operator

from jumpstarter_driver_flashers.bundle import FlasherBundleManifestV1Alpha1

from jumpstarter.client.decorators import driver_click_group
from jumpstarter.common.exceptions import ArgumentError, JumpstarterException
from jumpstarter_cli_common.exceptions import leaf_exceptions
from typing import TypeVar, Type

E = TypeVar('E', bound=Exception)


class FlashError(JumpstarterException):
    """Base exception for flash-related errors."""


class FlashRetryableError(FlashError):
    """Exception for retryable flash errors (network, timeout, etc.)."""


class FlashNonRetryableError(FlashError):
    """Exception for non-retryable flash errors (configuration, file system, etc.)."""

debug_console_option = click.option("--console-debug", is_flag=True, help="Enable console debug mode")

EXPECT_TIMEOUT_DEFAULT = 60
EXPECT_TIMEOUT_SYNC = 1200


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
        # TODO: also set console debug on uboot client

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
            with self.uboot.reboot_to_console(debug=self._console_debug):
                pass
            yield self.serial

    def flash(  # noqa: C901
        self,
        path: PathBuf,
        *,
        partition: str | None = None,
        operator: Operator | None = None,
        os_image_checksum: str | None = None,
        force_exporter_http: bool = False,
        force_flash_bundle: str | None = None,
        cacert_file: str | None = None,
        insecure_tls: bool = False,
        headers: dict[str, str] | None = None,
        bearer_token: str | None = None,
        retries: int = 3,
    ):
        if bearer_token:
            bearer_token = self._validate_bearer_token(bearer_token)

        if headers:
            headers = self._validate_header_dict(headers)

        """Flash image to DUT"""
        should_download_to_httpd = True
        image_url = ""
        original_http_url = None
        operator_scheme = None
        # initrmafs cannot handle https yet, fallback to using the exporter's http server
        if path.startswith(("http://", "https://")) and not force_exporter_http:
            # the flasher image can handle the http(s) from a remote directly, unless target is isolated
            image_url = path
            should_download_to_httpd = False
        else:
            # use the exporter's http server for the flasher image, we should download it first
            if operator is None:
                if path.startswith(("http://", "https://")) and bearer_token:
                    parsed = urlparse(path)
                    self.logger.info(f"Using Bearer token authentication for {parsed.netloc}")
                    original_http_url = path
                    operator = Operator(
                        "http", root="/", endpoint=f"{parsed.scheme}://{parsed.netloc}", token=bearer_token
                    )
                    operator_scheme = "http"
                    path = Path(parsed.path)
                else:
                    path, operator, operator_scheme = operator_for_path(path)
            image_url = self.http.get_url() + "/" + path.name

        # start counting time for the flash operation
        start_time = time.time()

        # Initialize storage_thread and error_queue
        storage_thread = None
        error_queue = Queue()

        if should_download_to_httpd:
            # Start the storage write operation in the background
            storage_thread = threading.Thread(
                target=self._transfer_bg_thread,
                args=(
                    path,
                    operator,
                    operator_scheme,
                    os_image_checksum,
                    self.http.storage,
                    error_queue,
                    original_http_url,
                    headers,
                ),
                name="storage_transfer",
            )
            storage_thread.start()

        # Make the exporter download the bundle contents and set files in the right places
        self.logger.info("Setting up flasher bundle files in exporter")
        self.call("setup_flasher_bundle", force_flash_bundle)

        # Early exit if there was an error in the background thread
        if should_download_to_httpd and not error_queue.empty():
            raise error_queue.get()

        with self._services_up():
            # Retry logic at the highest level - retry entire console setup and flash operation
            for attempt in range(retries + 1):  # +1 for initial attempt
                try:
                    self._perform_flash_operation(
                        partition, path, image_url, should_download_to_httpd,
                        storage_thread, error_queue, cacert_file, insecure_tls,
                        headers, bearer_token
                    )
                    self.logger.info(f"Flash operation succeeded on attempt {attempt + 1}")
                    break
                except Exception as e:
                    # Check if this is a retryable or non-retryable error (including sub-exceptions)
                    retryable_error = self._get_retryable_error(e)
                    non_retryable_error = self._get_non_retryable_error(e)

                    if retryable_error is not None:
                        if attempt < retries:
                            self.logger.warning(
                                f"Flash attempt {attempt + 1} failed with retryable error: {retryable_error}"
                            )
                            self.logger.info(f"Retrying flash operation (attempt {attempt + 2}/{retries + 1})")
                            # Wait a bit before retrying
                            time.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        else:
                            self.logger.error(f"Flash operation failed after {retries + 1} attempts")
                            raise FlashError(
                                f"Flash operation failed after {retries + 1} attempts. Last error: {retryable_error}"
                            ) from e
                    elif non_retryable_error is not None:
                        # Non-retryable error, fail immediately
                        self.logger.error(f"Flash operation failed with non-retryable error: {non_retryable_error}")
                        raise FlashError(f"Flash operation failed: {non_retryable_error}") from e
                    else:
                        # Unexpected error, don't retry
                        # If it's an ExceptionGroup, show leaf exceptions for better diagnostics
                        if isinstance(e, BaseExceptionGroup):
                            leaves = leaf_exceptions(e, fix_tracebacks=False)
                            error_details = "; ".join([f"{type(exc).__name__}: {exc}" for exc in leaves])
                            self.logger.error(f"Flash operation failed with unexpected error(s): {error_details}")
                            raise FlashError(f"Flash operation failed: {error_details}") from e
                        else:
                            self.logger.error(f"Flash operation failed with unexpected error: {e}")
                            raise FlashError(f"Flash operation failed: {e}") from e


        total_time = time.time() - start_time
        # total time in minutes:seconds
        minutes, seconds = divmod(total_time, 60)
        self.logger.info(f"Flashing completed in {int(minutes)}m {int(seconds):02d}s")

    def _find_exception_in_chain(self, exception: Exception, exc_type: Type[E]) -> E | None:
        """Find a specific exception type in an exception chain, including ExceptionGroups.

        Args:
            exception: The exception to search
            exc_type: The exception type to find

        Returns:
            The matching exception if found, None otherwise
        """
        # Check if this is a BaseExceptionGroup and look through leaf exceptions
        if isinstance(exception, BaseExceptionGroup):
            for sub_exc in leaf_exceptions(exception, fix_tracebacks=False):
                result = self._find_exception_in_chain(sub_exc, exc_type)
                if result is not None:
                    return result

        # Check the current exception
        if isinstance(exception, exc_type):
            return exception

        # Check the cause chain
        current = getattr(exception, '__cause__', None)
        while current is not None:
            if isinstance(current, exc_type):
                return current
            # Also check if the cause is a BaseExceptionGroup
            if isinstance(current, BaseExceptionGroup):
                for sub_exc in leaf_exceptions(current, fix_tracebacks=False):
                    result = self._find_exception_in_chain(sub_exc, exc_type)
                    if result is not None:
                        return result
            current = getattr(current, '__cause__', None)
        return None

    def _get_retryable_error(self, exception: Exception) -> FlashRetryableError | None:
        """Find a retryable error in an exception (or any of its causes)."""
        return self._find_exception_in_chain(exception, FlashRetryableError)

    def _get_non_retryable_error(self, exception: Exception) -> FlashNonRetryableError | None:
        """Find a non-retryable error in an exception (or any of its causes)."""
        return self._find_exception_in_chain(exception, FlashNonRetryableError)

    def _perform_flash_operation(
        self,
        partition: str | None,
        path: PathBuf,
        image_url: str,
        should_download_to_httpd: bool,
        storage_thread: threading.Thread | None,
        error_queue: Queue,
        cacert_file: str | None,
        insecure_tls: bool,
        headers: dict[str, str] | None,
        bearer_token: str | None,
    ):
        """Perform the actual flash operation with console setup.

        This method contains all the console operations that can be retried.
        """
        with self._busybox() as console:
            manifest = self.manifest
            target = partition or self.call("get_default_target") or manifest.spec.default_target
            if not target:
                raise ArgumentError("No partition or default target specified")

            target_device = self._get_target_device(target, manifest, console)

            self.logger.info(f"Using target block device: {target_device}")
            console.sendline(f"export dhcp_addr={self._dhcp_details.ip_address}")
            console.expect(manifest.spec.login.prompt, timeout=EXPECT_TIMEOUT_DEFAULT)
            console.sendline(f"export gw_addr={self._dhcp_details.gateway}")
            console.expect(manifest.spec.login.prompt, timeout=EXPECT_TIMEOUT_DEFAULT)

            # Preflash commands are executed before the flash operation
            # generally used to clean up boot entries in existing devices
            for preflash_command in manifest.spec.preflash_commands:
                self.logger.info(f"Running preflash command: {preflash_command}")
                console.sendline(preflash_command)
                console.expect(manifest.spec.login.prompt, timeout=EXPECT_TIMEOUT_DEFAULT)

            # make sure that the device is connected to the network and has an IP address
            console.sendline("udhcpc")
            console.expect(manifest.spec.login.prompt, timeout=EXPECT_TIMEOUT_DEFAULT)

            stored_cacert = None
            if should_download_to_httpd:
                self._wait_for_storage_thread(storage_thread, error_queue)
            else:
                stored_cacert = self._setup_flasher_ssl(console, manifest, cacert_file)

            header_args = self._prepare_headers(headers, bearer_token)

            # Perform the actual flash operation
            self._flash_with_progress(
                console,
                manifest,
                path,
                image_url,
                target_device,
                insecure_tls,
                stored_cacert,
                header_args,
            )

            console.sendline("reboot")
            time.sleep(2)
            self.logger.info("Powering off target")
            self.power.off()

    def _setup_flasher_ssl(self, console, manifest, cacert_file: str | None) -> str | None:
        """Setup SSL configuration for the flasher.

        Args:
            console: Console object for device interaction
            manifest: Flasher manifest containing login prompt
            cacert_file: Path to CA certificate file

        Returns:
            Path to stored CA certificate in the DUT flasher, or None if no certificate was provided

        Raises:
            RuntimeError: If there's an error reading the CA certificate file
        """
        # make sure that the remote system has the right time without using NTP
        # otherwise SSL certificate verification will fail
        self.logger.info("Setting the remote DUT time to match the local system time")
        current_timestamp = int(time.time())
        console.sendline(f"date -s @{current_timestamp}")
        console.expect(manifest.spec.login.prompt, timeout=EXPECT_TIMEOUT_DEFAULT)

        if cacert_file:
            cacert = b""
            try:
                with open(cacert_file, "rb") as f:
                    cacert = f.read()
            except OSError as e:
                self.logger.error(f"Error reading CA certificate file: {e}")
                raise RuntimeError(f"Error reading CA certificate file: {e}") from e
            self.logger.info("Storing the CA certificate in the remote DUT flasher")
            # write the contents of cacert to /tmp/cacert.crt on the remote target through console
            stored_cacert = "/tmp/cacert.crt"
            console.sendline(f"cat > {stored_cacert} << EOF")
            console.sendline(cacert)
            console.sendline("\nEOF")
            console.expect(manifest.spec.login.prompt, timeout=EXPECT_TIMEOUT_DEFAULT)
            return stored_cacert

        return None

    def _curl_tls_args(self, insecure_tls: bool, stored_cacert: str | None) -> str:
        """Generate TLS arguments for curl command.

        Args:
            insecure_tls: Whether to use insecure TLS
            stored_cacert: Path to the stored CA certificate in the DUT flasher

        Returns:
            String containing TLS arguments for curl command
        """
        tls_args = ""
        if insecure_tls:
            tls_args += "-k "
        if stored_cacert:
            tls_args += f"--cacert {stored_cacert} "
        return tls_args.strip()

    def _curl_header_args(self, headers: dict[str, str] | None) -> str:
        """Generate header arguments for curl command"""
        if not headers:
            return ""

        parts: list[str] = []

        def _sq(s: str) -> str:
            return s.replace("'", "'\"'\"'")

        for k, v in headers.items():
            k = str(k).strip()
            v = str(v).strip()
            if not k:
                continue
            parts.append(f"-H '{_sq(k)}: {_sq(v)}'")

        return " ".join(parts)

    def _flash_with_progress(
        self,
        console,
        manifest,
        path,
        image_url,
        target_path,
        insecure_tls,
        stored_cacert,
        header_args: str,
    ):
        """Flash image to target device with progress monitoring.

        Args:
            console: Console object for device interaction
            manifest: Flasher manifest containing target definitions
            path: Path to the source image
            image_url: URL to download the image from
            target_path: Target device path to flash to
            insecure_tls: Whether to use insecure TLS
            stored_cacert: Path to the stored CA certificate in the DUT flasher
        """

        # Calculate decompress and tls arguments for curl
        prompt = manifest.spec.login.prompt
        decompress_cmd = _get_decompression_command(path)
        tls_args = self._curl_tls_args(insecure_tls, stored_cacert)

        # Check if the image URL is accessible using curl and the TLS arguments
        self._check_url_access(console, prompt, image_url, tls_args, header_args)

        # Flash the image, we run curl -> decompress -> dd in the background, so we can monitor dd's progress
        # Use pipefail to ensure the pipeline fails if any command in the pipe fails
        flash_cmd = (
            f'( set -o pipefail; curl -fsSL {tls_args} {header_args} "{image_url}" | '
            f"{decompress_cmd} "
            f'dd of={target_path} bs=64k iflag=fullblock oflag=direct ' +
            '&& echo "F""LASH_COMPLETE" || echo "F""LASH_FAILED" ) &'
        )
        console.sendline(flash_cmd)
        console.expect(prompt, timeout=EXPECT_TIMEOUT_DEFAULT * 2)

        # Start monitoring the flash operation
        self._monitor_flash_progress(console, prompt)

        self.logger.info("Flushing buffers")
        console.sendline("sync")
        console.expect(prompt, timeout=EXPECT_TIMEOUT_SYNC)

    def _monitor_flash_progress(self, console, prompt):
        """Monitor flash progress by accumulating console output and looking for completion markers."""
        # Get dd process ID for progress monitoring
        console.sendline("pidof dd")
        console.expect(prompt, timeout=EXPECT_TIMEOUT_DEFAULT)
        pidof_output = console.before.decode(errors="ignore")
        accumulated_output = pidof_output # just in case we get the FLASH_COMPLETE or FLASH_FAILED markers soon

        # Extract the actual process ID from the output, handling potential error messages
        lines = pidof_output.splitlines()
        dd_pid = None
        for line in lines:
            line = line.strip()
            # Look for a line that contains only digits (the process ID)
            if line.isdigit():
                dd_pid = line
                break

        if not dd_pid:
            self.logger.error("Could not find dd process ID")
            raise FlashNonRetryableError("Could not find dd process ID")

        # Initialize progress tracking variables
        last_pos = 0
        last_time = time.time()
        dd_finished_time = None

        while True:
            # Check if dd process is still running for progress monitoring (only if we have a valid PID)
            if dd_pid != "0":
                console.sendline(f"cat /proc/{dd_pid}/fdinfo/1")
                console.expect(prompt, timeout=EXPECT_TIMEOUT_DEFAULT)
                data = console.before.decode(errors="ignore")
            else:
                # If we don't have a valid dd PID, just check for completion markers
                data = ""

            # Always accumulate output regardless of dd status
            accumulated_output = self._update_accumulated_output(accumulated_output, data)

            # Debug logging to help track output
            if "FLASH_" in data:
                self.logger.debug(f"Found FLASH marker in data: {data}")
                self.logger.debug(f"Current accumulated output: {accumulated_output}")

            # Check for completion markers in the accumulated output
            if self._check_completion_markers(accumulated_output):
                break

            if dd_pid != "0" and "No such file or directory" in data:
                # dd process finished, handle the completion
                dd_finished_time = self._handle_dd_finished(dd_finished_time)
                continue
            elif dd_pid != "0":
                # dd is still running, monitor progress
                last_pos, last_time = self._update_progress_stats(data, last_pos, last_time)
            else:
                # No valid dd PID, just wait and check for completion markers
                pass

            time.sleep(1)

    def _check_completion_markers(self, accumulated_output):
        """Check for completion markers in accumulated output."""
        if "FLASH_COMPLETE" in accumulated_output:
            self.logger.info("Flash operation completed successfully")
            return True
        elif "FLASH_FAILED" in accumulated_output:
            self.logger.error(f"FLASH_FAILED marker detected in output:\n{accumulated_output}")
            raise FlashRetryableError("Flash operation failed - curl or pipeline failed")
        return False

    def _handle_dd_finished(self, dd_finished_time):
        """Handle case when dd process has finished but no completion marker found yet."""
        if dd_finished_time is None:
            dd_finished_time = time.time()
        elif time.time() - dd_finished_time > 5:  # Wait up to 5 seconds for echo
            raise FlashNonRetryableError("Flash operation completed without success/failure marker")
        # Continue checking for a few more iterations
        time.sleep(1)
        return dd_finished_time

    def _update_accumulated_output(self, accumulated_output, data):
        """Update accumulated output with new data, keeping only last 64KB."""
        accumulated_output += data
        # Keep only the last 64KB to prevent memory growth
        if len(accumulated_output) > 64*1024:
            accumulated_output = accumulated_output[-64*1024:]
        return accumulated_output

    def _update_progress_stats(self, data, last_pos, last_time):
        """Update progress statistics and log progress if enough time has elapsed."""
        match = re.search(r"pos:\s+(\d+)", data)

        if match:
            current_bytes = int(match.group(1))
            current_time = time.time()
            elapsed = current_time - last_time

            if elapsed >= 5.0:  # Update speed every 5 seconds
                bytes_diff = current_bytes - last_pos
                speed_mb = (bytes_diff / (1024 * 1024)) / elapsed
                total_mb = current_bytes / (1024 * 1024)
                self.logger.info(f"Flash progress: {total_mb:.2f} MB, Speed: {speed_mb:.2f} MB/s")

                last_pos = current_bytes
                last_time = current_time

        return last_pos, last_time

    def _check_url_access(self, console, prompt, image_url: str, tls_args: str, header_args: str):
        """Check if the image URL is accessible using curl.

        Args:
            console: Console object for device interaction
            prompt: Login prompt for console
            image_url: URL to check accessibility for
            tls_args: TLS arguments for curl command

        Raises:
            RuntimeError: If the URL is not accessible
        """
        console.sendline(
            f'curl --location --max-time 30 --fail -sS -r 0-0 -o /dev/null {tls_args} {header_args} "{image_url}"'
        )
        console.expect(prompt, timeout=EXPECT_TIMEOUT_DEFAULT)
        curl_output = console.before.decode(errors="ignore").strip()
        console.sendline("echo $?")
        console.expect(prompt, timeout=EXPECT_TIMEOUT_DEFAULT)
        url_status = int(console.before.decode(errors="ignore").strip().splitlines()[-1])
        if url_status != 0:
            raise FlashRetryableError(
                f"Unable to access {image_url} (curl exit status {url_status}), output: {curl_output}"
            )

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
            target_path = self._lookup_block_device(console, manifest.spec.login.prompt, target_path.split("#")[1])

        return target_path

    def _wait_for_storage_thread(self, storage_thread, error_queue):
        """Wait for the storage write operation to complete and check for exceptions.

        Args:
            storage_thread: The background thread handling storage operations
            error_queue: Queue containing any exceptions from the background thread

        Raises:
            Exception: Any exception that occurred in the background thread
        """
        # Wait for the storage write operation to complete before proceeding
        self.logger.info("Waiting until the http image preparation in storage is completed")
        storage_thread.join()

        # Check if there were any exceptions in the background thread
        if not error_queue.empty():
            raise error_queue.get()

    def _transfer_bg_thread(
        self,
        src_path: PathBuf,
        src_operator: Operator,
        src_operator_scheme: str,
        known_hash: str | None,
        to_storage: OpendalClient,
        error_queue,
        original_url: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        """Transfer image to exporter storage in the background
        Args:
            src_path: Path to the source image
            src_operator: Operator to read the source image
            to_storage: Storage operator to write the image to
            error_queue: Queue to put exceptions in if any
            known_hash: Known hash of the image
            original_url: Original URL for HTTP fallback
            headers: HTTP headers for requests
        """
        self.logger.info(f"Writing image to storage in the background: {src_path}")
        try:
            filename = Path(src_path).name if isinstance(src_path, (str, os.PathLike)) else src_path.name

            if src_operator_scheme == "fs":
                file_hash = self._sha256_file(src_operator, src_path)
                self.logger.info(f"Hash of {filename} is {file_hash}")
            else:
                file_hash = known_hash
                self.logger.info(f"Using provided hash for {filename}: {known_hash}")

            if file_hash and to_storage.exists(filename):
                to_storage_hash = to_storage.hash(filename)
                self.logger.info(f"Hash of existing file in storage: {to_storage_hash}")

                if to_storage_hash == file_hash:
                    self.logger.info(f"Image {filename} already exists in storage with matching hash, skipping")
                    return
                else:
                    self.logger.info(f"Image {filename} exists in storage but hash differs, will overwrite")

            self.logger.info(f"Uploading image to storage: {filename}")
            to_storage.write_from_path(filename, src_path, src_operator)

            metadata, metadata_json = self._create_metadata_and_json(
                src_operator, src_path, file_hash, original_url, headers
            )
            metadata_file = filename + ".metadata"
            to_storage.write_bytes(metadata_file, metadata_json.encode(errors="ignore"))

            self.logger.info(f"Image written to storage: {filename}")

        except Exception as e:
            self.logger.error(f"Error writing image to storage: {e}")
            error_queue.put(e)
            raise

    def _sha256_file(self, src_operator, src_path) -> str:
        m = hashlib.sha256()
        with src_operator.open(src_path, "rb") as f:
            while True:
                data = f.read(size=65536)
                if len(data) == 0:
                    break
                m.update(data)

        return m.hexdigest()

    def _create_metadata_and_json(
        self, src_operator, src_path, file_hash=None, original_url=None, headers: dict[str, str] | None = None
    ) -> tuple[Metadata | None, str]:
        """Create a metadata json string from a metadata object"""
        metadata = None
        metadata_dict = {"path": str(src_path)}

        try:
            metadata = src_operator.stat(src_path)
            metadata_dict.update(
                {
                    "content_length": metadata.content_length,
                    "etag": metadata.etag,
                }
            )
        except Exception as e:
            # TODO(bennyz): remove when opendal issue is sorted out
            # https://github.com/apache/opendal/discussions/6418
            # fallback to request if we're using a custom certificate

            if original_url and original_url.startswith(("http://", "https://")):
                try:
                    if headers:
                        response = requests.head(original_url, headers=headers)
                    else:
                        response = requests.head(original_url)

                    http_metadata = {}
                    if "content-length" in response.headers:
                        http_metadata["content_length"] = int(response.headers["content-length"])
                    if "etag" in response.headers:
                        http_metadata["etag"] = response.headers["etag"]

                    metadata_dict.update(http_metadata)
                    self.logger.info("Successfully got HTTP metadata using requests fallback")
                except Exception as http_e:
                    self.logger.error(f"Error getting HTTP metadata with requests fallback: {http_e}")
            else:
                self.logger.error(f"Error getting metadata: {e}")

        if file_hash:
            metadata_dict["hash"] = file_hash

        return metadata, json.dumps(metadata_dict)

    def _lookup_block_device(self, console, prompt, address: str) -> str:
        """Lookup block device for a given address.
        Sometimes targets don't get assigned block device numbers in a predictable way,
        so we need to lookup the block device by address.
        """
        console.send(f"ls -l /sys/class/block/ | grep {address} | head -n 1" + "\n")
        console.expect(prompt, timeout=EXPECT_TIMEOUT_DEFAULT)
        # This produces an output like:
        # ls /sys/class/block/ -la | grep 4fb0000
        # lrwxrwxrwx    1 root     root             0 Jan  1
        # 00:00 mmcblk1 -> ../../devices/platform/bus@100000/4fb0000.mmc/mmc_host/mmc1/mmc1:aaaa/block/mmcblk1
        output = console.before.decode(errors="ignore")
        match = re.search(r"\s(\w+)\s->", output)
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
        storage.write_from_path(filename, path, operator=operator)

    @contextmanager
    def _services_up(self):
        """Make sure that the http and tftp services are up an running in this context"""
        try:
            self.http.start()
            self.tftp.start()
            yield
        finally:
            self.http.stop()
            self.tftp.stop()

    def _generate_uboot_env(self):
        """Generate a uboot environment dictionary, may need specific overrides for different targets"""
        tftp_host = self.tftp.get_host()
        return {
            "serverip": tftp_host,
        }

    @contextmanager
    def _busybox(self):
        """Start a busybox shell.

        This is a helper context manager that boots the device into uboot and returns a console object.
        """

        # make sure that the device is booted into the uboot console
        with self.uboot.reboot_to_console(debug=self._console_debug):
            # run dhcp discovery and gather details useful for later
            self._dhcp_details = self.uboot.setup_dhcp()
            self.logger.info(f"discovered dhcp details: {self._dhcp_details}")

            # configure the environment necessary
            env = self._generate_uboot_env()
            self.uboot.set_env_dict(env)

            # load any necessary files to RAM from the tftp storage
            manifest = self.manifest
            kernel_filename = Path(manifest.get_kernel_file()).name
            kernel_address = manifest.get_kernel_address()

            self.uboot.run_command(f"tftpboot {kernel_address} {kernel_filename}", timeout=120)

            if manifest.get_initram_file():
                initram_filename = Path(manifest.get_initram_file()).name
                initram_address = manifest.get_initram_address()
                if initram_address:
                    self.uboot.run_command(f"tftpboot {initram_address} {initram_filename}", timeout=120)

            try:
                dtb_file = manifest.get_dtb_file()
                if dtb_file:
                    dtb_filename = Path(dtb_file).name
                    dtb_address = manifest.get_dtb_address()
                    if dtb_address:
                        self.uboot.run_command(f"tftpboot {dtb_address} {dtb_filename}", timeout=120)
            except ValueError:
                # DTB variant not found, skip DTB loading
                pass

        with self.serial.pexpect() as console:
            if self._console_debug:
                console.logfile_read = sys.stdout.buffer

            bootcmd = self.call("get_bootcmd")

            self.logger.info(f"Running boot command: {bootcmd}")
            console.send(bootcmd + "\n")

            # if manifest has login details, we need to login
            if manifest.spec.login.username:
                console.expect(manifest.spec.login.login_prompt, timeout=EXPECT_TIMEOUT_DEFAULT * 3)
                console.send(manifest.spec.login.username + "\n")

            # if manifest has password, we need to send it
            if manifest.spec.login.password:
                console.expect("ssword:", timeout=EXPECT_TIMEOUT_DEFAULT)
                console.send(manifest.spec.login.password + "\n")

            console.expect(manifest.spec.login.prompt, timeout=EXPECT_TIMEOUT_DEFAULT * 3)
            yield console

    def use_dtb(self, path: PathBuf, operator: Operator | None = None):
        """Use DTB file"""
        if operator is None:
            path, operator, operator_scheme = operator_for_path(path)

        ...

    def use_initram(self, path: PathBuf, operator: Operator | None = None):
        """Use initramfs file"""
        if operator is None:
            path, operator, operator_scheme = operator_for_path(path)

        ...

    def use_kernel(self, path: PathBuf, operator: Operator | None = None):
        """Use kernel file"""
        if operator is None:
            path, operator, operator_scheme = operator_for_path(path)

        ...

    @property
    def manifest(self):
        """Get flasher bundle manifest"""
        if self._manifest:
            return self._manifest

        yaml_str = self.call("get_flasher_manifest_yaml")
        self._manifest = FlasherBundleManifestV1Alpha1.from_string(yaml_str)
        return self._manifest

    def _validate_header_dict(self, header_map: dict[str, str]) -> dict[str, str]:
        token_re = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
        seen: set[str] = set()
        for key, value in header_map.items():
            key = key.strip()
            value = value.strip()
            if not key:
                raise ArgumentError(f"Invalid header key: '{key}'")

            if not token_re.match(key):
                raise ArgumentError(f"Invalid header name '{key}': must be an HTTP token (RFC7230)")
            if any(c in ("\r", "\n") for c in key) or any(c in ("\r", "\n") for c in value):
                raise ArgumentError("Header names/values must not contain CR/LF")
            kl = key.lower()
            if kl in seen:
                raise ArgumentError(f"Duplicate header '{key}'")
            seen.add(kl)
        return header_map

    def _parse_headers(self, headers: list[str]) -> dict[str, str]:
        header_map: dict[str, str] = {}
        for h in headers:
            if ":" not in h:
                raise click.ClickException(f"Invalid header format: {h!r}. Expected 'Key: Value'.")

            key, value = h.split(":", 1)
            header_map[key.strip()] = value.strip()

        try:
            return self._validate_header_dict(header_map)
        except ArgumentError as e:
            raise click.ClickException(str(e)) from e

    def _prepare_headers(self, headers: dict[str, str] | None, bearer_token: str | None) -> str:
        all_headers = headers.copy() if headers else {}
        if bearer_token:
            if any(k.lower() == "authorization" for k in all_headers.keys()):
                self.logger.warning("Authorization header provided - ignoring bearer token")
            else:
                all_headers["Authorization"] = f"Bearer {bearer_token}"

        if bearer_token and "Authorization" not in (headers or {}):
            auth_header = {"Authorization": all_headers["Authorization"]}
            self._validate_header_dict(auth_header)

        return self._curl_header_args(all_headers)

    def _validate_bearer_token(self, token: str | None) -> str | None:
        if token is None:
            return None

        token = token.strip()
        if not token:
            raise click.ClickException("Bearer token cannot be empty")

        # RFC 6750 allows token68 format (base64url-encoded) or other token formats
        # Basic validation: printable ASCII excluding whitespace and special chars that could cause issues
        if not all(32 < ord(c) < 127 and c not in ' "\\' for c in token):
            raise click.ClickException("Bearer token contains invalid characters")

        if len(token) > 4096:
            raise click.ClickException("Bearer token is too long (max 4096 characters)")

        return token

    def cli(self):
        @driver_click_group(self)
        def base():
            """Software-defined flasher interface"""
            pass

        @base.command()
        @click.argument("file")
        @click.option("--target", type=str)
        @click.option("--os-image-checksum", help="SHA256 checksum of OS image (direct value)")
        @click.option(
            "--os-image-checksum-file",
            help="File containing SHA256 checksum of OS image",
            type=click.Path(exists=True, dir_okay=False),
        )
        @click.option("--force-exporter-http", is_flag=True, help="Force use of exporter HTTP")
        @click.option("--force-flash-bundle", type=str, help="Force use of a specific flasher OCI bundle")
        @click.option("--cacert", type=click.Path(exists=True, dir_okay=False), help="CA certificate to use for HTTPS")
        @click.option("--insecure-tls", is_flag=True, help="Skip TLS certificate verification")
        @click.option(
            "--header",
            "header",
            multiple=True,
            help="Custom HTTP header in 'Key: Value' format",
        )
        @click.option(
            "--bearer",
            type=str,
            help="Bearer token for HTTP authentication",
        )
        @click.option(
            "--retries",
            type=int,
            default=3,
            help="Number of retry attempts for flash operation (default: 3)",
        )
        @debug_console_option
        def flash(
            file,
            target,
            os_image_checksum,
            os_image_checksum_file,
            console_debug,
            force_exporter_http,
            force_flash_bundle,
            cacert,
            insecure_tls,
            header,
            bearer,
            retries,
        ):
            """Flash image to DUT from file"""
            if os_image_checksum_file and os.path.exists(os_image_checksum_file):
                with open(os_image_checksum_file) as f:
                    os_image_checksum = f.read().strip().split()[0]
                    self.logger.info(f"Read checksum from file: {os_image_checksum}")

            self.set_console_debug(console_debug)

            headers = self._parse_headers(header) if header else None

            self.flash(
                file,
                partition=target,
                force_exporter_http=force_exporter_http,
                force_flash_bundle=force_flash_bundle,
                cacert_file=cacert,
                insecure_tls=insecure_tls,
                headers=headers,
                bearer_token=bearer,
                retries=retries,
            )

        @base.command()
        @debug_console_option
        def bootloader_shell(console_debug):
            """Start a uboot/bootloader interactive console"""
            self.set_console_debug(console_debug)
            with self.bootloader_shell() as serial:
                print("=> ", end="", flush=True)
                c = Console(serial)
                c.run()

        @base.command()
        @debug_console_option
        def busybox_shell(console_debug):
            """Start a busybox shell"""
            self.set_console_debug(console_debug)
            with self.busybox_shell() as serial:
                print("# ", end="", flush=True)
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
    if filename.endswith((".gz", ".gzip")):
        return "zcat |"
    elif filename.endswith(".xz"):
        return "xzcat |"
    return ""
