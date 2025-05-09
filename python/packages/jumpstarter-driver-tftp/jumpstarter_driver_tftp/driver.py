import asyncio
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

from jumpstarter_driver_opendal.driver import Opendal

from jumpstarter_driver_tftp.server import TftpServer

from jumpstarter.common.ipaddr import get_ip_address
from jumpstarter.driver import Driver, export


class TftpError(Exception):
    """Base exception for TFTP server errors"""

    pass


class ServerNotRunning(TftpError):
    """Server is not running"""

    pass


@dataclass(kw_only=True)
class Tftp(Driver):
    """TFTP Server driver for Jumpstarter

    This driver implements a TFTP read-only server.

    Attributes:
        root_dir (str): Root directory for the TFTP server. Defaults to "/var/lib/tftpboot"
        host (str): IP address to bind the server to. If empty, will use the default route interface
        port (int): Port number to listen on. Defaults to 69 (standard TFTP port)
    """

    root_dir: str = "/var/lib/tftpboot"
    host: str = field(default="")
    port: int = 69
    server: Optional["TftpServer"] = field(init=False, default=None)
    server_thread: Optional[threading.Thread] = field(init=False, default=None)
    _shutdown_event: threading.Event = field(init=False, default_factory=threading.Event)
    _loop_ready: threading.Event = field(init=False, default_factory=threading.Event)
    _loop: Optional[asyncio.AbstractEventLoop] = field(init=False, default=None)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        os.makedirs(self.root_dir, exist_ok=True)

        self.children["storage"] = Opendal(scheme="fs", kwargs={"root": self.root_dir})
        self.storage = self.children["storage"]

        if self.host == "":
            self.host = get_ip_address(logger=self.logger)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_tftp.client.TftpServerClient"

    def _start_server(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self.server = TftpServer(
            host=self.host,
            port=self.port,
            operator=self.children["storage"]._operator,
            logger=self.logger,
        )
        try:
            self._loop_ready.set()
            self._loop.run_until_complete(self._run_server())
        except Exception as e:
            self.logger.error(f"Error running TFTP server: {e}")
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                self._loop.close()
            except Exception as e:
                self.logger.error(f"Error during event loop cleanup: {e}")
            self._loop = None
            self.logger.info("TFTP server thread completed")

    async def _run_server(self):
        try:
            server_task = asyncio.create_task(self.server.start())
            await asyncio.gather(server_task, self._wait_for_shutdown())
        except asyncio.CancelledError:
            self.logger.info("Server task cancelled")
            raise

    async def _wait_for_shutdown(self):
        while not self._shutdown_event.is_set():
            await asyncio.sleep(0.1)
        self.logger.info("Shutdown event detected")
        if self.server is not None:
            await self.server.shutdown()

    @export
    def start(self):
        """Start the TFTP server.

        The server will start listening for incoming TFTP requests on the configured
        host and port. If the server is already running, a warning will be logged.

        Raises:
            TftpError: If the server fails to start or times out during initialization
        """
        if self.server_thread is not None and self.server_thread.is_alive():
            self.logger.warning("TFTP server is already running")
            return

        self._shutdown_event.clear()
        self._loop_ready.clear()

        self.server_thread = threading.Thread(target=self._start_server, daemon=True)
        self.server_thread.start()

        if not self._loop_ready.wait(timeout=5.0):
            self.logger.error("Timeout waiting for event loop to be ready")
            self.server_thread = None
            raise TftpError("Failed to start TFTP server - event loop initialization timeout")

        self.logger.info(f"TFTP server started on {self.host}:{self.port}")

    @export
    def stop(self):
        """Stop the TFTP server.

        Initiates a graceful shutdown of the server and waits for all active transfers
        to complete. If the server is not running, a warning will be logged.
        """
        if self.server_thread is None or not self.server_thread.is_alive():
            self.logger.warning("stop called - TFTP server is not running")
            return

        self.logger.info("Initiating TFTP server shutdown")
        self._shutdown_event.set()
        self.server_thread.join(timeout=10)
        if self.server_thread.is_alive():
            self.logger.error("Failed to stop TFTP server thread within timeout")
        else:
            self.logger.info("TFTP server stopped successfully")
            self.server_thread = None

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

    def close(self):
        if self.server_thread is not None:
            self.stop()
        super().close()
