from dataclasses import dataclass
from subprocess import run
from .base import Power, PowerReading


@dataclass(kw_only=True)
class DutlinkPower(Power):
    name: str

    def on(self):
        p = run(
            ["jumpstarter", "power", "on", self.name], capture_output=True, check=True
        )
        return str(p.stdout)

    def off(self):
        p = run(
            ["jumpstarter", "power", "off", self.name], capture_output=True, check=True
        )
        return str(p.stdout)

    def read(self) -> PowerReading:
        return PowerReading(0.0, 0.0)
