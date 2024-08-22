from dataclasses import dataclass

from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class DigitalOutputClient(DriverClient):
    def off(self):
        self.call("off")

    def on(self):
        self.call("on")


@dataclass(kw_only=True)
class DigitalInputClient(DriverClient):
    def wait_for_active(self, timeout: float | None = None):
        self.call("wait_for_active", timeout)

    def wait_for_inactive(self, timeout: float | None = None):
        self.call("wait_for_inactive", timeout)
