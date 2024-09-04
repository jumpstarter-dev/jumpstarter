from base64 import b64decode

from jumpstarter.client import DriverClient

from .common import UStreamerState


class UStreamerClient(DriverClient):
    def state(self):
        """
        Get state of ustreamer service
        """

        return UStreamerState.model_validate(self.call("state"))

    def snapshot(self):
        return b64decode(self.call("snapshot"))
