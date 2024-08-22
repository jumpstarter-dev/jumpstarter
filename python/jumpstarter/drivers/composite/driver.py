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

    def __post_init__(self, *args):
        super().__post_init__(*args)

        for child in self.children:
            child.parent = self

    def items(self):
        return super().items() + list(chain(*[child.items() for child in self.children]))
