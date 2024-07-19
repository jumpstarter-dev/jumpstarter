# This file contains the base class for all jumpstarter drivers
from google.protobuf import struct_pb2, json_format
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from dataclasses import dataclass, asdict, is_dataclass
from abc import ABC
from uuid import UUID, uuid4
from typing import List, Any, BinaryIO, Dict
from dataclasses import field
from collections.abc import Generator
from jumpstarter.common import Metadata
from contextvars import ContextVar
from .registry import _registry
import inspect


ContextStore = ContextVar("store")


@dataclass(kw_only=True)
class Store:
    fds: List[BinaryIO] = field(default_factory=list, init=False)
    conns: Dict[UUID, Any] = field(default_factory=dict, init=False)


@dataclass(kw_only=True)
class Driver(Metadata, jumpstarter_pb2_grpc.ExporterServiceServicer):
    async def DriverCall(self, request, context):
        pass

    async def StreamingDriverCall(self, request, context):
        pass

    async def Stream(self, request_iterator, context):
        pass


@dataclass(kw_only=True)
class DriverClient(Metadata):
    pass


def drivercall(func):
    async def DriverCall(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]

        result = await func(self, *args)

        return jumpstarter_pb2.DriverCallResponse(
            uuid=str(uuid4()),
            result=json_format.ParseDict(
                asdict(result) if is_dataclass(result) else result, struct_pb2.Value()
            ),
        )

    DriverCall.is_drivercall = True

    return DriverCall


def streamingdrivercall(func):
    async def StreamingDriverCall(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]

        async for result in func(self, *args):
            yield jumpstarter_pb2.StreamingDriverCallResponse(
                uuid=str(uuid4()),
                result=json_format.ParseDict(
                    asdict(result) if is_dataclass(result) else result,
                    struct_pb2.Value(),
                ),
            )

    StreamingDriverCall.is_streamingdrivercall = True

    return StreamingDriverCall


# base class for all drivers
@dataclass(kw_only=True)
class DriverBase(ABC, Metadata, jumpstarter_pb2_grpc.ExporterServiceServicer):
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

    async def DriverCall(self, request, context):
        assert UUID(request.uuid) == self.uuid
        args = [json_format.MessageToDict(arg) for arg in request.args]
        result = await self.call(request.method, args)
        return jumpstarter_pb2.DriverCallResponse(
            uuid=str(uuid4()),
            result=json_format.ParseDict(
                asdict(result) if is_dataclass(result) else result, struct_pb2.Value()
            ),
        )

    async def StreamingDriverCall(self, request, context):
        assert UUID(request.uuid) == self.uuid
        args = [json_format.MessageToDict(arg) for arg in request.args]
        async for result in self.streaming_call(request.method, args):
            yield jumpstarter_pb2.StreamingDriverCallResponse(
                uuid=str(uuid4()),
                result=json_format.ParseDict(
                    asdict(result) if is_dataclass(result) else result,
                    struct_pb2.Value(),
                ),
            )

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

    def mapping(self) -> dict[UUID, "DriverBase"]:
        return {self.uuid: self}

    def reports(self, parent=None) -> List[jumpstarter_pb2.DriverInstanceReport]:
        return [
            jumpstarter_pb2.DriverInstanceReport(
                uuid=str(self.uuid),
                parent_uuid=str(parent.uuid) if parent else None,
                labels=self.labels | {"jumpstarter.dev/interface": self.interface},
            )
        ]
