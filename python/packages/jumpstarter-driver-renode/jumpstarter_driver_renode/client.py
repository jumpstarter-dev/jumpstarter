import click
from jumpstarter_driver_composite.client import CompositeClient


class RenodeClient(CompositeClient):
    """Client for interacting with a Renode composite driver."""

    @property
    def platform(self) -> str:
        """The Renode platform description path."""
        return self.call("get_platform")

    @property
    def uart(self) -> str:
        """The UART peripheral path in the Renode object model."""
        return self.call("get_uart")

    @property
    def machine_name(self) -> str:
        """The Renode machine name."""
        return self.call("get_machine_name")

    def monitor_cmd(self, command: str) -> str:
        """Send an arbitrary command to the Renode monitor."""
        return self.call("monitor_cmd", command)

    def cli(self):
        """Extend the composite CLI with a ``monitor`` subcommand."""
        base = super().cli()

        @base.command(name="monitor")
        @click.argument("command", nargs=-1, required=True)
        def monitor_command(command):
            """Send a command to the Renode monitor."""
            result = self.monitor_cmd(" ".join(command))
            if result.strip():
                click.echo(result.strip())

        return base
