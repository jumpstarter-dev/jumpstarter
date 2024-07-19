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

    async def DriverCall(self, request, context):
        # TODO: search for nested
        return await self[UUID(request.uuid)].DriverCall(request, context)

    async def StreamingDriverCall(self, request, context):
        # TODO: search for nested
        async for v in self[UUID(request.uuid)].StreamingDriverCall(request, context):
            yield v

    async def Stream(self, request_iterator, context):
        pass


@dataclass(kw_only=True)
class CompositeClient(DriverClient, CompositeInterface):
    childs: OrderedDict[UUID, DriverClient]

    def __getitem__(self, key: UUID) -> DriverClient:
        return self.childs.__getitem__(key)

    def __iter__(self) -> Iterator[UUID]:
        return self.childs.__iter__()

    def __post_init__(self):
        for child in self.childs.values():
            setattr(self, child.labels["jumpstarter.dev/name"], child)
