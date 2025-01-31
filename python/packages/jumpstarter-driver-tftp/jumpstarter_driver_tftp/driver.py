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
    checksum_suffix: str = ".sha256"
    server: Optional["TftpServer"] = field(init=False, default=None)
    server_thread: Optional[threading.Thread] = field(init=False, default=None)
    _shutdown_event: threading.Event = field(init=False, default_factory=threading.Event)
    _loop_ready: threading.Event = field(init=False, default_factory=threading.Event)
    _loop: Optional[asyncio.AbstractEventLoop] = field(init=False, default=None)
    _checksums: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
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
    async def put_file(self, filename: str, src_stream, client_checksum: str):
        """Only called when we know we need to upload"""
        file_path = os.path.join(self.root_dir, filename)

        try:
            if not Path(file_path).resolve().is_relative_to(Path(self.root_dir).resolve()):
                raise TftpError("Invalid target path")

            async with await FileWriteStream.from_path(file_path) as dst:
                async with self.resource(src_stream) as src:
                    async for chunk in src:
                        await dst.send(chunk)

            self._checksums[filename] = client_checksum
            self._write_checksum_file(filename, client_checksum)
            return filename
        except Exception as e:
            raise TftpError(f"Failed to upload file: {str(e)}") from e


    @export
    def delete_file(self, filename: str):
        """Delete file and its checksum file"""
        file_path = os.path.join(self.root_dir, filename)
        checksum_path = self._get_checksum_path(filename)

        if not os.path.exists(file_path):
            raise FileNotFound(f"File {filename} not found")

        try:
            os.remove(file_path)
            if os.path.exists(checksum_path):
                os.remove(checksum_path)
            self._checksums.pop(filename, None)
        except Exception as e:
            raise TftpError(f"Failed to delete {filename}") from e

    @export
    def check_file_checksum(self, filename: str, client_checksum: str) -> bool:
        """Check if file exists with matching checksum"""
        file_path = os.path.join(self.root_dir, filename)
        if not os.path.exists(file_path):
            return False

        current_checksum = self._compute_checksum(file_path)
        stored_checksum = self._read_checksum_file(filename)

        if stored_checksum != current_checksum:
            self._write_checksum_file(filename, current_checksum)
            self._checksums[filename] = current_checksum

        logger.debug(f"Client checksum: {client_checksum}, server checksum: {current_checksum}")
        return current_checksum == client_checksum

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

    def _get_checksum_path(self, filename: str) -> str:
        return os.path.join(self.root_dir, f"{filename}{self.checksum_suffix}")

    def _read_checksum_file(self, filename: str) -> Optional[str]:
        try:
            checksum_path = self._get_checksum_path(filename)
            if os.path.exists(checksum_path):
                with open(checksum_path, 'r') as f:
                    return f.read().strip()
        except Exception as e:
            logger.warning(f"Failed to read checksum file for {filename}: {e}")
        return None

    def _write_checksum_file(self, filename: str, checksum: str):
        """Write checksum to the checksum file"""
        try:
            checksum_path = self._get_checksum_path(filename)
            with open(checksum_path, 'w') as f:
                f.write(f"{checksum}\n")
        except Exception as e:
            logger.error(f"Failed to write checksum file for {filename}: {e}")

    def _compute_checksum(self, path: str) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _initialize_checksums(self):
        self._checksums.clear()
        for filename in os.listdir(self.root_dir):
            if filename.endswith(self.checksum_suffix):
                continue
            file_path = os.path.join(self.root_dir, filename)
            if os.path.isfile(file_path):
                stored_checksum = self._read_checksum_file(filename)
                current_checksum = self._compute_checksum(file_path)
                if stored_checksum != current_checksum:
                    self._write_checksum_file(filename, current_checksum)
                self._checksums[filename] = current_checksum
