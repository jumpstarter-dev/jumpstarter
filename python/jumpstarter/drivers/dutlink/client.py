from jumpstarter.drivers import DriverClient
from jumpstarter.drivers.composite import CompositeClient


class DutlinkPowerClient(DriverClient):
    def on(self):
        return self.call("on")

    def off(self):
        return self.call("off")


class DutlinkClient(CompositeClient):
    pass
