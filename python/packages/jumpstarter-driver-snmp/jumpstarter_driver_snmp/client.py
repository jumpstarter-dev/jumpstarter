from dataclasses import dataclass

import click
from jumpstarter_driver_power.client import PowerClient


@dataclass(kw_only=True)
class SNMPServerClient(PowerClient):
    """Client interface for SNMP Power Control"""

    def on(self):
        """Turn power on"""
        self.call("on")

    def off(self):
        """Turn power off"""
        self.call("off")

    def cli(self):
        @click.group()
        def snmp():
            """SNMP power control commands"""
            pass

        for cmd in super().cli().commands.values():
            snmp.add_command(cmd)

        return snmp
