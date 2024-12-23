from contextlib import contextmanager

import click
from pexpect.fdpexpect import fdspawn

from jumpstarter.client import DriverClient
from jumpstarter.client.adapters import PexpectAdapter

from .console import Console


class PySerialClient(DriverClient):

    """
    A client for handling serial communication using pexpect.

    """

    def open(self) -> fdspawn:
        """
        Open a pexpect session. You can find the pexpect documentation
        here: https://pexpect.readthedocs.io/en/stable/api/pexpect.html#spawn-class

        Returns:
            fdspawn: The pexpect session object.
        """
        self._context_manager = self.pexpect()
        return self._context_manager.__enter__()

    @contextmanager
    def pexpect(self):
        """
        Create a pexpect adapter context manager.

        Yields:
            PexpectAdapter: The pexpect adapter object.
        """
        with PexpectAdapter(client=self) as adapter:
            yield adapter

    def cli(self):
        @click.group
        def base():
            """Serial port client"""
            pass

        @base.command()
        def start_console():
            """Start serial port console"""
            click.echo("\nStarting serial port console ... exit with CTRL+B x 3 times\n")
            console = Console(serial_client=self)
            console.run()

        return base
