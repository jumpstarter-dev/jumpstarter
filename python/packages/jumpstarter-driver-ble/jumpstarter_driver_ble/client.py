from contextlib import contextmanager

import click
from jumpstarter_driver_network.adapters import PexpectAdapter
from pexpect.fdpexpect import fdspawn

from .console import BleConsole
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group


class BleWriteNotifyStreamClient(DriverClient):
    """
    Client interface for Bluetooth Low Energy (BLE) WriteNotifyStream driver.

    This client allows to communication with BLE devices, by leveraging a:
     - write characteristic for sending data
     - notify characteristic for receiving data
    """

    def info(self) -> str:
        """Get BLE information about the target"""
        return self.call("info")

    def open(self) -> fdspawn:
        """
        Open a pexpect session. You can find the pexpect documentation
        here: https://pexpect.readthedocs.io/en/stable/api/pexpect.html#spawn-class

        Returns:
            fdspawn: The pexpect session object.
        """
        return self.stack.enter_context(self.pexpect())

    @contextmanager
    def pexpect(self):
        """
        Create a pexpect adapter context manager.

        Yields:
            PexpectAdapter: The pexpect adapter object.
        """
        with PexpectAdapter(client=self) as adapter:
            yield adapter

    def cli(self):  # noqa: C901
        @driver_click_group(self)
        def base():
            """ble client"""
            pass

        @base.command()
        def info():
            """Get target information"""
            print(self.info())

        @base.command()
        def start_console():
            """Start BLE console"""
            click.echo(
                "\nStarting ble console ... exit with CTRL+B x 3 times\n")
            console = BleConsole(ble_client=self)
            console.run()

        return base
