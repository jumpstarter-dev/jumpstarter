# This file contains the base class for all jumpstarter drivers
from google.protobuf import struct_pb2, json_format
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc
from dataclasses import dataclass, asdict, is_dataclass
from uuid import UUID, uuid4
from typing import Any, BinaryIO
from dataclasses import field
from jumpstarter.common import Metadata
from contextvars import ContextVar
from abc import ABC, abstractmethod


ContextStore = ContextVar("store")


@dataclass(kw_only=True)
class Store:
    fds: list[BinaryIO] = field(default_factory=list, init=False)
    conns: dict[UUID, Any] = field(default_factory=dict, init=False)


@dataclass(kw_only=True)
class Driver(
    ABC,
    Metadata,
    jumpstarter_pb2_grpc.ExporterServiceServicer,
    router_pb2_grpc.RouterServiceServicer,
):
    @classmethod
    @abstractmethod
    def class_labels(cls): ...

    async def DriverCall(self, request, context):
        method = getattr(self, request.method)

        if not getattr(method, "is_drivercall", False):
            raise ValueError

        return await method(request, context)

    async def StreamingDriverCall(self, request, context):
        method = getattr(self, request.method)

        if not getattr(method, "is_streamingdrivercall", False):
            raise ValueError

        async for v in method(request, context):
            yield v

    async def Stream(self, request_iterator, context):
        pass

    def Reports(self, parent=None) -> list[jumpstarter_pb2.DriverInstanceReport]:
        return [
            jumpstarter_pb2.DriverInstanceReport(
                uuid=str(self.uuid),
                parent_uuid=str(parent.uuid) if parent else None,
                labels=self.labels | self.class_labels(),
            )
        ]

    def add_to_server(self, server):
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)


@dataclass(kw_only=True)
class DriverClient(Metadata):
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    async def drivercall(self, method, *args):
        return json_format.MessageToDict(
            (
                await self.stub.DriverCall(
                    jumpstarter_pb2.DriverCallRequest(
                        uuid=str(self.uuid),
                        method=method,
                        args=[
                            json_format.ParseDict(arg, struct_pb2.Value())
                            for arg in args
                        ],
                    )
                )
            ).result
        )

    async def streamingdrivercall(self, method, *args):
        async for v in self.stub.StreamingDriverCall(
            jumpstarter_pb2.StreamingDriverCallRequest(
                uuid=str(self.uuid),
                method=method,
                args=[json_format.ParseDict(arg, struct_pb2.Value()) for arg in args],
            )
        ):
            yield json_format.MessageToDict(v.result)


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
# @dataclass(kw_only=True)
# class DriverBase(ABC, Metadata, jumpstarter_pb2_grpc.ExporterServiceServicer):
#     def mapping(self) -> dict[UUID, "DriverBase"]:
#         return {self.uuid: self}
#
#     def reports(self, parent=None) -> List[jumpstarter_pb2.DriverInstanceReport]:
#         return [
#             jumpstarter_pb2.DriverInstanceReport(
#                 uuid=str(self.uuid),
#                 parent_uuid=str(parent.uuid) if parent else None,
#                 labels=self.labels | {"jumpstarter.dev/interface": self.interface},
#             )
#         ]
