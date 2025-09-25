from dataclasses import dataclass

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class SSHWrapper(Driver):
    """SSH wrapper driver for Jumpstarter that provides SSH CLI functionality"""

    default_username: str = ""
    ssh_command: str = "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "tcp" not in self.children:
            raise ConfigurationError("'tcp' child is required via ref, or directly as a TcpNetwork driver instance")

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
