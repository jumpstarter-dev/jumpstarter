import os
import socket
from contextlib import contextmanager
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
        iqn_prefix (str): iSCSI Qualified Name prefix
        target_name (str): Target name. Defaults to "target1"
        host (str): IP address to bind the server to
        port (int): Port number to listen on. Defaults to 3260
    """

    root_dir: str = "/var/lib/iscsi"
    iqn_prefix: str = "iqn.2024-06.dev.jumpstarter"
    target_name: str = "target1"
    host: str = field(default="")
    port: int = 3260

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

        self.children["storage"] = Opendal(scheme="fs", kwargs={"root": self.root_dir})
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

    @contextmanager
    def _configure_target(self):
        """Configure the iSCSI target"""
        self._rtsroot = RTSRoot()

        self._setup_target()
        self._setup_network_portal()
        self._configure_tpg_attributes()

        try:
            yield
        except Exception as e:
            self.logger.error(f"Error in iSCSI target configuration: {e}")
            raise

    def _setup_target(self):
        """Setup the iSCSI target"""
        target_exists = False
        try:
            targets_list = list(self._rtsroot.targets)
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
            # Create a new target
            self.logger.info(f"Creating new target: {self._iqn}")
            fabric_modules = {m.name: m for m in list(self._rtsroot.fabric_modules)}
            iscsi_fabric = fabric_modules.get("iscsi")
            if not iscsi_fabric:
                raise ISCSIError("Could not find iSCSI fabric module")
            self._target = Target(iscsi_fabric, self._iqn)
            self._tpg = TPG(self._target, 1)

        self._tpg.enable = True

    def _setup_network_portal(self):
        """Setup the network portal for the target"""
        portal_exists = False
        try:
            portals = list(self._tpg.network_portals)
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
        self._tpg.set_attribute("authentication", "0")
        self._tpg.set_attribute("generate_node_acls", "1")
        self._tpg.set_attribute("demo_mode_write_protect", "0")

    @export
    def start(self):
        """Start the iSCSI target server.

        Configures and starts the iSCSI target with the current configuration.

        Raises:
            ISCSIError: If the server fails to start
        """
        try:
            with self._configure_target():
                self.logger.info(f"iSCSI target server started at {self.host}:{self.port}")
        except Exception as e:
            raise ISCSIError(f"Failed to start iSCSI target server: {e}") from e

    @export
    def stop(self):
        """Stop the iSCSI target server.

        Cleans up and stops the iSCSI target server.
        """
        try:
            for name in list(self._luns.keys()):
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
        """Get the host address the server is bound to.

        Returns:
            str: The IP address or hostname
        """
        return self.host

    @export
    def get_port(self) -> int:
        """Get the port number the server is listening on.

        Returns:
            int: The port number
        """
        return self.port

    @export
    def get_target_iqn(self) -> str:
        """Get the iSCSI Qualified Name (IQN) of the target.

        Returns:
            str: The IQN string
        """
        return self._iqn

    @export
    def add_lun(self, name: str, file_path: str, size_mb: int = 0, is_block: bool = False) -> str:
        """Add a new LUN to the iSCSI target.

        Args:
            name (str): Unique name for the LUN
            file_path (str): Path to the file or block device
            size_mb (int): Size in MB for new file or existing file LUN
            is_block (bool): If True, the path is treated as a block device

        Returns:
            str: Name of the created LUN

        Raises:
            ISCSIError: If the LUN cannot be created
        """
        if not self._tpg:
            raise ISCSIError("iSCSI target not started, call start() first")

        if name in self._luns:
            raise ISCSIError(f"LUN with name {name} already exists")

        try:
            size_mb = int(size_mb)
        except (TypeError, ValueError) as e:
            raise ISCSIError("size_mb must be an integer value") from e

        full_path = os.path.join(self.root_dir, file_path)

        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        try:
            if is_block:
                if not os.path.exists(full_path) or not os.path.isfile(full_path):
                    raise ISCSIError(f"Block device {full_path} does not exist")
                storage_obj = BlockStorageObject(name, full_path)
            else:
                if size_mb <= 0:
                    raise ISCSIError("size_mb must be > 0 for file-backed LUNs")

                size_bytes = size_mb * 1024 * 1024

                if not os.path.exists(full_path):
                    # Create a sparse file of specified size
                    with open(full_path, "wb") as f:
                        f.truncate(size_bytes)
                    self.logger.info(f"Created new file {full_path} with size {size_mb}MB")
                else:
                    # Resize the file if it exists
                    with open(full_path, "wb") as f:
                        f.truncate(size_bytes)
                    self.logger.info(f"Resized file {full_path} to {size_mb}MB")

                storage_obj = FileIOStorageObject(name, full_path, size=size_bytes)

            lun = LUN(self._tpg, 0, storage_obj)
            self._storage_objects[name] = storage_obj
            self._luns[name] = lun

            self.logger.info(f"Added LUN {name} for path {full_path}")
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
        """List all configured LUNs.

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
        """Clean up resources when the driver is closed."""
        try:
            self.stop()
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        super().close()
