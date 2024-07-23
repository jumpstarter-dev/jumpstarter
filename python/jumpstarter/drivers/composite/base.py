from abc import ABCMeta
from dataclasses import dataclass
from itertools import chain

from jumpstarter.drivers import Driver, DriverClient


class CompositeInterface(metaclass=ABCMeta):
    @classmethod
    def client_module(cls) -> str:
        return "jumpstarter.drivers.composite"

    @classmethod
    def client_class(cls) -> str:
        return "CompositeClient"


@dataclass(kw_only=True)
class Composite(CompositeInterface, Driver):
    children: list[Driver]

    def items(self, parent=None):
        return super().items(parent) + list(chain(*[child.items(self) for child in self.children]))


@dataclass(kw_only=True)
class CompositeClient(CompositeInterface, DriverClient):
    def __or__(self, other: DriverClient):
        setattr(self, other.labels["jumpstarter.dev/name"], other)

        return self
