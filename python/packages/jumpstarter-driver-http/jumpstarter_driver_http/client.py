from dataclasses import dataclass

from jumpstarter_driver_composite.client import CompositeClient


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
