from __future__ import annotations

from dataclasses import dataclass

from jumpstarter_driver_composite.driver import Composite

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import export


@dataclass
class Vnc(Composite):
    """A VNC driver.

    Members:
        default_encrypt: Whether to default to an encrypted client connection.
    """

    default_encrypt: bool = False

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

    @export
    async def get_default_encrypt(self) -> bool:
        """Return the default encryption setting."""
        return self.default_encrypt

    @classmethod
    def client(cls) -> str:
        """
        Client class path for this driver.

        Returns:
            str: Dotted import path of the client class.
        """
        return "jumpstarter_driver_vnc.client.VNClient"
