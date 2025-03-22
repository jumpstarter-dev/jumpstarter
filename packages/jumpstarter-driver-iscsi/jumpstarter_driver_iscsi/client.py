import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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

    def upload_image(self, dst_name: str, src: PathBuf, size_mb: int = 0, operator: Optional[Operator] = None) -> str:
        """
        Upload an image file and expose it as a LUN

        Args:
            dst_name (str): Name to use for the LUN and local filename
            src (PathBuf): Source file path to read from
            size_mb (int): Size in MB if creating a new image. If 0 will use source file size.
            operator (Operator): Optional OpenDAL operator to use for reading

        Returns:
            str: Target IQN for connecting to the LUN

        Raises:
            ISCSIError: If the operation fails
        """
        # Ensure size_mb is an integer
        size_mb = int(size_mb)

        # Upload the file using OpenDAL
        dst_path = f"{dst_name}.img"
        self.storage.write_from_path(dst_path, src, operator)

        # If size not specified, get it from the uploaded file
        if size_mb <= 0:
            src_path = os.path.join(self.storage._storage.root_dir, dst_path)
            size_mb = os.path.getsize(src_path) // (1024 * 1024)
            if size_mb <= 0:
                size_mb = 1  # Minimum 1MB

        # Add it as a LUN with explicit size
        self.add_lun(dst_name, dst_path, size_mb)

        # Return the full connection string
        return self.get_target_iqn()
