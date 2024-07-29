from base64 import b64decode

from jumpstarter.drivers import DriverClient
from jumpstarter.drivers.mixins import StreamMixin


class UStreamerClient(DriverClient, StreamMixin):
    def state(self):
        return self.call("state")

    def snapshot(self):
        return b64decode(self.call("snapshot"))
