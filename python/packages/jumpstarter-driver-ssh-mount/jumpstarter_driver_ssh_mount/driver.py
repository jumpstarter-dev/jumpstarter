from dataclasses import dataclass

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver


@dataclass(kw_only=True)
class SSHMount(Driver):
    """SSHFS mount/umount driver for Jumpstarter

    This driver provides remote filesystem mounting via sshfs.
    It requires an 'ssh' child driver (SSHWrapper) which provides
    SSH credentials and a 'tcp' sub-child for network connectivity.
    """

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
