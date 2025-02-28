from contextlib import contextmanager

from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import FabricAdapter, NovncAdapter


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

    @contextmanager
    def shell(self):
        with FabricAdapter(
            client=self.ssh,
            user="jumpstarter",
            connect_kwargs={"password": "password"},
        ) as conn:
            yield conn
