from dataclasses import dataclass

from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_opendal.common import PathBuf
from opendal import Operator
from yarl import URL


@dataclass(kw_only=True)
class HttpServerClient(CompositeClient):
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

    def put_file(self, dst: PathBuf, src: PathBuf, operator: Operator | None = None) -> str:
        """
        Upload a file to the HTTP server using a opendal operator as source.

        Args:
            dst (PathBuf): Name to save the file as on the server.
            src (PathBuf): Name to read the file from opendal operator.
            operator (Operator): opendal operator to read the file from, defaults to local fs.

        Returns:
            str: URL of the uploaded file
        """
        self.storage.write_from_path(dst, src, operator)

        return str(URL(self.get_url()).joinpath(dst))
