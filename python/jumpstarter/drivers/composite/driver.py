from pydantic.dataclasses import dataclass

from jumpstarter.driver import Driver


class CompositeInterface:
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.composite.client.CompositeClient"


@dataclass(kw_only=True)
class Composite(CompositeInterface, Driver):
    pass
