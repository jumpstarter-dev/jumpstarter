from abc import ABCMeta
from dataclasses import dataclass
from itertools import chain

from jumpstarter.drivers import Driver, DriverClient


class CompositeInterface(metaclass=ABCMeta):
    @classmethod
    def interface(cls) -> str:
        return "composite"

    @classmethod
    def version(cls) -> str:
        return "0.0.1"


@dataclass(kw_only=True)
class Composite(CompositeInterface, Driver):
    childs: list[Driver]

    def items(self, parent=None):
        return super().items(parent) + list(chain(*[child.items(self) for child in self.childs]))


@dataclass(kw_only=True)
class CompositeClient(CompositeInterface, DriverClient):
    def __or__(self, other: DriverClient):
        setattr(self, other.labels["jumpstarter.dev/name"], other)

        return self
