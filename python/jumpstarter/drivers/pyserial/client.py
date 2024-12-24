import click

from jumpstarter.client import DriverClient

from .console import Console


class PySerialClient(DriverClient):
    pass

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
