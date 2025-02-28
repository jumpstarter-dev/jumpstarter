from contextlib import contextmanager

from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import NovncAdapter


class QemuClient(CompositeClient):
    @property
    def image(self) -> str:
        return self.call("get_image")

    @image.setter
    def image(self, path: str) -> None:
        self.call("set_image", path)

    def start(self) -> None:
        self.call("start")

    def stop(self) -> None:
        self.call("stop")

    @contextmanager
    def novnc(self):
        with NovncAdapter(client=self.vnc) as url:
            yield url
