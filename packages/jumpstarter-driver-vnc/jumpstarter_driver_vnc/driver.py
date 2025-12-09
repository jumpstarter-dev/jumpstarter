from __future__ import annotations

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver


class Vnc(Driver):
    """A driver for VNC."""

    def __post_init__(self):
        """Initialize the VNC driver."""
        super().__post_init__()
        if "tcp" not in self.children:
            raise ConfigurationError("A tcp child is required for Vnc")

    @classmethod
    def client(cls) -> str:
        """Return the client class path for this driver."""
        return "jumpstarter_driver_vnc.client.VNClient"
