import bz2
import gzip
import lzma
import os
import socket
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from jumpstarter_driver_opendal.driver import Opendal
from rtslib_fb import LUN, TPG, BlockStorageObject, FileIOStorageObject, NetworkPortal, RTSRoot, Target

from jumpstarter.driver import Driver, export


class ISCSIError(Exception):
    """Base exception for iSCSI server errors"""

    pass


class ConfigurationError(ISCSIError):
    """Error in iSCSI configuration"""

    pass


class StorageObjectError(ISCSIError):
    """Error related to storage objects"""

    pass


@dataclass(kw_only=True)
class ISCSI(Driver):
    """iSCSI Target driver for Jumpstarter

    This driver implements an iSCSI target server that can expose files or block devices.

    Attributes:
        root_dir (str): Root directory for the iSCSI storage
        iqn_prefix (str): iqn prefix
        target_name (str): Target name. Defaults to "target1"
        host (str): IP address to bind the server to
        port (int): Port number to listen on. Defaults to 3260
    """

    root_dir: str = "/var/lib/iscsi"
    iqn_prefix: str = "iqn.2024-06.dev.jumpstarter"
    target_name: str = "target1"
    host: str = field(default="")
    port: int = 3260
    remove_created_on_close: bool = False  # Keep disk images persistent by default

    _rtsroot: Optional[RTSRoot] = field(init=False, default=None)
    _target: Optional[Target] = field(init=False, default=None)
    _tpg: Optional[TPG] = field(init=False, default=None)
    _storage_objects: Dict[str, Any] = field(init=False, default_factory=dict)
    _portals: List[NetworkPortal] = field(init=False, default_factory=list)
    _luns: Dict[str, LUN] = field(init=False, default_factory=dict)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        os.makedirs(self.root_dir, exist_ok=True)

        self.children["storage"] = Opendal(
            scheme="fs",
            kwargs={"root": self.root_dir},
            remove_created_on_close=self.remove_created_on_close
        )
        self.storage = self.children["storage"]

        if self.host == "":
            self.host = self.get_default_ip()

        self._iqn = f"{self.iqn_prefix}:{self.target_name}"

    def get_default_ip(self):
        """Get the IP address of the default route interface"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            self.logger.warning("Could not determine default IP address, falling back to 0.0.0.0")
            return "0.0.0.0"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_iscsi.client.ISCSIServerClient"

    def _configure_target(self):
        """Helper that configures the target; formerly a context-manager but the
        implicit enter/exit semantics were confusing and the driver never
        needed teardown at this point.
        """
        try:
            self._rtsroot = RTSRoot()

            self._setup_target()
            self._setup_network_portal()
            self._configure_tpg_attributes()
        except Exception as e:
            self.logger.error(f"Error in iSCSI target configuration: {e}")
            raise

    def _setup_target(self):
        """Setup the iSCSI target"""
        target_exists = False
        try:
            targets_list = list(self._rtsroot.targets)  # type: ignore[attr-defined]
            for target in targets_list:
                if target.wwn == self._iqn:
                    self._target = target
                    target_exists = True
                    self.logger.info(f"Using existing target: {self._iqn}")
                    if target.tpgs:
                        self._tpg = list(target.tpgs)[0]
                    else:
                        self._tpg = TPG(self._target, 1)
                    break
        except Exception as e:
            self.logger.warning(f"Error checking for existing target: {e}")

        if not target_exists:
            self.logger.info(f"Creating new target: {self._iqn}")
            fabric_modules = {m.name: m for m in list(self._rtsroot.fabric_modules)}  # type: ignore[attr-defined]
            iscsi_fabric = fabric_modules.get("iscsi")
            if not iscsi_fabric:
                raise ISCSIError("Could not find iSCSI fabric module")
            self._target = Target(iscsi_fabric, self._iqn)
            self._tpg = TPG(self._target, 1)

        self._tpg.enable = True  # type: ignore[attr-defined]

    def _setup_network_portal(self):
        """Setup the network portal for the target"""
        portal_exists = False
        try:
            portals = list(self._tpg.network_portals)  # type: ignore[attr-defined]
            for portal in portals:
                if portal.ip_address == self.host and portal.port == self.port:
                    portal_exists = True
                    break
        except Exception as e:
            self.logger.warning(f"Error checking for existing portal: {e}")

        if not portal_exists:
            self.logger.info(f"Creating network portal on {self.host}:{self.port}")
            NetworkPortal(self._tpg, self.host, self.port)

    def _configure_tpg_attributes(self):
        """Configure TPG attributes"""
        self._tpg.set_attribute("authentication", "0")  # type: ignore[attr-defined]
        self._tpg.set_attribute("generate_node_acls", "1")  # type: ignore[attr-defined]
        self._tpg.set_attribute("demo_mode_write_protect", "0")  # type: ignore[attr-defined]

    def _clear_tpg_luns(self):
        """Clear all LUNs from the current TPG"""
        try:
            for lun in list(self._tpg.luns):  # type: ignore[attr-defined]
                try:
                    storage_obj = getattr(lun, "storage_object", None)
                except Exception:
                    storage_obj = None

                try:
                    lun.delete()
                finally:
                    if storage_obj is not None:
                        with suppress(Exception):
                            storage_obj.delete()
        except Exception as e:
            self.logger.warning(f"Failed clearing existing LUNs from TPG: {e}")

    def _cleanup_orphan_storage_objects(self):
        """Clean up orphan storage objects under root_dir"""
        try:
            root_abs = os.path.abspath(self.root_dir)
            for so in list(self._rtsroot.storage_objects):  # type: ignore[attr-defined]
                try:
                    if isinstance(so, FileIOStorageObject):
                        udev_path = os.path.abspath(getattr(so, "udev_path", ""))
                        if udev_path.startswith(root_abs + os.sep) or udev_path == root_abs:
                            with suppress(Exception):
                                so.delete()
                except Exception:
                    continue
        except Exception as e:
            self.logger.debug(f"No orphan storage object cleanup performed: {e}")

    @export
    def clear_all_luns(self):
        """Remove all existing LUNs and their backstores, including any orphans under root_dir"""
        if self._tpg is None:
            self._configure_target()

        self._clear_tpg_luns()
        self._luns.clear()
        self._storage_objects.clear()
        self._cleanup_orphan_storage_objects()

    @export
    def start(self):
        """Start the iSCSI target server

        Configures and starts the iSCSI target with the current configuration

        Raises:
            ISCSIError: If the server fails to start
        """
        try:
            self._configure_target()
            self.logger.info(f"iSCSI target server started at {self.host}:{self.port}")
        except Exception as e:
            raise ISCSIError(f"Failed to start iSCSI target server: {e}") from e

    @export
    def stop(self):
        """Stop the iSCSI target server

        Cleans up and stops the iSCSI target server
        """
        try:
            for name in list(self._luns.keys()):
                # TODO: maybe leave?
                self.remove_lun(name)

            if self._target:
                self._target.delete()
                self._target = None

            self.logger.info("iSCSI target server stopped")
        except Exception as e:
            self.logger.error(f"Error stopping iSCSI server: {e}")
            raise ISCSIError(f"Failed to stop iSCSI target: {e}") from e

    @export
    def get_host(self) -> str:
        """Get the host address the server is bound to

        Returns:
            str: The IP address or hostname
        """
        return self.host

    @export
    def get_port(self) -> int:
        """Get the port number the server is listening on

        Returns:
            int: The port number
        """
        return self.port

    @export
    def get_target_iqn(self) -> str:
        """Get the IQN of the target

        Returns:
            str: The IQN string
        """
        return self._iqn

    def _validate_lun_inputs(self, name: str, size_mb: int) -> int:
        """Validate LUN inputs and return validated size_mb"""
        if name in self._luns:
            raise ISCSIError(f"LUN with name {name} already exists")
        try:
            return int(size_mb)
        except (TypeError, ValueError) as e:
            raise ISCSIError("size_mb must be an integer value") from e

    def _get_full_path(self, file_path: str, is_block: bool) -> str:
        """Get the full path for the LUN file or block device"""
        if is_block:
            if not os.path.isabs(file_path):
                raise ISCSIError("For block devices, file_path must be an absolute path")
            return file_path
        else:
            normalized_path = os.path.normpath(file_path)

            if normalized_path.startswith("..") or os.path.isabs(normalized_path):
                raise ISCSIError(f"Invalid file path: {file_path}")

            full_path = os.path.join(self.root_dir, normalized_path)
            resolved_path = os.path.abspath(full_path)
            root_path = os.path.abspath(self.root_dir)

            if not resolved_path.startswith(root_path + os.sep) and resolved_path != root_path:
                raise ISCSIError(f"Path traversal attempt detected: {file_path}")
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            return full_path

    def _safe_join_under_root(self, rel_path: str) -> str:
        normalized_path = os.path.normpath(rel_path)
        if normalized_path.startswith("..") or os.path.isabs(normalized_path):
            raise ISCSIError(f"Invalid path: {rel_path}")
        full_path = os.path.join(self.root_dir, normalized_path)
        resolved_path = os.path.abspath(full_path)
        root_path = os.path.abspath(self.root_dir)
        if not resolved_path.startswith(root_path + os.sep) and resolved_path != root_path:
            raise ISCSIError(f"Path traversal attempt detected: {rel_path}")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        return full_path

    @export
    def decompress(self, src_path: str, dst_path: str, algo: str) -> None:
        """Decompress a file under storage root into another path under storage root.

        src_path and dst_path are relative to root_dir.
        algo is one of: gz, xz, bz2
        """
        src_full = self._safe_join_under_root(src_path)
        dst_full = self._safe_join_under_root(dst_path)

        def _copy_stream(read_f, write_f):
            while True:
                chunk = read_f.read(1024 * 1024)
                if not chunk:
                    break
                write_f.write(chunk)

        try:
            if algo == "gz":
                with open(dst_full, "wb") as out_f:
                    with gzip.open(src_full, "rb") as decomp:
                        _copy_stream(decomp, out_f)
            elif algo == "xz":
                with open(dst_full, "wb") as out_f:
                    with lzma.open(src_full, "rb") as decomp:
                        _copy_stream(decomp, out_f)
            elif algo == "bz2":
                with open(dst_full, "wb") as out_f:
                    with bz2.open(src_full, "rb") as decomp:
                        _copy_stream(decomp, out_f)
            else:
                raise ISCSIError(f"Unsupported compression algo: {algo}")
        except Exception as e:
            raise ISCSIError(f"Decompression failed: {e}") from e

    def _create_file_storage_object(self, name: str, full_path: str, size_mb: int) -> tuple:
        """Create file-backed storage object and return (storage_obj, final_size_mb)"""
        if not os.path.exists(full_path):
            if size_mb <= 0:
                raise ISCSIError("size_mb must be > 0 for new file-backed LUNs")
            size_bytes = size_mb * 1024 * 1024
            with open(full_path, "wb") as f:
                f.truncate(size_bytes)
            self.logger.info(f"Created new file {full_path} with size {size_mb}MB")
        else:
            current_size = os.path.getsize(full_path)
            if size_mb <= 0:
                size_bytes = current_size
                size_mb = size_bytes // (1024 * 1024)
                self.logger.info(f"Using existing file size: {size_mb}MB")
            else:
                size_bytes = size_mb * 1024 * 1024
                if current_size != size_bytes:
                    if current_size < size_bytes:
                        with open(full_path, "ab") as f:
                            f.truncate(size_bytes)
                        self.logger.info(
                            f"Extended file {full_path} from {current_size / (1024 * 1024):.1f}MB to {size_mb}MB"
                        )
                    else:
                        self.logger.warning(
                            f"File {full_path} is larger ({current_size / (1024 * 1024):.1f}MB) "
                            f"than requested size ({size_mb}MB). "
                            "Using requested size for LUN but file won't be truncated."
                        )
        return FileIOStorageObject(name, full_path, size=size_bytes), size_mb

    @export
    def add_lun(self, name: str, file_path: str, size_mb: int = 0, is_block: bool = False) -> str:
        """
        Add a new LUN to the iSCSI target.

        For file-backed LUNs (is_block=False), the provided file_path is relative to the configured storage root.
        For block devices (is_block=True), the file_path must be an absolute path and will be used as provided.

        Args:
            name (str): Unique name for the LUN.
            file_path (str): Path to the file or block device.
            size_mb (int): Size in MB for new file-backed LUNs (ignored for block devices).
            is_block (bool): If True, treat file_path as an absolute block device path.

        Returns:
            str: The name of the created LUN.

        Raises:
            ISCSIError: On error or if the file_path is invalid.
        """
        if name in self._luns:
            with suppress(Exception):
                self.remove_lun(name)

        size_mb = self._validate_lun_inputs(name, size_mb)
        full_path = self._get_full_path(file_path, is_block)

        try:
            if is_block:
                if not os.path.exists(full_path):
                    raise ISCSIError(f"Block device {full_path} does not exist")
                storage_obj = BlockStorageObject(name, full_path)
            else:
                storage_obj, size_mb = self._create_file_storage_object(name, full_path, size_mb)

            lun = LUN(self._tpg, 0, storage_obj)
            self._storage_objects[name] = storage_obj
            self._luns[name] = lun
            self.logger.info(f"Added LUN {name} for path {full_path} with size {size_mb}MB")
            return name
        except Exception as e:
            self.logger.error(f"Error adding LUN: {e}")
            raise ISCSIError(f"Failed to add LUN: {e}") from e

    @export
    def remove_lun(self, name: str):
        """Remove a LUN from the iSCSI target

        Args:
            name (str): Name of the LUN to remove

        Raises:
            ISCSIError: If the LUN cannot be removed
        """
        if not self._tpg:
            raise ISCSIError("iSCSI target not started, call start() first")

        if name not in self._luns:
            raise ISCSIError(f"LUN with name {name} does not exist")

        try:
            self._luns[name].delete()
            self._storage_objects[name].delete()

            del self._luns[name]
            del self._storage_objects[name]

            self.logger.info(f"Removed LUN {name}")
        except Exception as e:
            self.logger.error(f"Error removing LUN: {e}")
            raise ISCSIError(f"Failed to remove LUN: {e}") from e

    @export
    def list_luns(self) -> List[Dict[str, Any]]:
        """List all configured LUNs

        Returns:
            List[Dict[str, Any]]: List of dictionaries with LUN information
        """
        result = []
        for name, lun in self._luns.items():
            storage_obj = self._storage_objects[name]
            lun_info = {
                "name": name,
                "path": storage_obj.udev_path,
                "size": storage_obj.size,
                "lun_id": lun.lun,
                "is_block": isinstance(storage_obj, BlockStorageObject),
            }
            result.append(lun_info)
        return result

    def close(self):
        """Clean up resources when the driver is closed"""
        try:
            self.stop()
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        super().close()
