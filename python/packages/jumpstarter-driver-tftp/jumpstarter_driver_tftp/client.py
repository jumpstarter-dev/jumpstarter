import hashlib
from dataclasses import dataclass
from pathlib import Path

from jumpstarter_driver_opendal.adapter import OpendalAdapter
from opendal import Operator

from . import CHUNK_SIZE
from jumpstarter.client import DriverClient


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
        filename = Path(path).name
        client_checksum = self._compute_checksum(operator, path)

        if self.call("check_file_checksum", filename, client_checksum):
            self.logger.info(f"Skipping upload of identical file: {filename}")
            return filename

        with OpendalAdapter(client=self, operator=operator, path=path, mode="rb") as handle:
            return self.call("put_file", filename, handle, client_checksum)

    def put_local_file(self, filepath: str):
        absolute = Path(filepath).resolve()
        filename = absolute.name

        operator = Operator("fs", root="/")
        client_checksum = self._compute_checksum(operator, str(absolute))

        if self.call("check_file_checksum", filename, client_checksum):
            self.logger.info(f"Skipping upload of identical file: {filename}")
            return filename

        self.logger.info(f"checksum: {client_checksum}")
        with OpendalAdapter(client=self, operator=operator, path=str(absolute), mode="rb") as handle:
            return self.call("put_file", filename, handle, client_checksum)

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

    def _compute_checksum(self, operator: Operator, path: str) -> str:
        hasher = hashlib.sha256()
        with operator.open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()
