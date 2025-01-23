from dataclasses import dataclass
from pathlib import Path

from jumpstarter.client import DriverClient
from jumpstarter_driver_opendal.adapter import OpendalAdapter
from opendal import Operator


@dataclass(kw_only=True)
class TftpServerClient(DriverClient):
    """
    Client interface for TFTP Server driver

    This client provides methods to control a TFTP server and manage files on it.
    Supports file operations like uploading from various storage backends through OpenDAL.
    """

    def start(self):
        """
        Start the TFTP server

        Initializes and starts the TFTP server if it's not already running.
        The server will listen on the configured host and port.
        """
        self.call("start")

    def stop(self):
        """
        Stop the TFTP server

        Stops the running TFTP server and releases associated resources.

        Raises:
            ServerNotRunning: If the server is not currently running
        """
        self.call("stop")

    def list_files(self) -> list[str]:
        """
        List files in the TFTP server root directory

        Returns:
            list[str]: A list of filenames present in the TFTP server's root directory
        """
        return self.call("list_files")

    def put_file(self, operator: Operator, path: str):
        """
        Upload a file to the TFTP server using an OpenDAL operator

        Args:
            operator (Operator): OpenDAL operator for accessing the source storage
            path (str): Path to the file in the source storage system

        Returns:
            str: Name of the uploaded file
        """
        filename = Path(path).name
        with OpendalAdapter(client=self, operator=operator, path=path, mode="rb") as handle:
            return self.call("put_file", filename, handle)

    def put_local_file(self, filepath: str):
        """
        Upload a file from the local filesystem to the TFTP server
        Note: this doesn't use TFTP to upload.

        Args:
            filepath (str): Path to the local file to upload

        Returns:
            str: Name of the uploaded file

        Example:
            >>> client.put_local_file("/path/to/local/file.txt")
        """
        absolute = Path(filepath).resolve()
        with OpendalAdapter(client=self, operator=Operator("fs", root="/"), path=str(absolute), mode="rb") as handle:
            return self.call("put_file", absolute.name, handle)

    def delete_file(self, filename: str):
        """
        Delete a file from the TFTP server

        Args:
            filename (str): Name of the file to delete

        Raises:
            FileNotFound: If the specified file doesn't exist
            TftpError: If deletion fails for other reasons
        """
        return self.call("delete_file", filename)

    def get_host(self) -> str:
        """
        Get the host address the TFTP server is listening on

        Returns:
            str: The IP address or hostname the server is bound to
        """
        return self.call("get_host")

    def get_port(self) -> int:
        """
        Get the port number the TFTP server is listening on

        Returns:
            int: The port number (default is 69)
        """
        return self.call("get_port")
