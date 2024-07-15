# This file contains the base class for all jumpstarter drivers
from abc import ABC
from uuid import UUID
from typing import List, Any, BinaryIO, Dict
from dataclasses import dataclass, field
from collections.abc import Generator
from jumpstarter.v1 import jumpstarter_pb2
from jumpstarter.common import Metadata
from .registry import _registry
import inspect


@dataclass(kw_only=True)
class Session:
    fds: List[BinaryIO] = field(default_factory=list, init=False)
    conns: Dict[UUID, Any] = field(default_factory=dict, init=False)


# base class for all drivers
@dataclass(kw_only=True)
class DriverBase(ABC, Metadata):
    session: Session

    def __init_subclass__(cls, interface=None, **kwargs):
        if interface:
            cls.interface = interface
            _registry.register(cls)

        cls.callables = dict()
        cls.generator = dict()

        for name in inspect.getattr_static(cls, "__abstractmethods__"):
            attr = inspect.getattr_static(cls, name)
            if callable(attr):
                if inspect.isasyncgenfunction(attr):
                    cls.generator[name] = attr
                else:
                    cls.callables[name] = attr
            elif isinstance(attr, property):
                cls.callables["__get__" + name] = attr.__get__
                cls.callables["__set__" + name] = attr.__set__
            else:
                raise NotImplementedError("unrecognized abstract method")

        super().__init_subclass__(**kwargs)

    async def call(self, method: str, args: List[Any]) -> Any:
        function = self.callables.get(method)

        if not function:
            raise NotImplementedError("no such drivercall")

        if inspect.iscoroutinefunction(function):
            return await function(self, *args)
        else:
            return function(self, *args)

    async def streaming_call(
        self, method: str, args: List[Any]
    ) -> Generator[Any, None, None]:
        function = self.generator.get(method)

        if not function:
            raise NotImplementedError("no such streaming drivercall")

        async for v in function(self, *args):
            yield v

    def reports(self, parent=None) -> List[jumpstarter_pb2.DeviceReport]:
        return [
            jumpstarter_pb2.DeviceReport(
                device_uuid=str(self.uuid),
                parent_device_uuid=str(parent.uuid) if parent else None,
                driver_interface=self.interface,
                labels=self.labels,
            )
        ]
