import asyncio
import hashlib
import os
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from anyio.streams.file import FileWriteStream

from jumpstarter_driver_tftp.server import TftpServer

from . import CHUNK_SIZE
from jumpstarter.driver import Driver, export


class TftpError(Exception):
    """Base exception for TFTP server errors"""

    pass


class ServerNotRunning(TftpError):
    """Server is not running"""

    pass


class FileNotFound(TftpError):
    """File not found"""

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
        if self.host == "":
            self.host = self.get_default_ip()

    def get_default_ip(self):
        """Get the IP address of the default route interface"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            self.logger.warning("Could not determine default IP address, falling back to 0.0.0.0")
            return "0.0.0.0"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_tftp.client.TftpServerClient"

    def _start_server(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self.server = TftpServer(host=self.host, port=self.port, root_dir=self.root_dir)
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
    def list_files(self) -> list[str]:
        """List all files available in the TFTP server root directory.

        Returns:
            list[str]: A list of filenames present in the root directory
        """
        return os.listdir(self.root_dir)

    @export
    async def put_file(self, filename: str, src_stream, client_checksum: str):
        """Upload a file to the TFTP server.

        Args:
            filename (str): Name of the file to create
            src_stream: Source stream to read the file data from
            client_checksum (str): SHA256 checksum of the file for verification

        Returns:
            str: The filename that was uploaded

        Raises:
            TftpError: If the file upload fails or path validation fails
        """
        file_path = os.path.join(self.root_dir, filename)

        try:
            if not Path(file_path).resolve().is_relative_to(Path(self.root_dir).resolve()):
                raise TftpError("Invalid target path")

            async with await FileWriteStream.from_path(file_path) as dst:
                async with self.resource(src_stream) as src:
                    async for chunk in src:
                        await dst.send(chunk)

            return filename
        except Exception as e:
            raise TftpError(f"Failed to upload file: {str(e)}") from e

    @export
    def delete_file(self, filename: str):
        """Delete a file from the TFTP server.

        Args:
            filename (str): Name of the file to delete

        Returns:
            str: The filename that was deleted

        Raises:
            FileNotFound: If the specified file does not exist
            TftpError: If the deletion operation fails
        """
        file_path = os.path.join(self.root_dir, filename)

        if not os.path.exists(file_path):
            raise FileNotFound(f"File {filename} not found")

        try:
            os.remove(file_path)
            return filename
        except Exception as e:
            raise TftpError(f"Failed to delete {filename}") from e

    @export
    def check_file_checksum(self, filename: str, client_checksum: str) -> bool:
        """Check if a file matches the expected checksum.

        Args:
            filename (str): Name of the file to check
            client_checksum (str): Expected SHA256 checksum

        Returns:
            bool: True if the file exists and matches the checksum, False otherwise
        """
        file_path = os.path.join(self.root_dir, filename)
        self.logger.debug(f"checking checksum for file: {filename}")
        self.logger.debug(f"file path: {file_path}")

        if not os.path.exists(file_path):
            self.logger.debug(f"File {filename} does not exist")
            return False

        current_checksum = self._compute_checksum(file_path)
        self.logger.debug(f"Computed checksum: {current_checksum}")
        self.logger.debug(f"Client checksum: {client_checksum}")

        return current_checksum == client_checksum

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

    def _compute_checksum(self, path: str) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()
