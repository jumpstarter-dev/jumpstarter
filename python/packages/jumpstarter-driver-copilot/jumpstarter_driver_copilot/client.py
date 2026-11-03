from dataclasses import dataclass

import click
from jumpstarter_driver_power.client import PowerClient

from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group


@dataclass(kw_only=True)
class CoPilotClient(DriverClient):
    def info(self):
        return self.call("info")

    def reconnect(self):
        return self.call("reconnect")

    def set_line(self, line: int, active: bool):
        return self.call("set_line", line, active)

    def read_line(self, line: int):
        return self.call("read_line", line)

    def pulse(self, line: int, duration: float = 1.0):
        return self.call("pulse", line, duration)

    def set_relay(self, channel: int, active: bool):
        return self.call("set_relay", channel, active)

    def read_relay(self, channel: int):
        return self.call("read_relay", channel)

    def pulse_relay(self, channel: int, duration: float = 1.0):
        return self.call("pulse_relay", channel, duration)

    def cli(self):
        @driver_click_group(self)
        def base():
            """Control a BayLibre CoPilot and its HATs."""

        @base.command("info")
        def info_command():
            click.echo(self.info())

        @base.command("reconnect")
        def reconnect_command():
            """Release all relays, reopen the CoPilot and acknowledge an interrupted action."""
            click.echo(self.reconnect())

        @base.command("set")
        @click.argument("line", type=int)
        @click.argument("state", type=click.Choice(["on", "off"]))
        def set_command(line, state):
            self.set_line(line, state == "on")

        @base.command("pulse")
        @click.argument("line", type=int)
        @click.option("--duration", type=float, default=1.0, show_default=True)
        def pulse_command(line, duration):
            self.pulse(line, duration)

        @base.command("relay")
        @click.argument("channel", type=click.IntRange(1, 3))
        @click.argument("state", type=click.Choice(["on", "off"]))
        def relay_command(channel, state):
            self.set_relay(channel, state == "on")

        @base.command("read-relay")
        @click.argument("channel", type=click.IntRange(1, 3))
        def read_relay_command(channel):
            click.echo("active" if self.read_relay(channel) else "inactive")

        @base.command("pulse-relay")
        @click.argument("channel", type=click.IntRange(1, 3))
        @click.option("--duration", type=float, default=1.0, show_default=True)
        def pulse_relay_command(channel, duration):
            self.pulse_relay(channel, duration)

        return base


@dataclass(kw_only=True)
class CoPilotUSBClient(DriverClient):
    """Switch the power of the triple-USB HAT channels, numbered from 1."""

    def ports(self) -> int:
        return int(self.call("ports"))

    def on(self, port: int):
        self.call("on", port)

    def off(self, port: int):
        self.call("off", port)

    def read(self, port: int) -> bool:
        return self.call("read", port)

    def cycle(self, port: int, wait: float = 2.0):
        """Switch the port off, wait, then on; runs on the exporter."""
        self.call("cycle", port, wait)

    def cli(self):
        @driver_click_group(self)
        @click.argument("port", type=click.IntRange(min=1))
        def base(port):
            """Control the CoPilot USB HAT ports: j usb PORT on|off|read|cycle."""

        def selected_port() -> int:
            group_context = click.get_current_context().parent
            if group_context is None:
                raise click.UsageError("missing USB port")
            return group_context.params["port"]

        @base.command()
        def on():
            """Enable the port."""
            self.on(selected_port())

        @base.command()
        def off():
            """Disable the port."""
            self.off(selected_port())

        @base.command()
        def read():
            """Read the commanded port state."""
            click.echo("on" if self.read(selected_port()) else "off")

        @base.command()
        @click.option("--wait", type=float, default=2.0, show_default=True, help="Seconds off, from 1 to 30.")
        def cycle(wait):
            """Switch the port off, wait, then on again."""
            self.cycle(selected_port(), wait)

        return base


class CoPilotPowerClient(PowerClient):
    def state(self):
        return self.call("state")

    def cli(self):
        base = super().cli()

        @base.command("state")
        def state():
            """Read the switch command, not a voltage/current measurement."""
            click.echo(self.state())

        return base
