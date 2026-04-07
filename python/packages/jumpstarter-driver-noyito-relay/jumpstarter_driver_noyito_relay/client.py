import click
from jumpstarter_driver_power.client import PowerClient


class NoyitoPowerClient(PowerClient):
    def status(self) -> str:
        """Query the configured relay channel state."""
        return self.call("status")

    def cli(self):
        base = super().cli()

        @base.command()
        def status():
            """Query relay channel state"""
            click.echo(self.status())

        return base
