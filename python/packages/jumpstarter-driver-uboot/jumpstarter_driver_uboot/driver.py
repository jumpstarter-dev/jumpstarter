from dataclasses import dataclass

from .common import DEFAULT_PROMPT_TIMEOUT
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class UbootConsole(Driver):
    driver_type = "console"

    prompt: str = "\n=>"
    # seconds the target may take from a power cycle to the U-Boot prompt
    prompt_timeout: float = DEFAULT_PROMPT_TIMEOUT

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_uboot.client.UbootConsoleClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        for child in ("power", "serial"):
            if child not in self.children:
                raise ValueError(f"UbootConsole: {child} driver not configured as a child")

    @export
    def get_prompt(self) -> str:
        return self.prompt

    @export
    def get_prompt_timeout(self) -> float:
        return self.prompt_timeout
