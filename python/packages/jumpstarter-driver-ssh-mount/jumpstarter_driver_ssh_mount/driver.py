from dataclasses import dataclass

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver


@dataclass(kw_only=True)
class SSHMount(Driver):
    """SSHFS mount driver. Requires an 'ssh' child (SSHWrapper) with a 'tcp' sub-child."""

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "ssh" not in self.children:
            raise ConfigurationError(
                "'ssh' child is required via ref to an SSHWrapper driver instance"
            )

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ssh_mount.client.SSHMountClient"
