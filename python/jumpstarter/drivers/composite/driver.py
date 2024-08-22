from dataclasses import dataclass
from itertools import chain

from jumpstarter.driver import Driver


class CompositeInterface:
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.composite.client.CompositeClient"


@dataclass(kw_only=True)
class Composite(CompositeInterface, Driver):
    children: list[Driver]

    def items(self, parent=None):
        return super().items(parent) + list(chain(*[child.items(self) for child in self.children]))
