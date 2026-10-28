import os
from contextlib import ExitStack
from dataclasses import dataclass, field

from anyio.from_thread import start_blocking_portal
from jumpstarter_driver_opendal.driver import Opendal

from jumpstarter_driver_tftp.server import TftpServer

from jumpstarter.common.ipaddr import get_ip_address
from jumpstarter.driver import Driver, export


class TftpError(Exception):
    """Base exception for TFTP server errors"""



class ServerNotRunning(TftpError):
    """Server is not running"""



@dataclass(kw_only=True)
class Tftp(Driver):
    """TFTP Server driver for Jumpstarter

    This driver implements a TFTP read-only server.

    Attributes:
        root_dir (str): Root directory for the TFTP server. Defaults to "/var/lib/tftpboot"
        host (str): IP address to bind the server to. If empty, will use the default route interface
        port (int): Port number to listen on. Defaults to 69 (standard TFTP port)
        advertised_host (str | None): IP address to advertise to clients via get_host()
            without binding to it. Defaults to the bind host.
    """

    driver_type = "storage"

    root_dir: str = "/var/lib/tftpboot"
    host: str = field(default="")
    port: int = 69
    advertised_host: str | None = None
    remove_created_on_close: bool = True  # Clean up temporary boot files by default
    server: TftpServer | None = field(init=False, default=None)
    _running: ExitStack | None = field(init=False, default=None)

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
            self.host = get_ip_address(logger=self.logger)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_tftp.client.TftpServerClient"

    @export
    def start(self):
        """Start the TFTP server.

        The server will start listening for incoming TFTP requests on the configured
        host and port. If the server is already running, a warning will be logged.

        Raises:
            TftpError: If the server cannot listen on the configured host and port
        """
        if self._running is not None:
            self.logger.warning("TFTP server is already running")
            return

        server = TftpServer(
            host=self.host,
            port=self.port,
            operator=self.children["storage"]._operator,  # ty: ignore[unresolved-attribute]
            logger=self.logger,
        )
        with ExitStack() as stack:
            portal = stack.enter_context(start_blocking_portal())
            try:
                serving, _ = portal.start_task(server.serve)
            except Exception as e:
                raise TftpError(f"Failed to start TFTP server: {e}") from e
            stack.callback(serving.cancel)
            self._running = stack.pop_all()
        self.server = server
        self.logger.info(f"TFTP server started on {self.host}:{self.port}")

    @export
    def stop(self):
        """Stop the TFTP server, ending the transfers in progress.

        If the server is not running, a warning will be logged.
        """
        if self._running is None:
            self.logger.warning("stop called - TFTP server is not running")
            return

        self._running.close()
        self._running = None
        self.logger.info("TFTP server stopped")

    @export
    def get_host(self) -> str:
        """Get the host address clients should use to reach the server.

        Returns:
            str: The advertised IP address or hostname, or the bind host
        """
        return self.advertised_host or self.host

    @export
    def get_port(self) -> int:
        """Get the port number the server is listening on.

        Returns:
            int: The port number
        """
        return self.port

    def close(self):
        if self._running is not None:
            self.stop()
        super().close()
