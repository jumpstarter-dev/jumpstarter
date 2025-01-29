import asyncio
import os
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from anyio.streams.file import FileWriteStream

from jumpstarter_driver_tftp.server import TftpServer

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
    """TFTP Server driver for Jumpstarter"""

    root_dir: str = "/var/lib/tftpboot"
    host: str = field(default=None)
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
        if self.host is None:
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
            # Signal that the loop is ready
            self._loop_ready.set()

            # Run the server until shutdown is requested
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
        if self.server_thread is not None and self.server_thread.is_alive():
            self.logger.warning("TFTP server is already running")
            return

        # Clear any previous shutdown state
        self._shutdown_event.clear()
        self._loop_ready.clear()

        # Start the server thread
        self.server_thread = threading.Thread(target=self._start_server, daemon=True)
        self.server_thread.start()

        if not self._loop_ready.wait(timeout=5.0):
            self.logger.error("Timeout waiting for event loop to be ready")
            self.server_thread = None
            raise TftpError("Failed to start TFTP server - event loop initialization timeout")

        self.logger.info(f"TFTP server started on {self.host}:{self.port}")

    @export
    def stop(self):
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
        return os.listdir(self.root_dir)

    @export
    async def put_file(self, filename: str, src_stream):
        """Handle file upload using streaming"""
        try:
            file_path = os.path.join(self.root_dir, filename)

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
        try:
            os.remove(os.path.join(self.root_dir, filename))
        except FileNotFoundError as err:
            raise FileNotFound(f"File {filename} not found") from err
        except Exception as e:
            raise TftpError(f"Failed to delete {filename}") from e

    @export
    def get_host(self) -> str:
        return self.host

    @export
    def get_port(self) -> int:
        return self.port

    def close(self):
        if self.server_thread is not None:
            self.stop()
        super().close()
