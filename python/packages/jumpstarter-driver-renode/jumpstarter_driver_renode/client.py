import click
from jumpstarter_driver_composite.client import CompositeClient


class RenodeClient(CompositeClient):
    @property
    def platform(self) -> str:
        return self.call("get_platform")

    @property
    def uart(self) -> str:
        return self.call("get_uart")

    @property
    def machine_name(self) -> str:
        return self.call("get_machine_name")

    def monitor_cmd(self, command: str) -> str:
        """Send an arbitrary command to the Renode monitor."""
        return self.call("monitor_cmd", command)

    def cli(self):
        base = super().cli()

        @base.command(name="monitor")
        @click.argument("command")
        def monitor_command(command):
            """Send a command to the Renode monitor."""
            result = self.monitor_cmd(command)
            if result.strip():
                click.echo(result.strip())

        return base
