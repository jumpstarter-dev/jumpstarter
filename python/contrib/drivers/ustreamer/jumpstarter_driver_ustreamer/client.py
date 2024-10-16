import io
import logging
from base64 import b64decode

from PIL import Image

from jumpstarter.client import DriverClient

from .common import UStreamerState

log = logging.getLogger("ustreamer")

class UStreamerClient(DriverClient):
    """UStreamer client class

    Client methods for the UStreamer driver.
    """

    def state(self):
        """
        Get state of ustreamer service
        """

        return UStreamerState.model_validate(self.call("state"))

    def snapshot(self):
        """
        Get a snapshot image from the video input

        :return: PIL Image object of the snapshot image
        :rtype: PIL.Image
        """
        input_jpg_data = b64decode(self.call("snapshot"))
        return Image.open(io.BytesIO(input_jpg_data))
