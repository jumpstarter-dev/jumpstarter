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
    def session(self, *, encrypt: bool = True) -> typing.Iterator[str]:
        """Create a new VNC session."""
        with NovncAdapter(client=self.tcp, method="connect", encrypt=encrypt) as adapter:
            yield adapter

    def get_default_encrypt(self) -> bool:
        """Fetch the default encryption setting from the remote driver."""
        return typing.cast(bool, self.call("get_default_encrypt"))

    def cli(self) -> click.Command:
        """Return a click command handler for this driver."""

        @driver_click_group(self)
        def vnc():
            """Open a VNC session."""

        @vnc.command()
        @click.option("--browser/--no-browser", default=True, help="Open the session in a web browser.")
        @click.option(
            "--encrypt",
            "encrypt_override",
            flag_value=True,
            default=None,
            help="Force an encrypted connection (wss://). Overrides the driver default.",
        )
        @click.option(
            "--no-encrypt",
            "encrypt_override",
            flag_value=False,
            help="Force an unencrypted connection (ws://). Overrides the driver default.",
        )
        def session(browser: bool, encrypt_override: bool | None):
            """Open a VNC session."""
            encrypt = encrypt_override if encrypt_override is not None else self.get_default_encrypt()
            # The NovncAdapter is a blocking context manager that runs in a thread.
            # We can enter it, open the browser, and then just wait for the user
            # to press Ctrl+C to exit. The adapter handles the background work.
            with self.session(encrypt=encrypt) as url:
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
