from dataclasses import dataclass
from pathlib import Path

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class SSHMount(Driver):
    """SSHFS mount/umount driver for Jumpstarter

    This driver provides remote filesystem mounting via sshfs.
    It requires a 'tcp' child driver for network connectivity to the SSH server.
    """

    default_username: str = ""
    ssh_identity: str | None = None
    ssh_identity_file: str | None = None

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "tcp" not in self.children:
            raise ConfigurationError("'tcp' child is required via ref, or directly as a TcpNetwork driver instance")

        if self.ssh_identity and self.ssh_identity_file:
            raise ConfigurationError("Cannot specify both ssh_identity and ssh_identity_file")

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ssh_mount.client.SSHMountClient"

    @export
    def get_default_username(self):
        """Get default SSH username"""
        return self.default_username

    @export
    def get_ssh_identity(self):
        """Get the SSH identity key content"""
        if self.ssh_identity is None and self.ssh_identity_file:
            try:
                self.ssh_identity = Path(self.ssh_identity_file).read_text()
            except Exception as e:
                raise ConfigurationError(f"Failed to read ssh_identity_file '{self.ssh_identity_file}': {e}") from None
        return self.ssh_identity
