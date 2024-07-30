from jumpstarter.drivers import DriverClient
from jumpstarter.drivers.composite import CompositeClient
from jumpstarter.drivers.mixins import ResourceMixin


class DutlinkPowerClient(DriverClient):
    def on(self):
        return self.call("on")

    def off(self):
        return self.call("off")


class DutlinkStorageMuxClient(DriverClient, ResourceMixin):
    def host(self):
        return self.call("host")

    def dut(self):
        return self.call("dut")

    def off(self):
        return self.call("off")

    def write(self, filepath):
        with self.local_file(filepath) as handle:
            return self.call("write", handle)


class DutlinkClient(CompositeClient):
    pass
