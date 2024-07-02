from .base import Power, PowerReading
from ..base import drivercall


class MockPower(Power):
    @drivercall
    def on(self):
        return True

    @drivercall
    def off(self):
        return True

    @drivercall
    def read(self) -> PowerReading:
        return PowerReading(5.0, 2.0)
