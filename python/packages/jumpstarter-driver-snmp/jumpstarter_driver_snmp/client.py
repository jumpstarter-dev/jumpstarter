from dataclasses import dataclass

from jumpstarter_driver_power.client import PowerClient

from jumpstarter.client.decorators import driver_click_group


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
        @driver_click_group(self)
        def snmp():
            """SNMP power control commands"""
            pass

        for cmd in super().cli().commands.values():
            snmp.add_command(cmd)

        return snmp
