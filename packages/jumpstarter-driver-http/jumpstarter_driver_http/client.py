from dataclasses import dataclass

from jumpstarter_driver_opendal.client import FileServerClient


@dataclass(kw_only=True)
class HttpServerClient(FileServerClient):
    """Client for the HTTP server driver"""

    def get_url(self) -> str:
        """Get the base URL of the HTTP server"""
        return self.call("get_url")
