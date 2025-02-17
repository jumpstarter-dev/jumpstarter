import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import anyio
from aiohttp import web
from anyio.streams.file import FileWriteStream

from jumpstarter.driver import Driver, export

# 4MiB
CHUNK_SIZE = 4 * 1024 * 1024

class HttpServerError(Exception):
    """Base exception for HTTP server errors"""


class FileWriteError(HttpServerError):
    """Exception raised when file writing fails"""


@dataclass(kw_only=True)
class HttpServer(Driver):
    """HTTP Server driver for Jumpstarter"""

    root_dir: str = "/var/www"
    host: str | None = field(default=None)
    port: int = 8080
    app: web.Application = field(init=False, default_factory=web.Application)
    runner: Optional[web.AppRunner] = field(init=False, default=None)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        os.makedirs(self.root_dir, exist_ok=True)
        self.app.router.add_routes(
            [
                web.get("/{filename}", self.get_file),
            ]
        )
        if self.host is None:
            self.host = self.get_default_ip()

    def get_default_ip(self):
        try:
            import socket

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            self.logger.warning("Could not determine default IP address, falling back to 0.0.0.0")
            return "0.0.0.0"

    @classmethod
    def client(cls) -> str:
        """Return the import path of the corresponding client"""
        return "jumpstarter_driver_http.client.HttpServerClient"

    @export
    async def put_file(self, filename: str, src_stream) -> str:
        """
        Upload a file to the HTTP server.

        Args:
            filename (str): Name of the file to upload.
            src_stream: Stream of file content.

        Returns:
            str: Name of the uploaded file.

        Raises:
            HttpServerError: If the target path is invalid.
            FileWriteError: If the file upload fails.
        """
        try:
            file_path = os.path.join(self.root_dir, filename)

            if not Path(file_path).resolve().is_relative_to(Path(self.root_dir).resolve()):
                raise HttpServerError("Invalid target path")

            async with await FileWriteStream.from_path(file_path) as dst:
                async with self.resource(src_stream) as src:
                    async for chunk in src:
                        await dst.send(chunk)

            self.logger.info(f"File '{filename}' written to '{file_path}'")
            return f"{self.get_url()}/{filename}"

        except Exception as e:
            self.logger.error(f"Failed to upload file '{filename}': {e}")
            raise FileWriteError(f"Failed to upload file '{filename}': {e}") from e

    @export
    async def delete_file(self, filename: str) -> str:
        """
        Delete a file from the HTTP server.

        Args:
            filename (str): Name of the file to delete.

        Returns:
            str: Name of the deleted file.

        Raises:
            HttpServerError: If the file does not exist or deletion fails.
        """
        file_path = Path(self.root_dir) / filename
        if not file_path.exists():
            raise HttpServerError(f"File '{filename}' does not exist.")
        try:
            file_path.unlink()
            self.logger.info(f"File '{filename}' has been deleted.")
            return filename
        except Exception as e:
            self.logger.error(f"Failed to delete file '{filename}': {e}")
            raise HttpServerError(f"Failed to delete file '{filename}': {e}") from e

    async def get_file(self, request) -> web.FileResponse:
        """
        Retrieve a file from the HTTP server.

        Args:
            request: aiohttp request object.

        Returns:
            web.FileResponse: HTTP response containing the requested file.

        Raises:
            web.HTTPNotFound: If the requested file does not exist.
        """
        filename = request.match_info["filename"]
        file_path = os.path.join(self.root_dir, filename)
        if not os.path.isfile(file_path):
            self.logger.warning(f"File not found: {file_path}")
            raise web.HTTPNotFound(text=f"File '{filename}' not found.")
        self.logger.info(f"Serving file: {file_path}")
        return web.FileResponse(file_path)

    @export
    def list_files(self) -> list[str]:
        """
        List all files in the root directory.

        Returns:
            list[str]: List of filenames in the root directory.

        Raises:
            HttpServerError: If listing files fails.
        """
        try:
            files = os.listdir(self.root_dir)
            files = [f for f in files if os.path.isfile(os.path.join(self.root_dir, f))]
            return files
        except Exception as e:
            self.logger.error(f"Failed to list files: {e}")
            raise HttpServerError(f"Failed to list files: {e}") from e

    @export
    async def start(self):
        """
        Start the HTTP server.

        Raises:
            HttpServerError: If the server fails to start.
        """
        if self.runner is not None:
            self.logger.warning("HTTP server is already running.")
            return

        self.runner = web.AppRunner(self.app)
        if self.runner:
            await self.runner.setup()

        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        self.logger.info(f"HTTP server started at http://{self.host}:{self.port}")

    @export
    async def stop(self):
        """
        Stop the HTTP server.

        Raises:
            HttpServerError: If the server fails to stop.
        """
        if self.runner is None:
            self.logger.warning("HTTP server is not running.")
            return

        await self.runner.cleanup()
        self.logger.info("HTTP server stopped.")
        self.runner = None

    @export
    def get_url(self) -> str:
        """
        Get the base URL of the HTTP server.

        Returns:
            str: Base URL of the HTTP server.
        """
        return f"http://{self.host}:{self.port}"

    @export
    def get_host(self) -> str | None:
        """
        Get the host IP address of the HTTP server.

        Returns:
            str: Host IP address.
        """
        return self.host

    @export
    def get_port(self) -> int:
        """
        Get the port number of the HTTP server.

        Returns:
            int: Port number.
        """
        return self.port

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

        if not os.path.exists(file_path):
            self.logger.debug(f"File {filename} does not exist")
            return False

        current_checksum = self._compute_checksum(file_path)
        self.logger.debug(f"Computed checksum: {current_checksum}")
        self.logger.debug(f"Client checksum: {client_checksum}")

        return current_checksum == client_checksum

    def _compute_checksum(self, path: str) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()

    def close(self):
        if self.runner:
            try:
                if anyio.get_current_task():
                    anyio.from_thread.run(self._async_cleanup)
            except Exception as e:
                self.logger.warning(f"HTTP server cleanup failed synchronously: {e}")
            self.runner = None
        super().close()

    async def _async_cleanup(self):
        try:
            if self.runner:
                await self.runner.shutdown()
                await self.runner.cleanup()
                self.logger.info("HTTP server cleanup completed asynchronously.")
        except Exception as e:
            self.logger.error(f"HTTP server cleanup failed asynchronously: {e}")
