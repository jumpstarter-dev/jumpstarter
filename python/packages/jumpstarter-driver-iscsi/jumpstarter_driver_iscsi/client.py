import contextlib
import hashlib
import os
from dataclasses import dataclass
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import click
import requests
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_opendal.common import PathBuf
from opendal import Operator


@dataclass(kw_only=True)
class ISCSIServerClient(CompositeClient):
    """
    Client interface for iSCSI Server driver

    This client provides methods to control an iSCSI target server and manage LUNs.
    Supports exposing files and block devices through the iSCSI protocol.
    """

    def start(self):
        """
        Start the iSCSI target server

        Initializes and starts the iSCSI target server if it's not already running.
        The server will listen on the configured host and port.
        """
        self.call("start")

    def stop(self):
        """
        Stop the iSCSI target server

        Stops the running iSCSI target server and releases associated resources.

        Raises:
            ISCSIError: If the server fails to stop
        """
        self.call("stop")

    def get_host(self) -> str:
        """
        Get the host address the iSCSI server is listening on

        Returns:
            str: The IP address or hostname the server is bound to
        """
        return self.call("get_host")

    def get_port(self) -> int:
        """
        Get the port number the iSCSI server is listening on

        Returns:
            int: The port number (default is 3260)
        """
        return self.call("get_port")

    def get_target_iqn(self) -> str:
        """
        Get the IQN of the target

        Returns:
            str: The IQN string for connecting to this target
        """
        return self.call("get_target_iqn")

    def _normalized_name_from_file(self, path: str) -> str:
        base = os.path.basename(path)
        for ext in (".gz", ".xz", ".bz2"):
            if base.endswith(ext):
                base = base[: -len(ext)]
                break
        if base.endswith(".img"):
            base = base[: -len(".img")]
        return base or "image"

    def _get_src_and_operator(
        self, file: str, headers: tuple[str, ...]
    ) -> tuple[str, Optional[Operator], Optional[str]]:
        from jumpstarter_driver_opendal.client import operator_for_path

        if file.startswith(("http://", "https://")):
            if headers:
                header_map: Dict[str, str] = {}
                for h in headers:
                    if ":" not in h:
                        raise click.ClickException(f"Invalid header format: {h!r}. Expected 'Key: Value'.")
                    key, value = h.split(":", 1)
                    key = key.strip()
                    value = value.strip()
                    if not key:
                        raise click.ClickException(f"Invalid header key in: {h!r}")
                    header_map[key] = value

                parsed = urlparse(file)
                with NamedTemporaryFile(
                    prefix="jumpstarter-iscsi-",
                    suffix=os.path.basename(parsed.path),
                    delete=False,
                ) as tf:
                    temp_path = tf.name
                    with requests.get(file, stream=True, headers=header_map, timeout=60) as resp:
                        resp.raise_for_status()
                        for chunk in resp.iter_content(chunk_size=65536):
                            if chunk:
                                tf.write(chunk)
                return temp_path, None, temp_path

            _, src_operator, _ = operator_for_path(file)
            return file, src_operator, None

        file = os.path.abspath(file)
        _, src_operator, _ = operator_for_path(file)
        return file, src_operator, None

    def add_lun(self, name: str, file_path: str, size_mb: int = 0, is_block: bool = False) -> str:
        """
        Add a new LUN to the iSCSI target

        Args:
            name (str): Unique name for the LUN
            file_path (str): Path to the file or block device
            size_mb (int): Size in MB for new file (if file doesn't exist), 0 means use existing file
            is_block (bool): If True, the path is treated as a block device

        Returns:
            str: Name of the created LUN

        Raises:
            ISCSIError: If the LUN cannot be created
        """
        return self.call("add_lun", name, file_path, size_mb, is_block)

    def remove_lun(self, name: str):
        """
        Remove a LUN from the iSCSI target

        Args:
            name (str): Name of the LUN to remove

        Raises:
            ISCSIError: If the LUN cannot be removed
        """
        self.call("remove_lun", name)

    def list_luns(self) -> List[Dict[str, Any]]:
        """
        List all configured LUNs

        Returns:
            List[Dict[str, Any]]: List of dictionaries with LUN information
        """
        return self.call("list_luns")

    def _calculate_file_hash(self, file_path: str, operator: Optional[Operator] = None) -> str:
        """Calculate SHA256 hash of a file"""
        if operator is None:
            hash_obj = hashlib.sha256()
            with open(file_path, "rb") as f:
                while chunk := f.read(8192):
                    hash_obj.update(chunk)
            return hash_obj.hexdigest()
        else:
            hash_obj = hashlib.sha256()
            if isinstance(file_path, str) and file_path.startswith(("http://", "https://")):
                src_path = urlparse(file_path).path
            else:
                src_path = str(file_path)
            with operator.open(str(src_path), "rb") as f:
                while chunk := f.read(8192):
                    hash_obj.update(chunk)
            return hash_obj.hexdigest()

    def _files_are_identical(self, src: PathBuf, dst_path: str, operator: Optional[Operator] = None) -> bool:
        """Check if source and destination files are identical"""
        try:
            if not self.storage.exists(dst_path):
                self.logger.info(f"{dst_path} does not exist")
                return False

            dst_stat = self.storage.stat(dst_path)
            dst_size = dst_stat.content_length

            if operator is None:
                src_size = os.path.getsize(str(src))
            else:
                if isinstance(src, str) and src.startswith(("http://", "https://")):
                    src_path = urlparse(src).path
                else:
                    src_path = str(src)
                src_size = operator.stat(str(src_path)).content_length

            if src_size != dst_size:
                self.logger.info(f"Source size {src_size} != destination size {dst_size}")
                return False

            self.logger.info("checking hashes")
            src_hash = self._calculate_file_hash(str(src), operator)
            self.logger.info(f"Source hash: {src_hash}")
            dst_hash = self.storage.hash(dst_path, "sha256")
            self.logger.info(f"Destination hash: {dst_hash}")

            return src_hash == dst_hash

        except Exception:
            return False

    def _should_skip_upload(
        self, src_path: str, dst_path: str, operator: Optional[Operator], force_upload: bool, algo: Optional[str]
    ) -> bool:
        if force_upload or algo is not None or not self.storage.exists(dst_path):
            return False

        self.logger.info(f"Checking if {src_path} and {dst_path} are identical")
        if self._files_are_identical(src_path, dst_path, operator):
            self.logger.info(f"File {dst_path} already exists and is identical to source. Skipping upload...")
            return True

        self.logger.info(f"File {dst_path} is not identical to source")
        return False

    def _upload_file(
        self, src_path: str, dst_name: str, dst_path: str, operator: Optional[Operator], algo: Optional[str]
    ):
        if algo is None:
            self.logger.info(f"Uploading {src_path} to {dst_path}...")
            self.storage.write_from_path(dst_path, src_path, operator)
        else:
            ext_to_algo = {".gz": "gz", ".xz": "xz", ".bz2": "bz2"}
            ext = next(k for k, v in ext_to_algo.items() if v == algo)
            compressed_path = f"{dst_name}.img{ext}"
            self.logger.info(f"Uploading {src_path} to {compressed_path}...")
            self.storage.write_from_path(compressed_path, src_path, operator)
            self.logger.info(f"Decompressing on exporter: {compressed_path} -> {dst_name}.img ...")
            self.call("decompress", compressed_path, f"{dst_name}.img", algo)
            with contextlib.suppress(Exception):
                self.storage.delete(compressed_path)

    def upload_image(
        self,
        dst_name: str,
        src: PathBuf,
        size_mb: int = 0,
        operator: Optional[Operator] = None,
        force_upload: bool = False,
    ) -> str:
        """
        Upload an image file and expose it as a LUN

        Args:
            dst_name (str): Name to use for the LUN and local filename
            src (PathBuf): Source file path to read from
            size_mb (int): Size in MB if creating a new image. If 0 will use source file size.
            operator (Operator): Optional OpenDAL operator to use for reading
            force_upload (bool): If True, skip file comparison and force upload

        Returns:
            str: Target IQN for connecting to the LUN

        Raises:
            ISCSIError: If the operation fails
        """
        size_mb = int(size_mb)
        dst_path = f"{dst_name}.img"

        src_path = str(src)
        if operator is None and not src_path.startswith(("http://", "https://")):
            src_path = os.path.abspath(src_path)

        ext_to_algo = {".gz": "gz", ".xz": "xz", ".bz2": "bz2"}
        algo = next((v for k, v in ext_to_algo.items() if src_path.endswith(k)), None)

        if not self._should_skip_upload(src_path, dst_path, operator, force_upload, algo):
            self._upload_file(src_path, dst_name, dst_path, operator, algo)

        if size_mb <= 0:
            try:
                dst_stat = self.storage.stat(dst_path)
                size_mb = max(1, int(dst_stat.content_length) // (1024 * 1024))
            except Exception:
                size_mb = 1

        self.add_lun(dst_name, dst_path, size_mb)
        return self.get_target_iqn()

    def cli(self):
        base = super().cli()

        @base.command()
        @click.argument("file", type=str)
        @click.option("--name", "name", "-n", type=str, help="LUN name (defaults to basename without extension)")
        @click.option("--size-mb", type=int, default=0, show_default=True, help="Size in MB if creating a new image")
        @click.option(
            "--force-upload",
            is_flag=True,
            default=False,
            help="Force uploading even if the file appears identical on the exporter",
        )
        @click.option(
            "--header",
            "headers",
            multiple=True,
            help="Custom HTTP header in 'Key: Value' format. Repeatable.",
        )
        def serve(file: str, name: Optional[str], size_mb: int, force_upload: bool, headers: tuple[str, ...]):
            """Serve an image as an iSCSI LUN from a local path or HTTP(S) URL."""
            self.start()

            try:
                self.call("clear_all_luns")
            except Exception:
                pass

            if not name:
                candidate = urlparse(file).path if file.startswith(("http://", "https://")) else file
                name = self._normalized_name_from_file(candidate)

            src_path, src_operator, temp_cleanup = self._get_src_and_operator(file, headers)
            try:
                iqn = self.upload_image(
                    name, src_path, size_mb=size_mb, operator=src_operator, force_upload=force_upload
                )
            finally:
                if temp_cleanup is not None:
                    with contextlib.suppress(Exception):
                        os.remove(temp_cleanup)
            host = self.get_host()
            port = self.get_port()

            click.echo(f"{host}:{port} {iqn}")

        return base
