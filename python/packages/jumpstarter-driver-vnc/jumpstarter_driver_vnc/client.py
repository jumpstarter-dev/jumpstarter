from __future__ import annotations

import contextlib
import typing
import webbrowser

import anyio
import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters.novnc import NovncAdapter

from jumpstarter.client.decorators import driver_click_group

if typing.TYPE_CHECKING:
    from jumpstarter_driver_network.client import TCPClient


class VNClient(CompositeClient):
    """Client for interacting with a VNC server."""

    @property
    def tcp(self) -> TCPClient:
        """Get the TCP client."""
        return typing.cast("TCPClient", self.children["tcp"])

    def stream(self, method="connect"):
        """Create a new stream, proxied to the underlying TCP driver."""
        return self.tcp.stream(method)

    async def stream_async(self, method="connect"):
        """Create a new async stream, proxied to the underlying TCP driver."""
        return await self.tcp.stream_async(method)

    @contextlib.contextmanager
    def session(self) -> typing.Iterator[str]:
        """Create a new VNC session."""
        with NovncAdapter(client=self.tcp, method="connect") as adapter:
            yield adapter

    def cli(self) -> click.Command:
        """Return a click command handler for this driver."""

        @driver_click_group(self)
        def vnc():
            """Open a VNC session."""

        @vnc.command()
        @click.option("--browser/--no-browser", default=True, help="Open the session in a web browser.")
        def session(browser: bool):
            """Open a VNC session."""
            # The NovncAdapter is a blocking context manager that runs in a thread.
            # We can enter it, open the browser, and then just wait for the user
            # to press Ctrl+C to exit. The adapter handles the background work.
            with self.session() as url:
                click.echo(f"To connect, please visit: {url}")
                if browser:
                    webbrowser.open(url)
                click.echo("Press Ctrl+C to close the VNC session.")
                try:
                    # Use the client's own portal to wait for cancellation.
                    self.portal.call(anyio.sleep_forever)
                except (KeyboardInterrupt, anyio.get_cancelled_exc_class()):
                    click.echo("\nClosing VNC session.")

        return vnc
