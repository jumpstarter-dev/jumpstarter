from dataclasses import dataclass
from pathlib import Path

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class SSHWrapper(Driver):
    """SSH wrapper driver for Jumpstarter that provides SSH CLI functionality"""

    default_username: str = ""
    ssh_command: str = "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
    ssh_identity: str | None = None
    ssh_identity_file: str | None = None

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "tcp" not in self.children:
            raise ConfigurationError("'tcp' child is required via ref, or directly as a TcpNetwork driver instance")

        if self.ssh_identity and self.ssh_identity_file:
            raise ConfigurationError("Cannot specify both ssh_identity and ssh_identity_file")

        # If ssh_identity_file is provided, read it into ssh_identity
        if self.ssh_identity_file:
            try:
                self.ssh_identity = Path(self.ssh_identity_file).read_text()
            except Exception as e:
                raise ConfigurationError(f"Failed to read ssh_identity_file '{self.ssh_identity_file}': {e}") from None

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ssh.client.SSHWrapperClient"

    @export
    def get_default_username(self):
        """Get default SSH username"""
        return self.default_username

    @export
    def get_ssh_command(self):
        """Get the SSH command to use"""
        return self.ssh_command

    @export
    def get_ssh_identity(self):
        """Get the SSH identity key content"""
        return self.ssh_identity
