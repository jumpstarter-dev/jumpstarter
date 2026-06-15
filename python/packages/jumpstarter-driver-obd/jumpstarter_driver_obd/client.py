from dataclasses import dataclass
from typing import Optional

from .driver import OBDConnectionStatus
from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class OBDClient(DriverClient):
    """Client for the OBD-II driver."""

    def query(self, command_name: str) -> Optional[str]:
        """Query a PID by name (e.g. 'RPM'); returns None if the ECU doesn't answer."""
        return self.call("query", command_name)

    def clear_dtc(self) -> None:
        """Clear stored DTCs and freeze-frame data (mode 04).

        Destructive: also resets readiness monitors.
        """
        return self.call("clear_dtc")

    def status(self) -> OBDConnectionStatus:
        """Connection state."""
        return OBDConnectionStatus(self.call("status"))

    def supported_commands(self) -> list[str]:
        """Sorted PID names the connected ECU advertises."""
        return self.call("supported_commands")

    def is_connected(self) -> bool:
        """True when a vehicle ECU is on the bus, not just the adapter."""
        return self.call("is_connected")
