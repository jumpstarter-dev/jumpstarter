from jumpstarter.driver import Driver
from pydantic.dataclasses import dataclass


class CompositeInterface:
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_composite.client.CompositeClient"


@dataclass(kw_only=True)
class Composite(CompositeInterface, Driver):
    pass
