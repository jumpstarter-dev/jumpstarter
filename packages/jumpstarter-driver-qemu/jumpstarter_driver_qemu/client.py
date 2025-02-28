from jumpstarter_driver_composite.client import CompositeClient


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
