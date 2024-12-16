from dataclasses import dataclass
from pathlib import Path

from opendal import Operator

from jumpstarter.client import DriverClient
from jumpstarter.client.adapters.opendal import OpendalAdapter


@dataclass(kw_only=True)
class HttpServerClient(DriverClient):
    """Client for the HTTP server driver"""

    def start(self):
        """
        Start the HTTP server.

        Initializes and starts the HTTP server if it's not already running.
        The server will listen on the configured host and port.
        """
        self.call("start")

    def stop(self):
        """
        Stop the HTTP server.

        Stops the running HTTP server and releases associated resources.
        Raises:
            ServerNotRunning: If the server is not currently running
        """
        self.call("stop")

    def list_files(self) -> list[str]:
        """
        List all files in the HTTP server's root directory.

        Returns:
            list[str]: A list of filenames present in the HTTP server's root directory
        """
        return self.call("list_files")

    def put_file(self, filename: str, src_stream):
        """
        Upload a file to the HTTP server using a streamed source.

        Args:
            filename (str): Name to save the file as on the server.
            src_stream: Stream/source to read the file data from.

        Returns:
            str: Name of the uploaded file
        """
        return self.call("put_file", filename, src_stream)

    def put_local_file(self, filepath: str) -> str:
        """
        Upload a file from the local filesystem to the HTTP server.

        Note: This doesn't use HTTP to upload; it streams the file content directly.

        Args:
            filepath (str): Path to the local file to upload.

        Returns:
            str: Name of the uploaded file

        Example:
            >>> client.put_local_file("/path/to/local/file.txt")
        """
        absolute = Path(filepath).resolve()
        with OpendalAdapter(
            client=self,
            operator=Operator("fs", root="/"),
            path=str(absolute),
            mode="rb"
        ) as handle:
            return self.call("put_file", absolute.name, handle)

    def delete_file(self, filename: str) -> str:
        """
        Delete a file from the HTTP server.

        Args:
            filename (str): Name of the file to delete.

        Returns:
            str: Name of the deleted file
        """
        return self.call("delete_file", filename)

    def get_host(self) -> str:
        """
        Get the host IP address the HTTP server is listening on.

        Returns:
            str: The IP address or hostname the server is bound to
        """
        return self.call("get_host")

    def get_port(self) -> int:
        """
        Get the port number the HTTP server is listening on.

        Returns:
            int: The port number (default is 8080)
        """
        return self.call("get_port")

    def get_url(self) -> str:
        """
        Get the base URL of the HTTP server.

        Returns:
            str: The base URL of the server
        """
        return self.call("get_url")
