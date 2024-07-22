from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import chain
from uuid import UUID

from jumpstarter.drivers import Driver, DriverClient


class CompositeInterface(metaclass=ABCMeta):
    @classmethod
    def interface(cls) -> str:
        return "composite"

    @classmethod
    def version(cls) -> str:
        return "0.0.1"

    @abstractmethod
    def __getitem__(self, key: UUID) -> Driver: ...

    @abstractmethod
    def __iter__(self) -> Iterator[UUID]: ...


@dataclass(kw_only=True)
class Composite(CompositeInterface, Driver):
    childs: OrderedDict[UUID, Driver]

    def items(self, parent=None):
        return super().items(parent) + list(chain(*[child.items(self) for child in self.childs.values()]))

    def __init__(self, *args, childs: list[Driver], **kwargs):
        super().__init__(*args, **kwargs)
        self.childs = OrderedDict([(v.uuid, v) for v in childs])

    def __getitem__(self, key: UUID) -> Driver:
        try:
            return self.childs.__getitem__(key)
        except KeyError:
            for child in self.childs.values():
                try:
                    return child.__getitem__(key)
                except AttributeError:
                    pass
                except KeyError:
                    pass
        raise KeyError(UUID)

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
class CompositeClient(CompositeInterface, DriverClient):
    childs: OrderedDict[UUID, DriverClient] = field(init=False, default_factory=OrderedDict)

    def __getitem__(self, key: UUID) -> DriverClient:
        return self.childs.__getitem__(key)

    def __setitem__(self, key: UUID, value: DriverClient):
        self.childs.__setitem__(key, value)

        setattr(self, value.labels["jumpstarter.dev/name"], value)

    def __iter__(self) -> Iterator[UUID]:
        return self.childs.__iter__()
