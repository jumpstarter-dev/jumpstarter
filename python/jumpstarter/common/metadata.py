from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(kw_only=True, slots=True)
class Metadata:
    uuid: UUID = field(default_factory=uuid4)
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if "jumpstarter.dev/name" not in self.labels:
            raise ValueError("missing required label: jumpstarter.dev/name")


@dataclass(kw_only=True, slots=True)
class MetadataFilter:
    labels: dict[str, str] = field(default_factory=dict)


class Interface(metaclass=ABCMeta):
    @classmethod
    @abstractmethod
    def interface(cls) -> str:
        """Return interface name of the driver/client

        Names should be globally unique thus should
        be namespaced like `example.com/foo`.
        """

    @classmethod
    @abstractmethod
    def version(cls) -> str:
        """Return interface version of the driver/client

        Versions are matched exactly and don't have
        to follow semantic versioning.
        """
