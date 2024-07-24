from abc import ABCMeta, abstractmethod
from dataclasses import InitVar, dataclass, field
from uuid import UUID, uuid4


@dataclass(kw_only=True, slots=True)
class Metadata:
    uuid: UUID = field(default_factory=uuid4)
    labels: dict[str, str] = field(default_factory=dict)

    name: InitVar[str | None] = None

    def __post_init__(self, name):
        if name is not None:
            self.labels["jumpstarter.dev/name"] = name

        if "jumpstarter.dev/name" not in self.labels:
            raise ValueError("missing required label: jumpstarter.dev/name")


@dataclass(kw_only=True, slots=True)
class MetadataFilter:
    labels: dict[str, str] = field(default_factory=dict)

    name: InitVar[str | None] = None

    def __post_init__(self, name):
        if name is not None:
            self.labels["jumpstarter.dev/name"] = name


class Interface(metaclass=ABCMeta):
    @classmethod
    @abstractmethod
    def client_module(cls) -> str:
        """Return module name of the driver client"""

    @classmethod
    @abstractmethod
    def client_class(cls) -> str:
        """Return class name of the driver client"""
