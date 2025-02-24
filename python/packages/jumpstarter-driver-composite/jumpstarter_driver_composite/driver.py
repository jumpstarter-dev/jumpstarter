from functools import reduce

from pydantic.dataclasses import dataclass

from jumpstarter.driver import Driver


class CompositeInterface:
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_composite.client.CompositeClient"


@dataclass(kw_only=True)
class Composite(CompositeInterface, Driver):
    pass


@dataclass(kw_only=True)
class Proxy(Driver):
    path: list[str]

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"  # unused

    def __target(self, root):
        return reduce(lambda instance, name: instance.children[name], self.path, root)

    def report(self, *, root=None, parent=None, name=None):
        return self.__target(root).report(root=root, parent=parent, name=name)

    def enumerate(self, *, root=None, parent=None, name=None):
        return self.__target(root).enumerate(root=root, parent=parent, name=name)
