from dataclasses import dataclass, field
from itertools import chain

import click

from jumpstarter.client import DriverClient
from jumpstarter.driver import Driver


class CompositeInterface:
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.composite.CompositeClient"


@dataclass(kw_only=True)
class Composite(CompositeInterface, Driver):
    children: list[Driver]

    def items(self, parent=None):
        return super().items(parent) + list(chain(*[child.items(self) for child in self.children]))


@dataclass(kw_only=True)
class CompositeClient(DriverClient):
    children: list[DriverClient] = field(init=False, default_factory=list)

    def __or__(self, other: DriverClient):
        name = other.labels["jumpstarter.dev/name"]
        setattr(self, name, other)

        self.children.append(other)

        return self

    def cli(self):
        @click.group
        def base():
            """Generic composite device"""
            pass

        for child in self.children:
            if hasattr(child, "cli"):
                base.add_command(child.cli(), child.labels["jumpstarter.dev/name"])

        return base
