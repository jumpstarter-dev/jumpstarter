from __future__ import annotations

import asyncio
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
        """
        Access the underlying TCP client.
        
        Returns:
            TCPClient: The TCP client instance stored in this composite client's children mapping.
        """
        return typing.cast("TCPClient", self.children["tcp"])

    @contextlib.contextmanager
    def session(self, *, encrypt: bool = False) -> typing.Iterator[str]:
        """
        Open a noVNC session and yield the connection URL.
        
        Parameters:
            encrypt (bool): If True, request an encrypted WebSocket (use `wss://`); otherwise use `ws://`.
        
        Returns:
            url (str): The URL to connect to the VNC session.
        """
        with NovncAdapter(client=self.tcp, method="connect", encrypt=encrypt) as adapter:
            yield adapter

    def cli(self) -> click.Command:
        """
        Provide a Click command group for running VNC sessions.
        
        The returned command exposes a `session` subcommand that opens a VNC session, prints the connection URL, optionally opens it in the user's browser, and waits until the user cancels the session.
        
        Returns:
            click.Command: Click command group with a `session` subcommand that accepts
            `--browser/--no-browser` and `--encrypt/--no-encrypt` options.
        """

        @driver_click_group(self)
        def vnc():
            """
            Open a VNC session and block until the user closes it.
            
            When invoked, prints the connection URL for the noVNC session, optionally opens that URL in the user's web browser, and waits for user-initiated termination (for example, Ctrl+C). On exit, prints a message indicating the session is closing.
            
            Parameters:
                browser (bool): If True, open the session URL in the default web browser.
                encrypt (bool): If True, request an encrypted (wss://) connection.
            """

        @vnc.command()
        @click.option("--browser/--no-browser", default=True, help="Open the session in a web browser.")
        @click.option(
            "--encrypt/--no-encrypt",
            default=False,
            help="Use an encrypted connection (wss://).",
        )
        def session(browser: bool, encrypt: bool):
            """
            Open an interactive VNC session and wait for the user to terminate it.
            
            Starts a VNC session using the client's session context, prints the connection URL, optionally opens that URL in a web browser, and blocks until the user cancels (e.g., Ctrl+C), then closes the session.
            
            Parameters:
                browser (bool): If True, open the session URL in the default web browser.
                encrypt (bool): If True, request an encrypted (wss://) connection for the session.
            """
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
                    self.portal.call(asyncio.Event().wait)
                except (KeyboardInterrupt, anyio.get_cancelled_exc_class()):
                    click.echo("\nClosing VNC session.")

        return vnc