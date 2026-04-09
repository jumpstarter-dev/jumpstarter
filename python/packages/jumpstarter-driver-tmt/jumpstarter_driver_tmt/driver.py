from dataclasses import dataclass

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class TMT(Driver):
    """ driver for Jumpstarter"""

    reboot_cmd: str = ""
    default_username: str = ""
    default_password: str = ""

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "ssh" not in self.children:
            raise ConfigurationError("'ssh' child is required via ref, or directly as a TcpNetwork driver instance")


    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_tmt.client.TMTClient"

    @export
    def get_reboot_cmd(self):
        return self.reboot_cmd

    @export
    def get_default_user_pass(self):
        return self.default_username, self.default_password

