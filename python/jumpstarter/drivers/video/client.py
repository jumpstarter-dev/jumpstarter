from base64 import b64decode
from dataclasses import dataclass

from jumpstarter.drivers import DriverClient
from jumpstarter.drivers.mixins import StreamMixin


@dataclass(kw_only=True)
class State:
    encoder: str
    """type of encoder in use, e.g. CPU/GPU"""
    quality: int
    """encoding quality"""
    width: int
    """resolution width"""
    height: int
    """resolution height"""
    online: bool
    """client active"""
    desired_fps: int
    """desired fps"""
    captured_fps: int
    """actual fps"""


class UStreamerClient(DriverClient, StreamMixin):
    def state(self):
        """
        Get state of ustreamer service
        """

        result = self.call("state")
        return State(
            encoder=result["result"]["encoder"]["type"],
            quality=result["result"]["encoder"]["quality"],
            width=result["result"]["source"]["resolution"]["width"],
            height=result["result"]["source"]["resolution"]["height"],
            online=result["result"]["source"]["online"],
            desired_fps=result["result"]["source"]["desired_fps"],
            captured_fps=result["result"]["source"]["captured_fps"],
        )

    def snapshot(self):
        return b64decode(self.call("snapshot"))
