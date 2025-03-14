from dataclasses import dataclass

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class UbootConsole(Driver):
    prompt: str = "=>"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_uboot.client.UbootConsoleClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        for child in ("power", "serial"):
            if child not in self.children:
                raise ValueError("UbootConsole: {} driver not configured as a child".format(child))

    @export
    def get_prompt(self) -> str:
        return self.prompt
