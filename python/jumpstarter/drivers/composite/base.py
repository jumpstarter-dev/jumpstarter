from jumpstarter.drivers import Driver, DriverClient
from collections.abc import Iterator
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID
from abc import ABC, abstractmethod


class CompositeInterface(ABC):
    @abstractmethod
    def __getitem__(self, key: UUID) -> Driver: ...

    @abstractmethod
    def __iter__(self) -> Iterator[UUID]: ...


@dataclass(kw_only=True)
class Composite(Driver, CompositeInterface):
    childs: OrderedDict[UUID, Driver]

    def __getitem__(self, key: UUID) -> Driver:
        return self.childs.__getitem__(key)

    def __iter__(self) -> Iterator[UUID]:
        return self.childs.__iter__()


@dataclass(kw_only=True)
class CompositeClient(DriverClient, CompositeInterface):
    childs: OrderedDict[UUID, DriverClient]

    def __getitem__(self, key: UUID) -> DriverClient:
        return self.childs.__getitem__(key)

    def __iter__(self) -> Iterator[UUID]:
        return self.childs.__iter__()
