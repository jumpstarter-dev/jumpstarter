from functools import reduce

from pydantic.dataclasses import dataclass

from jumpstarter.common.exceptions import ConfigurationError
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

    def __target(self, root, name):
        try:
            return reduce(lambda instance, name: instance.children[name], self.path, root)
        except KeyError:
            raise ConfigurationError(
                f"Proxy driver {name} references nonexistent driver {'.'.join(self.path)}"
            ) from None

    def report(self, *, root=None, parent=None, name=None):
        return self.__target(root, name).report(root=root, parent=parent, name=name)

    def enumerate(self, *, root=None, parent=None, name=None):
        return self.__target(root, name).enumerate(root=root, parent=parent, name=name)
