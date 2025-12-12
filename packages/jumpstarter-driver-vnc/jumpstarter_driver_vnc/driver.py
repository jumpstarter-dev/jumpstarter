from __future__ import annotations

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver


class Vnc(Driver):
    """A driver for VNC."""

    def __post_init__(self):
        """
        Validate the VNC driver's post-initialization configuration.
        
        Ensures the driver has a "tcp" child configured.
        
        Raises:
            ConfigurationError: If a "tcp" child is not present.
        """
        super().__post_init__()
        if "tcp" not in self.children:
            raise ConfigurationError("A tcp child is required for Vnc")

    @classmethod
    def client(cls) -> str:
        """
        Client class path for this driver.
        
        Returns:
            str: Dotted import path of the client class, e.g. "jumpstarter_driver_vnc.client.VNClient".
        """
        return "jumpstarter_driver_vnc.client.VNClient"