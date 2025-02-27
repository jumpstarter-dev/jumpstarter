from dataclasses import dataclass

from jumpstarter_driver_composite.client import CompositeClient


@dataclass(kw_only=True)
class TftpServerClient(CompositeClient):
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
