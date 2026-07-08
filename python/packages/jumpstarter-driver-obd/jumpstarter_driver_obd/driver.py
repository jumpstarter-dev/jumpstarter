from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

import obd

from jumpstarter.client.core import DriverInvalidArgument
from jumpstarter.driver import Driver, export


class OBDConnectionStatus(StrEnum):
    NOT_CONNECTED = "Not Connected"
    ELM_CONNECTED = "ELM Connected"
    OBD_CONNECTED = "OBD Connected"
    CAR_CONNECTED = "Car Connected"

# CLEAR_DTC (mode 04) erases stored codes and resets readiness monitors, so it
# is reachable only through clear_dtc(), never the generic query().
DESTRUCTIVE_COMMANDS = frozenset({"CLEAR_DTC"})


@dataclass(kw_only=True)
class OBD(Driver):
    """OBD-II vehicle diagnostics via an ELM327 adapter, wrapping python-obd.

    ``port=None`` (the default) auto-detects the adapter; otherwise pass the
    serial path, e.g. ``/dev/ttyUSB0``.
    """

    driver_type = "automotive"

    port: Optional[str] = field(default=None)
    baudrate: int = field(default=38400)
    # fast mode is quicker but flaky on cheap clone adapters, so it defaults off
    fast: bool = field(default=False)

    _connection: Optional[obd.OBD] = field(init=False, default=None)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_obd.client.OBDClient"

    def __post_init__(self):
        super().__post_init__()
        conn = obd.OBD(portstr=self.port, baudrate=self.baudrate, fast=self.fast)
        if conn.status() == obd.OBDStatus.NOT_CONNECTED:
            conn.close()
            raise ConnectionError(f"No ELM327 adapter found on port {self.port!r}")
        self._connection = conn

    def close(self):
        if self._connection is not None:
            self._connection.close()
        super().close()

    @export
    def query(self, command_name: str) -> Optional[str]:
        """Query a PID by name (e.g. 'RPM', 'SPEED', 'COOLANT_TEMP').

        Returns None if the ECU doesn't answer. Destructive commands such as
        CLEAR_DTC are rejected here; use clear_dtc() instead.
        """
        if command_name in DESTRUCTIVE_COMMANDS:
            raise DriverInvalidArgument(
                f"{command_name} is destructive (clears stored DTCs and resets "
                f"readiness monitors) and cannot be sent via query(); use clear_dtc()"
            )
        if not obd.commands.has_name(command_name):
            raise DriverInvalidArgument(f"Unknown OBD command: {command_name}")
        cmd = obd.commands[command_name]
        response = self._connection.query(cmd)
        if response.is_null():
            return None
        return self._serialize(response.value)

    @export
    def clear_dtc(self) -> None:
        """Clear stored DTCs and freeze-frame data (mode 04).

        Destructive: also resets readiness monitors, which then need drive
        cycles to re-complete. Use only as a deliberate reset.
        """
        # obd.commands is populated dynamically; index it by name
        self._connection.query(obd.commands["CLEAR_DTC"], force=True)

    @staticmethod
    def _serialize(value) -> str:
        """Stringify a python-obd value.

        Most values stringify fine, but VIN/CALIBRATION_ID are bytearrays and
        STATUS is an object with no __str__ (and a None key in __dict__). Handle
        those so callers never get raw bytes or a '<object at 0x...>' address.
        """
        if isinstance(value, (bytes, bytearray)):
            return value.decode("ascii", errors="replace")
        if type(value).__str__ is object.__str__:
            try:
                fields = {k: v for k, v in vars(value).items() if isinstance(k, str) and not k.startswith("_")}
            except TypeError:
                fields = None
            if fields:
                return ", ".join(f"{k}={v}" for k, v in fields.items())
        return str(value)

    @export
    def status(self) -> OBDConnectionStatus:
        """Connection state."""
        return OBDConnectionStatus(str(self._connection.status()))

    @export
    def supported_commands(self) -> list[str]:
        """Sorted PID names the connected ECU advertises."""
        return sorted(cmd.name for cmd in self._connection.supported_commands)

    @export
    def is_connected(self) -> bool:
        """True when a vehicle ECU is on the bus, not just the adapter."""
        return self._connection.is_connected()
