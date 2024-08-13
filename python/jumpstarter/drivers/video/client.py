from base64 import b64decode

from jumpstarter.drivers import DriverClient
from jumpstarter.drivers.mixins import StreamMixin

from .common import UStreamerState


class UStreamerClient(DriverClient, StreamMixin):
    def state(self):
        """
        Get state of ustreamer service
        """

        return UStreamerState.model_validate(self.call("state"))

    def snapshot(self):
        return b64decode(self.call("snapshot"))
