import os
from dataclasses import dataclass, field
from typing import Optional

import anyio
import anyio.from_thread
from aiohttp import web
from jumpstarter_driver_opendal.driver import Opendal

from jumpstarter.common.ipaddr import get_ip_address
from jumpstarter.driver import Driver, export


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
    timeout: int = field(default=600)
    app: web.Application = field(init=False, default_factory=web.Application)
    runner: Optional[web.AppRunner] = field(init=False, default=None)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        os.makedirs(self.root_dir, exist_ok=True)

        self.children["storage"] = Opendal(scheme="fs", kwargs={"root": self.root_dir})
        self.app.router.add_routes(
            [
                web.static("/", self.root_dir),
            ]
        )
        if self.host is None:
            self.host = get_ip_address(logger=self.logger)

    @classmethod
    def client(cls) -> str:
        """Return the import path of the corresponding client"""
        return "jumpstarter_driver_http.client.HttpServerClient"

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
