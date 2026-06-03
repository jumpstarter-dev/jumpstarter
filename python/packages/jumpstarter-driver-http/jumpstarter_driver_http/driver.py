import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional

from aiohttp import web
from jumpstarter_driver_opendal.driver import Opendal

from jumpstarter.common.ipaddr import get_ip_address
from jumpstarter.driver import Driver, export


class HttpServerError(Exception):
    """Base exception for HTTP server errors"""



@dataclass(kw_only=True)
class HttpServer(Driver):
    """HTTP Server driver for Jumpstarter"""

    root_dir: str = "/var/www"
    host: str | None = field(default=None)
    port: int = 0
    timeout: int = field(default=600)
    remove_created_on_close: bool = True  # Clean up temporary web files by default
    app: web.Application = field(init=False, default_factory=web.Application)
    runner: Optional[web.AppRunner] = field(init=False, default=None)
    _bound_port: int = field(init=False, default=0)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        os.makedirs(self.root_dir, exist_ok=True)

        self.children["storage"] = Opendal(
            scheme="fs",
            kwargs={"root": self.root_dir},
            remove_created_on_close=self.remove_created_on_close
        )
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
        # Defense in depth: clean up any stale runner before starting
        if self.runner is not None:
            self.logger.warning("Cleaning up stale HTTP server runner before starting.")
            try:
                await self.runner.cleanup()
            except Exception as e:
                self.logger.warning(f"Failed to clean up stale runner: {e}")
            self.runner = None
            self._bound_port = 0

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()

        # Retrieve the actual bound port (important when port=0)
        sockets = site._server.sockets
        if sockets:
            self._bound_port = int(sockets[0].getsockname()[1])
        else:
            self._bound_port = self.port

        self.logger.info(f"HTTP server started at http://{self.host}:{self._bound_port}")

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
        self._bound_port = 0

    @export
    def get_url(self) -> str:
        """
        Get the base URL of the HTTP server.

        Returns:
            str: Base URL of the HTTP server.
        """
        return f"http://{self.host}:{self._bound_port}"

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
        return int(self._bound_port)

    def close(self):
        if self.runner:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We are on the event loop thread (e.g., called from
                    # Session.__contextmanager__'s finally block). Schedule the
                    # cleanup coroutine on the running loop and wait for it.
                    future = asyncio.ensure_future(self._async_cleanup(), loop=loop)
                    # ensure_future schedules it; we cannot block-wait here
                    # because we are on the loop thread. Add a callback to log
                    # errors instead of silently dropping them.
                    future.add_done_callback(self._cleanup_done_callback)
                else:
                    # No event loop running — we can drive one ourselves.
                    loop.run_until_complete(self._async_cleanup())
            except RuntimeError:
                # No event loop at all — create a temporary one.
                asyncio.run(self._async_cleanup())
            except Exception as e:
                self.logger.warning(f"HTTP server cleanup failed: {e}")
            finally:
                self.runner = None
                self._bound_port = 0
        super().close()

    def _cleanup_done_callback(self, future: asyncio.Future):
        """Log errors from the async cleanup when it was scheduled as a task."""
        try:
            future.result()
        except Exception as e:
            self.logger.error(f"HTTP server async cleanup failed: {e}")

    async def _async_cleanup(self):
        try:
            if self.runner:
                await self.runner.shutdown()
                await self.runner.cleanup()
                self.logger.info("HTTP server cleanup completed.")
        except Exception as e:
            self.logger.error(f"HTTP server cleanup failed: {e}")
            raise
